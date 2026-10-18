"""Resolved v2 worker contracts. Paths are bound only by the worker adapter."""
from typing import Annotated, Literal

from pydantic import Field, model_validator

from .benchmarks import BenchmarkSpec, EvaluationRecord, SelectionSpec
from .config import GraphTrainingConfig, StrictModel
from .executor_profiles import ExecutorProfile, ResourceFacts
from .model_artifacts import ModelArtifactRef, ModelInput
from .projects import DatasetRef, LocalId, Sha256


class RuntimeProfile(StrictModel):
    profile: Literal["primary", "ppo"]
    engine_version: Literal["0.9.5", "0.9.3"]
    dependency_sha256: Sha256

    @model_validator(mode="after")
    def pinned_engine(self):
        if self.engine_version != ("0.9.3" if self.profile == "ppo" else "0.9.5"):
            raise ValueError("runtime profile/engine mismatch")
        return self


class OperationResources(StrictModel):
    executor: ExecutorProfile
    facts: ResourceFacts
    timeout_seconds: int = Field(strict=True, ge=1, le=604800)
    runtime: RuntimeProfile

    @model_validator(mode="after")
    def same_pod(self):
        if self.executor.pod_id != self.facts.pod_id:
            raise ValueError("executor/resource Pod mismatch")
        return self


class OperationLineage(StrictModel):
    project_id: LocalId
    project_revision: Sha256
    node_id: LocalId
    plan_sha256: Sha256
    simulation: bool = Field(default=True, strict=True)


class _Operation(StrictModel):
    resources: OperationResources
    lineage: OperationLineage


class TrainOperation(_Operation):
    kind: Literal["train"] = "train"
    config: GraphTrainingConfig
    model_input: ModelInput
    dataset: DatasetRef

    @model_validator(mode="after")
    def resolved_training(self):
        if (self.dataset.dataset_id, self.dataset.version) != (self.config.dataset_id, self.config.dataset_version):
            raise ValueError("training dataset differs from resolved config")
        if self.config.resource_profile != self.resources.facts.legacy_resources(self.resources.timeout_seconds):
            raise ValueError("training resources differ from pinned Pod budget")
        expected = "ppo" if self.config.training_stage == "ppo" else "primary"
        if self.resources.runtime.profile != expected:
            raise ValueError("training runtime mismatch")
        reward = self.model_input.reward_model
        if (self.config.training_stage == "ppo") != (reward is not None):
            raise ValueError("PPO requires a typed reward_model binding; other stages cannot bind reward")
        if reward and reward.adapter and self.config.training_method not in {"lora", "qlora"}:
            raise ValueError("LoRA reward models require a LoRA/QLoRA PPO policy")
        return self


class EvaluateOperation(_Operation):
    kind: Literal["evaluate"] = "evaluate"
    model: ModelArtifactRef
    benchmark: BenchmarkSpec


class MergeOperation(_Operation):
    kind: Literal["merge"] = "merge"
    models: tuple[ModelArtifactRef, ...] = Field(min_length=2, max_length=4)
    weights: tuple[float, ...]
    method: Literal["cat"] = "cat"

    @model_validator(mode="after")
    def merge_contract(self):
        from .graph_schema import MergeNode
        MergeNode(id="merge", label="merge", models=self.models, weights=self.weights,
                  executor_profile_ids=(self.resources.executor.profile_id,))
        return self


class MaterializeOperation(_Operation):
    kind: Literal["materialize"] = "materialize"
    model: ModelArtifactRef


OperationSpec = Annotated[TrainOperation | EvaluateOperation | MergeOperation | MaterializeOperation,
                          Field(discriminator="kind")]


class LocalSelectSpec(StrictModel):
    kind: Literal["select"] = "select"
    lineage: OperationLineage
    evaluations: tuple[EvaluationRecord, ...]
    requested_models: tuple[ModelArtifactRef, ...]
    failed_parent_node_ids: tuple[LocalId, ...] = ()
    selection: SelectionSpec
    benchmark_sha256: Sha256
    merged_models: tuple[ModelArtifactRef, ...] = ()


def resolve_local_selection(spec: LocalSelectSpec):
    """Compute a local receipt, preserving symbolic failures and merge fallback."""
    from .benchmarks import finalize_with_merge, select_best
    merged = set((ref.artifact_id, ref.inventory_sha256) for ref in spec.merged_models)
    originals = tuple(record for record in spec.evaluations
                      if (record.model.artifact_id, record.model.inventory_sha256) not in merged)
    requested = tuple(ref for ref in spec.requested_models if (ref.artifact_id, ref.inventory_sha256) not in merged)
    receipt = select_best(originals, spec.selection, requested_models=requested)
    if spec.failed_parent_node_ids and (not spec.selection.allow_partial or len(receipt.eligible) < spec.selection.min_success):
        return receipt.model_copy(update={"winner": None, "status": "no_winner",
                                           "reasons": ("insufficient_partial_success",)})
    if not merged or receipt.winner is None:
        return receipt
    if len(merged) != 1:
        raise ValueError("merge finalization requires one explicit merged candidate")
    original = next(item for item in originals if item.model == receipt.winner)
    merged_record = next(item for item in spec.evaluations if item.model in spec.merged_models)
    return finalize_with_merge(original, merged_record, spec.selection)
