"""V2 operation runner: path-free model-input staging and worker-side training-plan binding.

Control plane: ``stage_model_inputs`` verifies every model role of a resolved operation against the
approved artifact cache and writes ``model-inputs.json`` (identities only; never weights or host paths).

Worker: ``verify_model_paths`` re-derives the roles from the operation and the hash-verified model cache,
resolves HF roots into link-free local snapshots and repeats every rule. ``bind_training_plan`` replaces
only the allowlisted role sentinels of the logical LLaMA-Factory template with those verified paths,
inside cache roots that the worker passes from its own configuration.

One ``model_artifacts.ArtifactVerifier`` per public call hashes each distinct artifact once: lineage-aware
on the control plane, entry-only on the worker (whose cache holds only the bound roles).

Every model directory produced or accepted here is link-free: no symlink or reparse point on the path, on
its ancestors or inside the tree, because the model readers refuse links. HF roots are therefore
downloaded with ``snapshot_download(local_dir=...)`` (real files), never bound as hub-cache snapshots,
which are trees of symlinks. Fingerprints are computed on exactly the tree that is bound; the
``.cache/huggingface/`` download metadata is walked for links but never fingerprinted.

The producer stage of a parent adapter is ledger evidence, not artifact metadata, so the
SFT-to-preference rule is enforced at compilation and staging only; the worker cannot re-derive it.

Module import touches only the standard library, pydantic, PyYAML and local contracts: no graph_store or
jobs (the control-plane runner, 7.5) and no torch, transformers or huggingface_hub (imported lazily).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal

import yaml
from pydantic import Field, TypeAdapter, model_validator

from .config import StrictModel
from .model_artifacts import (
    TOKENIZER_FILES,
    ArtifactVerifier,
    BaseRef,
    HFRootRef,
    ModelArtifactRef,
    ModelArtifactStore,
    ModelDescriptor,
    check_link_free,
)
from .model_ops import (
    check_continuation,
    check_full_model_directory,
    local_config_sha256,
    local_tokenizer_fingerprint,
    validate_merge_inputs,
    verify_inspected_targets,
)
from .operations import MaterializeOperation, MergeOperation, OperationSpec, TrainOperation
from .projects import Sha256
from .registry import canonical, digest
from .training import ROLE_PREFIX, ROLE_SLOTS, check_model_input_contract

ModelRole = Literal["policy_base", "policy_adapter", "reference_base", "reference_adapter",
                    "reward_base", "reward_adapter", "operation_base", "operation_adapter"]
MODEL_INPUTS = "model-inputs.json"
HF_ROOTS = "factory-roots"   # under HF_HOME: one link-free local_dir per <owner>/<name>/<revision>
# Loader files only: no custom code (trust_remote_code is False), no pickled weights, no consolidated copies.
HF_ALLOW_PATTERNS = ("config.json", "generation_config.json", *sorted(TOKENIZER_FILES), "chat_templates/*",
                     "model.safetensors", "model.safetensors.index.json", "model-*.safetensors")
# TRL's ValueHead (Linear(hidden, 1)) as LLaMA-Factory loads it from the reward path.
VALUE_HEAD_TENSORS = frozenset({"v_head.summary.weight", "v_head.summary.bias"})
RELATIVE_PATHS = MappingProxyType({"dataset_dir": "./data", "output_dir": "./output"})
# Arguments that LLaMA-Factory 0.9.5 / HF TrainingArguments read as filesystem locations. Model slots hold
# verified absolute cache paths, RELATIVE_PATHS hold the attempt's own data/output, all others are refused.
PATH_KEYS = frozenset({
    *ROLE_SLOTS.values(), "adapter_folder", "cache_dir", "tokenized_path", "dataset_dir", "media_dir",
    "image_dir", "video_dir", "audio_dir", "reward_model_adapters", "export_dir", "export_quantization_dataset",
    "output_dir", "logging_dir", "resume_from_checkpoint", "deepspeed", "fsdp_config", "ray_storage_path",
})
OUTSIDE = "bound model path is outside the approved model caches"
_PATH_LIKE = re.compile(r"^(?:/|\\|~|\.{1,2}(?:[/\\]|$)|[A-Za-z]:[/\\])")   # catch-all for unknown keys
_WEIGHT_SUFFIXES = (".safetensors", ".bin", ".pt", ".pth", ".gguf")


# -- staged identities (control plane -> worker) -------------------------------------------------

class StagedModel(StrictModel):
    role: ModelRole
    index: int = Field(default=0, strict=True, ge=0, le=3)   # merge adapter position; 0 otherwise
    source: Literal["artifact", "hf_root"]
    artifact: ModelArtifactRef | None = None
    hf_root: HFRootRef | None = None
    storage_kind: Literal["adapter", "full_model"]
    config_sha256: Sha256
    tokenizer_fingerprint: Sha256

    @model_validator(mode="after")
    def one_identity(self):
        if (self.artifact is None) == (self.hf_root is None) or (self.source == "artifact") != (
                self.artifact is not None):
            raise ValueError("a staged model has exactly one identity matching its source")
        if self.hf_root is not None and (self.storage_kind, self.config_sha256, self.tokenizer_fingerprint) != (
                "full_model", self.hf_root.config_sha256, self.hf_root.tokenizer_fingerprint):
            raise ValueError("an HF root is a full model with the root's own fingerprints")
        if (self.storage_kind == "adapter") != self.role.endswith("_adapter"):
            raise ValueError("base roles hold full models and adapter roles hold adapters")
        if self.index and self.role != "operation_adapter":
            raise ValueError("only merge adapter roles carry an index")
        return self


class ResolvedModelInputs(StrictModel):
    schema_version: Literal[1] = 1
    operation_kind: Literal["train", "evaluate", "merge", "materialize"]
    operation_sha256: Sha256                     # digest(canonical(operation.model_dump(mode="json")))
    models: tuple[StagedModel, ...] = Field(min_length=1)
    transfers: tuple[ModelArtifactRef, ...]      # sorted unique artifacts the executor must cache remotely
    reference_quantization_bit: Literal[4, 8] | None = None
    reward_model_type: Literal["lora", "full"] | None = None

    @model_validator(mode="after")
    def consistent(self):
        roles = [(item.role, item.index) for item in self.models]
        if len(set(roles)) != len(roles):
            raise ValueError("duplicate staged model role")
        if self.transfers != _transfers(self.models):
            raise ValueError("transfers must be the sorted unique artifact roles")
        return self


def _key(ref: ModelArtifactRef):
    return ref.artifact_id, ref.inventory_sha256


def _transfers(models) -> tuple[ModelArtifactRef, ...]:
    unique = {_key(item.artifact): item.artifact for item in models if item.artifact is not None}
    return tuple(unique[key] for key in sorted(unique))


def operation_sha256(operation) -> str:
    return digest(canonical(operation.model_dump(mode="json")))


def _operation(operation):
    value = operation.model_dump(mode="json") if hasattr(operation, "model_dump") else operation
    return TypeAdapter(OperationSpec).validate_python(value)


# -- role derivation and rules shared by staging and the worker ----------------------------------

def _role_refs(operation, verifier: ArtifactVerifier) -> list[tuple[str, int, BaseRef]]:
    if isinstance(operation, TrainOperation):
        model_input, refs = operation.model_input, []
        for prefix, load in (("policy", model_input.policy), ("reference", model_input.frozen_reference),
                             ("reward", model_input.reward_model)):
            if load is not None:
                refs.append((f"{prefix}_base", 0, load.base))
                if load.adapter is not None:
                    refs.append((f"{prefix}_adapter", 0, load.adapter))
        return refs
    if isinstance(operation, MergeOperation):
        first = verifier.verify(operation.models[0])
        return [("operation_base", 0, first.effective_base),
                *(("operation_adapter", index, ref) for index, ref in enumerate(operation.models))]
    descriptor = verifier.verify(operation.model)
    if descriptor.storage_kind == "adapter":
        return [("operation_base", 0, descriptor.effective_base), ("operation_adapter", 0, operation.model)]
    if isinstance(operation, MaterializeOperation):
        raise ValueError("materialize requires a causal LoRA/QLoRA adapter without value head")
    return [("operation_base", 0, operation.model)]


def _staged(role: str, index: int, ref: BaseRef, verifier: ArtifactVerifier) -> StagedModel:
    if isinstance(ref, HFRootRef):
        return StagedModel(role=role, index=index, source="hf_root", hf_root=ref, storage_kind="full_model",
                           config_sha256=ref.config_sha256, tokenizer_fingerprint=ref.tokenizer_fingerprint)
    descriptor = verifier.verify(ref)
    return StagedModel(role=role, index=index, source="artifact", artifact=ref, storage_kind=descriptor.storage_kind,
                       config_sha256=descriptor.config_sha256, tokenizer_fingerprint=descriptor.tokenizer_fingerprint)


def _tokenizer(ref: BaseRef, verifier: ArtifactVerifier) -> str:
    return ref.tokenizer_fingerprint if isinstance(ref, HFRootRef) else verifier.verify(ref).tokenizer_fingerprint


def _training_rules(operation: TrainOperation, verifier: ArtifactVerifier):
    config, model_input = operation.config, operation.model_input
    check_model_input_contract(config, model_input)   # reward iff PPO, PPO continuation, initialization, reference
    # LLaMA-Factory loads the tokenizer from each base path it is given (plan F1).
    policy = _tokenizer(model_input.policy.base, verifier)
    for role, load in (("reference_base", model_input.frozen_reference), ("reward_base", model_input.reward_model)):
        if load is not None and _tokenizer(load.base, verifier) != policy:
            raise ValueError(f"{role} tokenizer differs from policy")
    # Exact loads, semantics, roots and (on the control plane) lineage, through the shared verifier.
    verifier.validate_input(model_input, require_real=not operation.lineage.simulation)
    reward, reward_type = model_input.reward_model, None
    if reward is not None:
        # A LoRA reward is loaded onto the policy's own base (plan F5).
        if reward.adapter is not None and reward.base != model_input.policy.base:
            raise ValueError("LoRA reward requires the exact policy effective base")
        head = {tensor.name for tensor in verifier.verify(reward.adapter or reward.base).tensors
                if tensor.file == "value_head.safetensors"}
        if not VALUE_HEAD_TENSORS <= head:
            raise ValueError("PPO reward requires reward_model semantics with a value_head.safetensors value head "
                             "(v_head.summary.weight and v_head.summary.bias)")
        reward_type = "lora" if reward.adapter is not None else "full"
    base = model_input.policy.base
    if (config.training_method == "qlora" and isinstance(base, ModelArtifactRef)
            and verifier.verify(base).quantization_bit is not None):
        raise ValueError("QLoRA requires an unquantized full effective base")
    reference_bit = (config.quantization_bit if model_input.frozen_reference is not None
                     and config.training_method == "qlora" else None)   # plan §6 Q5
    return reference_bit, reward_type


def _derive(operation, verifier: ArtifactVerifier) -> ResolvedModelInputs:
    """Roles and descriptor-level rules of one operation; identical on the control plane and the worker."""
    require_real = not operation.lineage.simulation
    reference_bit = reward_type = None
    if isinstance(operation, TrainOperation):
        reference_bit, reward_type = _training_rules(operation, verifier)
    models = tuple(_staged(role, index, ref, verifier) for role, index, ref in _role_refs(operation, verifier))
    roles = {(item.role, item.index): item for item in models}
    if require_real and any(not verifier.verify(item.artifact).eligible_for_real_use
                            for item in models if item.artifact is not None):
        raise ValueError("synthetic/simulation artifact is ineligible for real use")
    for item in models:
        base = roles.get((item.role.replace("_adapter", "_base"), 0))
        if item.storage_kind == "adapter" and base is not None and (
                item.config_sha256, item.tokenizer_fingerprint) != (base.config_sha256, base.tokenizer_fingerprint):
            raise ValueError("adapter/base config or tokenizer differs")
    if isinstance(operation, MergeOperation):
        validate_merge_inputs(operation.models, [verifier.verify(ref) for ref in operation.models],
                              operation.weights, require_real=require_real)
    elif isinstance(operation, MaterializeOperation):
        from .graph_planner import check_materializable
        check_materializable(verifier.verify(operation.model))
        base = roles[("operation_base", 0)]
        if base.artifact is not None and verifier.verify(base.artifact).quantization_bit is not None:
            raise ValueError("materialization requires unquantized effective base weights")
    return ResolvedModelInputs(operation_kind=operation.kind, operation_sha256=operation_sha256(operation),
                               models=models, transfers=_transfers(models),
                               reference_quantization_bit=reference_bit, reward_model_type=reward_type)


def _parent_stage(operation: TrainOperation, parent: ModelDescriptor, producers) -> None:
    """Ledger rule (graph_planner._training_parent): an SFT adapter needs materialize before DPO/KTO."""
    ref, stage = operation.model_input.policy.adapter, operation.config.training_stage
    matches = [item for item in producers if item.model == ref]
    if stage in {"dpo", "kto"} and (len(matches) != 1 or matches[0].training_stage is None):
        raise ValueError("parent training stage evidence is required")
    for item in matches:
        if (item.job_id, item.attempt_id, item.manifest_sha256) != (
                parent.producer_job_id, parent.producer_attempt_id, parent.producer_manifest_sha256):
            raise ValueError("parent producer evidence differs from the verified artifact")
        if item.training_stage == "sft" and stage in {"dpo", "kto"}:
            raise ValueError("SFT to preference training requires explicit materialize")


def _continuation(operation, verifier: ArtifactVerifier, evidence_for) -> None:
    if not isinstance(operation, TrainOperation) or operation.model_input.initialization != "continue_adapter":
        return
    policy = operation.model_input.policy
    check_continuation(operation.config, verifier.verify(policy.adapter).adapter, evidence_for(policy.base))


def _verified_evidence(evidence, base: BaseRef, verifier: ArtifactVerifier):
    from .graph_planner import ModelTargetEvidence
    evidence = ModelTargetEvidence.model_validate(evidence)
    if evidence.base != base:
        raise ValueError("inspected target evidence belongs to another base")
    if isinstance(base, ModelArtifactRef):
        # Artifact-base evidence must be the complete linear inventory of the verified full model.
        verify_inspected_targets(evidence, verifier.verify(base), base)
    return evidence


# -- control plane: staging ----------------------------------------------------------------------

def _write_atomic(path: Path, data: bytes) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def stage_model_inputs(operation, approved_cache: ModelArtifactStore, staging_root: Path, *,
                       target_evidence: tuple = (), producers: tuple = ()) -> ResolvedModelInputs:
    """Verify every model role in the approved cache and write ``model-inputs.json``; never copy weights.

    ``target_evidence`` (``ModelTargetEvidence``) is required for adapter continuation. ``producers``
    (``graph_planner.ProducerEvidence`` from the job ledger) is required to continue an adapter into
    DPO/KTO, because the producer stage of a parent adapter is not artifact metadata.
    """
    operation = _operation(operation)
    if not isinstance(approved_cache, ModelArtifactStore):
        raise TypeError("staging requires the approved control-plane ModelArtifactStore")
    verifier = approved_cache.verifier()   # lineage-aware; each artifact hashed once in this call
    resolved = _derive(operation, verifier)
    if isinstance(operation, TrainOperation) and operation.model_input.initialization == "continue_adapter":
        _parent_stage(operation, verifier.verify(operation.model_input.policy.adapter), tuple(producers))

    def evidence_for(base):
        matches = [item for item in target_evidence if item.base == base]
        return _verified_evidence(matches[0], base, verifier) if len(matches) == 1 else None
    _continuation(operation, verifier, evidence_for)
    root = Path(staging_root).absolute()
    check_link_free(root)
    root.mkdir(parents=True, exist_ok=True)
    if any(path.suffix in _WEIGHT_SUFFIXES for path in root.rglob("*")):
        raise ValueError("attempt directory must not contain model weights; stage them through the model cache")
    _write_atomic(root / MODEL_INPUTS, canonical(resolved.model_dump(mode="json")))
    return resolved


# -- worker: cache and HF verification -----------------------------------------------------------

def verify_cached_artifact(cache_root: Path, ref: ModelArtifactRef) -> ModelDescriptor:
    """Verify ``<cache_root>/<artifact_id>/<inventory_sha256>`` without lineage (fixed argv entry for 7.4).

    A remote cache holds only the roles an operation binds, not every ancestor.
    """
    return ArtifactVerifier(cache_root, lineage=False).verify(ref)


def hf_root_location(root: HFRootRef, hf_home: Path) -> Path:
    owner, name = root.repo_id.split("/")
    return Path(hf_home).absolute() / HF_ROOTS / owner / name / root.revision   # nested: no name collisions


def resolve_hf_root(root: HFRootRef, hf_home: Path) -> Path:
    """Download (or reuse) a link-free local snapshot of an exact HF root and verify it; return its path.

    Same GPU-host download behaviour as v1 LLaMA-Factory loading, but into ``local_dir`` (real files)
    rather than the symlinked hub cache. The config hash and tokenizer fingerprint must equal the root.
    """
    return _resolve_hf_snapshot(root, hf_home)[0]


def _resolve_hf_snapshot(root: HFRootRef, hf_home: Path) -> tuple[Path, dict]:
    root = HFRootRef.model_validate(root)
    home = Path(hf_home).absolute()
    check_link_free(home)
    target = hf_root_location(root, home)
    check_link_free(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    from huggingface_hub import snapshot_download
    returned = snapshot_download(root.repo_id, revision=root.revision, local_dir=str(target),
                                 allow_patterns=list(HF_ALLOW_PATTERNS))
    check_link_free(target)
    if Path(returned).resolve() != target.resolve():
        raise ValueError("HF snapshot was not written to the approved local directory")
    try:
        # The whole tree must be link-free (including .cache/huggingface/ metadata) and loadable unambiguously.
        config = check_full_model_directory(target)
        config_sha256 = local_config_sha256(target)
    except OSError as exc:
        raise ValueError("HF snapshot is incomplete or inaccessible") from exc
    if config_sha256 != root.config_sha256:
        raise ValueError("model config snapshot fingerprint mismatch")
    if local_tokenizer_fingerprint(target) != root.tokenizer_fingerprint:
        raise ValueError("tokenizer snapshot fingerprint mismatch")
    return target, config


# -- worker: verified paths ----------------------------------------------------------------------

@dataclass(frozen=True)
class VerifiedModelPath:
    role: ModelRole
    index: int
    path: Path
    staged: StagedModel
    descriptor: ModelDescriptor | None   # None only for an HF root

    def __post_init__(self):
        if (self.role, self.index) != (self.staged.role, self.staged.index):
            raise ValueError("verified model path role differs from its staged role")


@dataclass(frozen=True)
class VerifiedModelPaths:
    """Verified local paths only; the approved cache roots come from the worker configuration."""
    models: tuple[VerifiedModelPath, ...]

    def __post_init__(self):
        roles = [(item.role, item.index) for item in self.models]
        if len(set(roles)) != len(roles):
            raise ValueError("duplicate verified model role")

    def path(self, role: ModelRole, index: int = 0) -> Path:
        for item in self.models:
            if (item.role, item.index) == (role, index):
                return item.path
        raise ValueError(f"unbound model role: {role}")


def verify_model_paths(resolved: ResolvedModelInputs, operation, *, model_cache: Path, hf_home: Path | None,
                       inspect_targets: Callable[[Path, BaseRef], Any] | None = None) -> VerifiedModelPaths:
    """Re-derive the roles from the operation and verified cache descriptors; they must equal ``resolved``.

    ``inspect_targets(path, base)`` supplies the inspected target inventory of the bound policy base; it
    is required for adapter continuation.
    """
    value = resolved.model_dump(mode="json") if hasattr(resolved, "model_dump") else resolved
    resolved = ResolvedModelInputs.model_validate(value)
    operation = _operation(operation)
    if (resolved.operation_kind, resolved.operation_sha256) != (operation.kind, operation_sha256(operation)):
        raise ValueError("staged model inputs belong to another operation")
    verifier = ArtifactVerifier(model_cache, lineage=False)   # each cache entry hashed once in this call
    if _derive(operation, verifier) != resolved:
        raise ValueError("staged model inputs differ from the operation's verified roles")
    home = Path(hf_home).absolute() if hf_home is not None else None
    snapshots, configs, entries = {}, {}, []
    for staged in resolved.models:
        if staged.artifact is not None:
            path, descriptor = verifier.path(staged.artifact), verifier.verify(staged.artifact)
            if staged.storage_kind == "full_model":
                configs[path] = check_full_model_directory(path)
        else:
            if home is None:
                raise ValueError("HF root inputs require an approved HF_HOME cache")
            key = staged.hf_root.model_dump_json()
            if key not in snapshots:
                snapshots[key], config = _resolve_hf_snapshot(staged.hf_root, home)
                configs[snapshots[key]] = config
            path, descriptor = snapshots[key], None
        entries.append(VerifiedModelPath(role=staged.role, index=staged.index, path=path, staged=staged,
                                         descriptor=descriptor))
    verified = VerifiedModelPaths(models=tuple(entries))
    if isinstance(operation, TrainOperation):
        if operation.config.training_method == "qlora" and "quantization_config" in configs[
                verified.path("policy_base")]:
            raise ValueError("QLoRA requires an unquantized full effective base")

        def evidence_for(base):
            if inspect_targets is None:
                return None
            return _verified_evidence(inspect_targets(verified.path("policy_base"), base), base, verifier)
        _continuation(operation, verifier, evidence_for)
    return verified


# -- worker: binding -----------------------------------------------------------------------------

class BoundTrainingPlan(StrictModel):
    plan: dict[str, Any]
    binding: dict[str, Any]
    template_sha256: Sha256
    bound_sha256: Sha256
    binding_sha256: Sha256

    @model_validator(mode="after")
    def evidence_hashes(self):
        if (digest(canonical(self.plan)), digest(canonical(self.binding))) != (self.bound_sha256,
                                                                               self.binding_sha256):
            raise ValueError("bound plan evidence hash mismatch")
        return self


def _walk(value, key=""):
    if isinstance(value, dict):
        for name, item in value.items():
            yield from _walk(item, f"{key}.{name}" if key else str(name))
    elif isinstance(value, list):
        for position, item in enumerate(value):
            yield from _walk(item, f"{key}[{position}]")
    else:
        yield key, value


def _approved_path(path: Path, roots: tuple[Path, ...]) -> Path:
    path = Path(path)
    if not path.is_absolute() or ".." in path.parts or not any(
            path != root and path.is_relative_to(root) for root in roots):
        raise ValueError(OUTSIDE)
    check_link_free(path)
    if not path.is_dir():
        raise ValueError("bound model path is not a directory")
    return path


def _inconsistent(key: str):
    return ValueError(f"bound training plan is inconsistent with verified model roles: {key}")


def bind_training_plan(template: Mapping, verified_bindings: VerifiedModelPaths, *,
                       approved_roots: Sequence[Path]) -> BoundTrainingPlan:
    """Replace only allowlisted role sentinels with verified local paths; refuse anything else.

    ``approved_roots`` are the model cache and HF_HOME from the worker's own configuration, never taken
    from the verified paths, so a forged path cannot carry its own root.
    """
    if not isinstance(verified_bindings, VerifiedModelPaths):
        raise TypeError("binding requires VerifiedModelPaths from verify_model_paths")
    if not isinstance(template, Mapping):
        raise TypeError("training template must be a mapping")
    roots = tuple(Path(root).absolute() for root in approved_roots)
    if not roots:
        raise ValueError("binding requires the worker's approved model cache roots")
    template = json.loads(canonical(dict(template)))   # detached, JSON-normalized copy
    verified = {(item.role, item.index): item for item in verified_bindings.models}
    slots = set(ROLE_SLOTS.values())
    for key, value in _walk(template):
        if isinstance(value, str) and value.startswith(ROLE_PREFIX) and key not in slots:
            raise ValueError(f"unbound or misplaced model role placeholder: {key}")
    plan, placed = dict(template), set()
    for key in sorted(slots & set(template)):
        value = template[key]
        if not isinstance(value, str) or not value.startswith(ROLE_PREFIX):
            raise ValueError("graph training binds only typed model roles")
        role = value[len(ROLE_PREFIX):]
        entry = verified.get((role, 0))
        if ROLE_SLOTS.get(role) != key or entry is None:
            raise ValueError(f"unbound or misplaced model role placeholder: {key}")
        plan[key] = _approved_path(entry.path, roots).as_posix()
        placed.add(role)
    required = {role for role, _ in verified}
    if "reward_adapter" in required:
        required.discard("reward_base")   # a LoRA reward sits on the policy base (plan F5)
    missing = sorted(required - placed)
    if missing:
        raise ValueError(f"unbound or misplaced model role placeholder: {missing[0]}")
    if ("policy_adapter" in placed) != ("create_new_adapter" in plan) or plan.get("create_new_adapter", False):
        raise _inconsistent("create_new_adapter")
    reward_type = ("lora" if "reward_adapter" in placed else "full") if placed & {"reward_base",
                                                                                  "reward_adapter"} else None
    if plan.get("reward_model_type") != reward_type:
        raise _inconsistent("reward_model_type")
    reference_bit = plan.get("quantization_bit") if "reference_base" in placed else None
    if ("ref_model_quantization_bit" in plan) != (reference_bit is not None) or plan.get(
            "ref_model_quantization_bit") != reference_bit:
        raise _inconsistent("ref_model_quantization_bit")
    if "resume_from_checkpoint" in plan:
        raise ValueError("a bound plan starts a fresh optimizer; resume_from_checkpoint is not allowed")
    for key in sorted(PATH_KEYS & set(plan) - slots):
        if RELATIVE_PATHS.get(key) != plan[key]:
            raise ValueError(f"training plan contains an unapproved path: {key}")
    if any(plan.get(key) != value for key, value in RELATIVE_PATHS.items()):
        raise ValueError("training plan contains an unapproved path: dataset_dir/output_dir")
    for key, value in _walk(plan):
        if key in slots:
            continue
        if isinstance(value, str) and value.startswith(ROLE_PREFIX):
            raise ValueError(f"unbound or misplaced model role placeholder: {key}")
        if isinstance(value, str) and _PATH_LIKE.match(value) and RELATIVE_PATHS.get(key) != value:
            raise ValueError(f"training plan contains an unapproved path: {key}")
    binding = {
        "schema_version": 1,
        "models": [{"role": item.role, "index": item.index, "path": item.path.as_posix(),
                    "slot": ROLE_SLOTS[item.role] if item.role in placed else None,
                    **item.staged.model_dump(mode="json", exclude={"role", "index"})}
                   for _, item in sorted(verified.items())],
        "reference_quantization_bit": plan.get("ref_model_quantization_bit"),
        "reward_model_type": plan.get("reward_model_type"),
    }
    return BoundTrainingPlan(plan=plan, binding=binding, template_sha256=digest(canonical(template)),
                             bound_sha256=digest(canonical(plan)), binding_sha256=digest(canonical(binding)))


def write_bound_plan(directory: Path, bound: BoundTrainingPlan) -> None:
    """Write train.bound.yaml, model-binding.json and binding-evidence.json with deterministic bytes.

    The evidence also records the SHA256 of the exact ``train.bound.yaml`` bytes that the engine executes.
    """
    bound = BoundTrainingPlan.model_validate(bound.model_dump(mode="json"))
    directory = Path(directory)
    plan_bytes = yaml.safe_dump(bound.plan, sort_keys=True).encode("utf-8")
    evidence = {"template_sha256": bound.template_sha256, "bound_sha256": bound.bound_sha256,
                "binding_sha256": bound.binding_sha256, "train_bound_yaml_sha256": hashlib.sha256(plan_bytes).hexdigest()}
    for name, data in (("train.bound.yaml", plan_bytes), ("model-binding.json", canonical(bound.binding)),
                       ("binding-evidence.json", canonical(evidence))):
        _write_atomic(directory / name, data)
