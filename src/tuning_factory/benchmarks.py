"""Pure comparison of immutable model evaluation evidence under explicit objectives.

Controllers must pass every known artifact candidate in ``requested_models`` and
gate graph parents separately. A parent that failed before producing an artifact
has no invented model reference: its symbolic outcome belongs to the graph receipt.
Without ``requested_models``, records define the complete requested set; pending
or running placeholders keep that set open. Test-set results belong in separate
records and must never be appended to the selection benchmark.
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from fractions import Fraction
from types import MappingProxyType
from typing import Any, Literal

from pydantic import ConfigDict, Field, field_serializer, field_validator, model_validator

from .config import StrictModel
from .evaluation import evaluator_source_sha256, metric_schema
from .model_artifacts import ModelArtifactRef
from .projects import DatasetRef, LocalId, Sha256
from .registry import canonical, digest

Direction = Literal["max", "min"]


class BenchmarkSpec(StrictModel):
    dataset: DatasetRef
    evaluator_kind: str
    evaluator_version: str = Field(min_length=1)
    ordered_sample_ids: tuple[str, ...] = Field(min_length=1)
    max_new_tokens: int = Field(strict=True, gt=0)
    cutoff_len: int = Field(strict=True, gt=0)
    seed: int = Field(strict=True)
    generation_mode: Literal["greedy", "sample"] = "greedy"
    precision: Literal["fp16", "bf16", "fp32"]
    quantization_bit: Literal[4, 8] | None
    tokenizer_fingerprint: Sha256
    template: str = Field(min_length=1)
    semantic_model_kind: Literal["causal_lm", "reward_model"]

    @model_validator(mode="after")
    def valid_conditions(self):
        metric_schema(self.evaluator_kind)
        if any(not value for value in self.ordered_sample_ids):
            raise ValueError("sample IDs must be nonempty")
        if len(set(self.ordered_sample_ids)) != len(self.ordered_sample_ids):
            raise ValueError("duplicate sample IDs")
        return self

    def fingerprint(self):
        return digest(canonical({"spec": self.model_dump(mode="json"),
                                 "evaluator_source_sha256": evaluator_source_sha256(self.evaluator_kind)}))


class MetricThreshold(StrictModel):
    metric: str = Field(min_length=1)
    direction: Direction
    value: float = Field(strict=True)


class SelectionSpec(StrictModel):
    metric: str = Field(min_length=1)
    direction: Direction
    thresholds: tuple[MetricThreshold, ...] = ()
    allow_partial: bool = Field(default=False, strict=True)
    min_success: int = Field(default=2, strict=True, ge=2)
    merge_regression_tolerance: float = Field(default=0, strict=True, ge=0)


class EvaluationRecord(StrictModel):
    """Immutable raw evidence; invalid scalar metrics are retained for exclusion."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=True, revalidate_instances="always")
    model: ModelArtifactRef
    benchmark_sha256: Sha256
    metrics: Mapping[str, Any]
    status: Literal["pending", "running", "completed", "failed", "cancelled", "skipped", "unevaluated"]
    synthetic: bool = Field(strict=True)
    simulation: bool = Field(strict=True)
    producer_attempt_id: LocalId | None

    @field_validator("metrics")
    @classmethod
    def freeze_metrics(cls, values):
        if any(type(value) not in {float, int, str, bool, type(None)} for value in values.values()):
            raise ValueError("metric evidence must contain scalar values")
        return MappingProxyType(dict(values))

    @field_serializer("metrics")
    def serialize_metrics(self, values):
        # JSON has no NaN/Infinity; preserve those invalid observations explicitly.
        return {key: value if type(value) is not float or math.isfinite(value)
                else {"nonfinite": repr(value)} for key, value in values.items()}

    @field_validator("metrics", mode="before")
    @classmethod
    def restore_nonfinite(cls, values):
        if not isinstance(values, Mapping):
            raise ValueError("metrics must be an object")
        return {key: float(value["nonfinite"])
                if isinstance(value, dict) and set(value) == {"nonfinite"}
                and value["nonfinite"] in {"nan", "inf", "-inf"} else value
                for key, value in values.items()}


class CandidateExclusion(StrictModel):
    model: ModelArtifactRef
    record: EvaluationRecord | None
    reasons: tuple[str, ...]


class SelectionResult(StrictModel):
    """A frozen, JSON-serializable receipt containing scores and exclusion evidence."""

    winner: ModelArtifactRef | None
    eligible: tuple[EvaluationRecord, ...]
    excluded: tuple[CandidateExclusion, ...]
    reasons: tuple[str, ...]
    status: Literal["waiting", "selected", "no_winner"]
    selection: SelectionSpec
    benchmark_sha256: Sha256 | None
    requested_models: tuple[ModelArtifactRef, ...]
    observed_models: tuple[ModelArtifactRef, ...]


def _key(model):
    return model.artifact_id, model.inventory_sha256


def _excluded_reasons(record, selection):
    reasons = []
    if record.status != "completed":
        reasons.append(record.status)
    if record.synthetic:
        reasons.append("synthetic")
    if record.simulation:
        reasons.append("simulation")
    if record.producer_attempt_id is None:
        reasons.append("missing_producer_attempt")
    required = {selection.metric, *(threshold.metric for threshold in selection.thresholds)}
    if required - record.metrics.keys():
        reasons.append("missing_metric")
    for value in record.metrics.values():
        if type(value) not in {float, int}:
            reasons.append("invalid_metric")
        elif type(value) is float and not math.isfinite(value):
            reasons.append("nonfinite_metric")
    if not reasons:
        for threshold in selection.thresholds:
            value = record.metrics[threshold.metric]
            if (threshold.direction == "max" and value < threshold.value
                    or threshold.direction == "min" and value > threshold.value):
                reasons.append(f"threshold_failed:{threshold.metric}")
    return tuple(dict.fromkeys(reasons))


def select_best(records, selection: SelectionSpec, *, requested_models=None) -> SelectionResult:
    """Wait for the complete set by default; compare only real, finite evidence.

    Terminal failures finish waiting but cannot win. Explicit partial selection
    needs ``min_success`` eligible evaluations when any requested candidate is
    missing, active, or excluded. A complete successful one-model set can select.
    Duplicate evidence and mixed benchmarks are plan errors, never ranked.
    """
    records = tuple(EvaluationRecord.model_validate(record) for record in records)
    selection = SelectionSpec.model_validate(selection)
    observed = tuple(sorted((record.model for record in records), key=_key))
    requested = observed if requested_models is None else tuple(sorted(
        (ModelArtifactRef.model_validate(model) for model in requested_models), key=_key))
    if len({_key(model) for model in observed}) != len(observed):
        raise ValueError("duplicate candidate records")
    if len({_key(model) for model in requested}) != len(requested):
        raise ValueError("duplicate requested candidates")
    if {_key(model) for model in observed} - {_key(model) for model in requested}:
        raise ValueError("unrequested candidate record")
    hashes = {record.benchmark_sha256 for record in records}
    if len(hashes) > 1:
        raise ValueError("candidates must use the same benchmark")
    by_model = {_key(record.model): record for record in records}
    eligible, excluded = [], []
    active = False
    for model in requested:
        record = by_model.get(_key(model))
        reasons = ("missing_record",) if record is None else _excluded_reasons(record, selection)
        active |= record is None or record.status in {"pending", "running"}
        if reasons:
            excluded.append(CandidateExclusion(model=model, record=record, reasons=reasons))
        else:
            eligible.append(record)
    winner, reasons = None, ()
    status = "no_winner"
    if active and not selection.allow_partial:
        status, reasons = "waiting", ("awaiting_requested_candidates",)
    elif selection.allow_partial and excluded and len(eligible) < selection.min_success:
        status = "waiting" if active else "no_winner"
        reasons = ("insufficient_partial_success",)
    elif eligible:
        winner = min(eligible, key=lambda record: (
            -record.metrics[selection.metric] if selection.direction == "max" else record.metrics[selection.metric],
            *_key(record.model))).model
        status = "selected"
        if excluded:
            reasons = ("partial_selection",) if selection.allow_partial else ("terminal_candidates_excluded",)
    else:
        reasons = ("no_eligible_candidates",)
    return SelectionResult(winner=winner, eligible=tuple(eligible), excluded=tuple(excluded), reasons=reasons,
                           status=status, selection=selection, benchmark_sha256=next(iter(hashes), None),
                           requested_models=requested, observed_models=observed)


def finalize_with_merge(best_original, merged_record, selection: SelectionSpec) -> SelectionResult:
    """Retain original on invalid/regressed merge; allowance applies directionally.

    Within a positive regression allowance, the merged artifact may be retained.
    Exact score ties still use artifact ID order. No service deployment occurs.
    """
    receipt = select_best((best_original, merged_record), selection)
    if receipt.status == "waiting":
        return receipt
    original = next((record for record in receipt.eligible if record.model == best_original.model), None)
    merged = next((record for record in receipt.eligible if record.model == merged_record.model), None)
    if original is None:
        return receipt.model_copy(update={"winner": None, "status": "no_winner",
                                          "reasons": (*receipt.reasons, "original_not_eligible")})
    if merged is None:
        return receipt.model_copy(update={"winner": original.model, "status": "selected",
                                          "reasons": (*receipt.reasons, "merged_not_eligible")})
    # Respect decimal JSON/config boundaries (e.g. 0.8 - 0.7 == tolerance 0.1)
    # without introducing an epsilon that could silently permit a real regression.
    difference = Fraction(str(merged.metrics[selection.metric])) - Fraction(str(original.metrics[selection.metric]))
    improvement = difference if selection.direction == "max" else -difference
    if improvement < -Fraction(str(selection.merge_regression_tolerance)):
        return receipt.model_copy(update={"winner": original.model, "status": "selected",
                                          "reasons": (*receipt.reasons, "merge_regression")})
    winner = min((original.model, merged.model), key=_key) if difference == 0 else merged.model
    return receipt.model_copy(update={"winner": winner, "status": "selected"})
