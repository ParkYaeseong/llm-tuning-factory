"""V2 operation runner: path-free model-input staging and worker-side training-plan binding.

Control plane: ``stage_model_inputs`` verifies every model role of a resolved operation against the
approved artifact cache and writes ``model-inputs.json`` (identities only; never weights or host paths).

Worker: ``verify_model_paths`` re-derives the roles from the operation and the hash-verified model cache,
resolves HF roots into link-free local snapshots and repeats every rule. ``bind_training_plan`` replaces
only the allowlisted role sentinels of the logical LLaMA-Factory template with those verified paths.

Every model directory produced or accepted here is link-free: no symlink or reparse point on the path, on
its ancestors or inside the tree, because the model readers refuse links. HF roots are therefore
downloaded with ``snapshot_download(local_dir=...)`` (real files), never bound as hub-cache snapshots,
which are trees of symlinks. Fingerprints are computed on exactly the tree that is bound; the
``.cache/huggingface/`` download metadata is walked for links but never fingerprinted.

The PEFT/LLaMA-Factory producer stage of a parent adapter is ledger evidence, not artifact metadata, so
the SFT-to-preference rule is enforced at compilation and staging only; the worker cannot re-derive it.

Module import touches only the standard library, pydantic, PyYAML and local contracts: no graph_store or
jobs (the control-plane runner, 7.5) and no torch, transformers or huggingface_hub (imported lazily).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal

import yaml
from pydantic import Field, TypeAdapter, model_validator

from .config import StrictModel
from .model_artifacts import (
    TOKENIZER_FILES,
    BaseRef,
    HFRootRef,
    ModelArtifactRef,
    ModelArtifactStore,
    ModelDescriptor,
    ModelLoadRef,
    _check_ancestors,
    _FileEntry,
    _files,
    _inventory,
    _inventory_bytes,
    _open_regular,
    _read_json,
    _validate_payload,
)
from .model_ops import (
    check_continuation,
    check_full_model_directory,
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
RELATIVE_PATHS = MappingProxyType({"dataset_dir": "./data", "output_dir": "./output"})
OUTSIDE = "bound model path is outside the approved model caches"
_PATH_LIKE = re.compile(r"^(?:/|\\|~|\.{1,2}(?:[/\\]|$)|[A-Za-z]:[/\\])")
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


def _cached(verify: Callable[[ModelArtifactRef], ModelDescriptor]):
    """One verification per artifact and public call (a descriptor never outlives the call)."""
    seen = {}

    def describe(ref: ModelArtifactRef) -> ModelDescriptor:
        ref = ModelArtifactRef.model_validate(ref)
        if _key(ref) not in seen:
            seen[_key(ref)] = verify(ref)
        return seen[_key(ref)]
    return describe


# -- role derivation and rules shared by staging and the worker ----------------------------------

def _role_refs(operation, describe) -> list[tuple[str, int, BaseRef]]:
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
        first = describe(operation.models[0])
        return [("operation_base", 0, first.effective_base),
                *(("operation_adapter", index, ref) for index, ref in enumerate(operation.models))]
    descriptor = describe(operation.model)
    if descriptor.storage_kind == "adapter":
        return [("operation_base", 0, descriptor.effective_base), ("operation_adapter", 0, operation.model)]
    if isinstance(operation, MaterializeOperation):
        raise ValueError("materialize requires a causal LoRA/QLoRA adapter without value head")
    return [("operation_base", 0, operation.model)]


def _staged(role: str, index: int, ref: BaseRef, describe) -> StagedModel:
    if isinstance(ref, HFRootRef):
        return StagedModel(role=role, index=index, source="hf_root", hf_root=ref, storage_kind="full_model",
                           config_sha256=ref.config_sha256, tokenizer_fingerprint=ref.tokenizer_fingerprint)
    descriptor = describe(ref)
    return StagedModel(role=role, index=index, source="artifact", artifact=ref, storage_kind=descriptor.storage_kind,
                       config_sha256=descriptor.config_sha256, tokenizer_fingerprint=descriptor.tokenizer_fingerprint)


def _check_load(load: ModelLoadRef, kind: str, describe) -> None:
    """model_artifacts' exact-load rules, without lineage (the worker cache holds only the roles)."""
    base = describe(load.base) if isinstance(load.base, ModelArtifactRef) else load.base
    if isinstance(base, ModelDescriptor) and base.storage_kind != "full_model":
        raise ValueError("model input base must be a full model")
    effective = base
    if load.adapter is not None:
        effective = describe(load.adapter)
        if effective.storage_kind != "adapter":
            raise ValueError("model input adapter must have adapter storage")
        if effective.effective_base != load.base:
            raise ValueError("adapter effective base differs from model input base")
        if (effective.config_sha256, effective.tokenizer_fingerprint) != (base.config_sha256,
                                                                           base.tokenizer_fingerprint):
            raise ValueError("adapter/base config or tokenizer differs")
    semantic = effective.semantic_kind if isinstance(effective, ModelDescriptor) else "causal_lm"
    if semantic != kind:
        raise ValueError(f"model input requires semantic kind {kind}")


def _root(ref: BaseRef, describe) -> HFRootRef:
    return ref if isinstance(ref, HFRootRef) else describe(ref).root


def _training_rules(operation: TrainOperation, roles: dict, describe):
    config, model_input = operation.config, operation.model_input
    check_model_input_contract(config, model_input)
    if model_input.initialization == "continue_adapter":
        if config.training_method not in {"lora", "qlora"}:
            raise ValueError("adapter continuation requires LoRA/QLoRA")
    elif model_input.initialization != ("full_weights" if config.training_method in {"full", "freeze"}
                                        else "new_adapter"):
        raise ValueError("initialization differs from requested training method")
    for load, kind in ((model_input.policy, "causal_lm"), (model_input.frozen_reference, "causal_lm"),
                       (model_input.reward_model, "reward_model")):
        if load is not None:
            _check_load(load, kind, describe)
    # LLaMA-Factory loads the tokenizer from each base path it is given (plan F1).
    policy = roles[("policy_base", 0)]
    for role in ("reference_base", "reward_base"):
        staged = roles.get((role, 0))
        if staged is not None and staged.tokenizer_fingerprint != policy.tokenizer_fingerprint:
            raise ValueError(f"{role} tokenizer differs from policy")
    policy_root = _root(model_input.policy.base, describe)
    for load in (model_input.frozen_reference, model_input.reward_model):
        if load is not None and _root(load.base, describe) != policy_root:
            raise ValueError("reference/reward HF root differs from policy")
    reward, reward_type = model_input.reward_model, None
    if reward is not None:
        # A LoRA reward is loaded onto the policy's own base (plan F5).
        if reward.adapter is not None and reward.base != model_input.policy.base:
            raise ValueError("LoRA reward requires the exact policy effective base")
        head = describe(reward.adapter or reward.base)
        if not any(tensor.file == "value_head.safetensors" and tensor.name.startswith("v_head.")
                   for tensor in head.tensors):
            raise ValueError("PPO reward requires reward_model semantics with a value_head.safetensors value head")
        reward_type = "lora" if reward.adapter is not None else "full"
    base = model_input.policy.base
    if (config.training_method == "qlora" and isinstance(base, ModelArtifactRef)
            and describe(base).quantization_bit is not None):
        raise ValueError("QLoRA requires an unquantized full effective base")
    reference_bit = (config.quantization_bit if model_input.frozen_reference is not None
                     and config.training_method == "qlora" else None)   # plan §6 Q5
    return reference_bit, reward_type


def _derive(operation, describe) -> ResolvedModelInputs:
    """Roles and descriptor-level rules of one operation; identical on the control plane and the worker."""
    require_real = not operation.lineage.simulation
    models = tuple(_staged(role, index, ref, describe) for role, index, ref in _role_refs(operation, describe))
    roles = {(item.role, item.index): item for item in models}
    if require_real and any(not describe(item.artifact).eligible_for_real_use
                            for item in models if item.artifact is not None):
        raise ValueError("synthetic/simulation artifact is ineligible for real use")
    for item in models:
        base = roles.get((item.role.replace("_adapter", "_base"), 0))
        if item.storage_kind == "adapter" and base is not None and (
                item.config_sha256, item.tokenizer_fingerprint) != (base.config_sha256, base.tokenizer_fingerprint):
            raise ValueError("adapter/base config or tokenizer differs")
    reference_bit = reward_type = None
    if isinstance(operation, TrainOperation):
        reference_bit, reward_type = _training_rules(operation, roles, describe)
    elif isinstance(operation, MergeOperation):
        validate_merge_inputs(operation.models, [describe(ref) for ref in operation.models], operation.weights,
                              require_real=require_real)
    elif isinstance(operation, MaterializeOperation):
        from .graph_planner import check_materializable
        check_materializable(describe(operation.model))
        base = roles[("operation_base", 0)]
        if base.artifact is not None and describe(base.artifact).quantization_bit is not None:
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


def _continuation(operation, describe, evidence_for) -> None:
    if not isinstance(operation, TrainOperation) or operation.model_input.initialization != "continue_adapter":
        return
    policy = operation.model_input.policy
    parent = describe(policy.adapter)
    check_continuation(operation.config, parent.adapter, evidence_for(policy.base))


def _verified_evidence(evidence, base: BaseRef, describe):
    from .graph_planner import ModelTargetEvidence
    evidence = ModelTargetEvidence.model_validate(evidence)
    if evidence.base != base:
        raise ValueError("inspected target evidence belongs to another base")
    if isinstance(base, ModelArtifactRef):
        # Artifact-base evidence must be the complete linear inventory of the verified full model.
        verify_inspected_targets(evidence, describe(base), base)
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
    if isinstance(operation, TrainOperation):
        check_model_input_contract(operation.config, operation.model_input)
        # Lineage-aware verification of every load (frozen reference, reward root/tokenizer, adapter base).
        approved_cache.validate_input(operation.model_input, require_real=not operation.lineage.simulation)
    describe = _cached(approved_cache.verify)
    resolved = _derive(operation, describe)
    if isinstance(operation, TrainOperation) and operation.model_input.initialization == "continue_adapter":
        _parent_stage(operation, describe(operation.model_input.policy.adapter), tuple(producers))

    def evidence_for(base):
        matches = [item for item in target_evidence if item.base == base]
        return _verified_evidence(matches[0], base, describe) if len(matches) == 1 else None
    _continuation(operation, describe, evidence_for)
    root = Path(staging_root).absolute()
    _check_ancestors(root)
    root.mkdir(parents=True, exist_ok=True)
    if any(path.suffix in _WEIGHT_SUFFIXES for path in root.rglob("*")):
        raise ValueError("attempt directory must not contain model weights; stage them through the model cache")
    _write_atomic(root / MODEL_INPUTS, canonical(resolved.model_dump(mode="json")))
    return resolved


# -- worker: cache and HF verification -----------------------------------------------------------

def _artifact_location(cache_root: Path, ref: ModelArtifactRef) -> Path:
    return Path(cache_root).absolute() / ref.artifact_id / ref.inventory_sha256


def verify_cached_artifact(cache_root: Path, ref: ModelArtifactRef) -> ModelDescriptor:
    """Verify ``<cache_root>/<artifact_id>/<inventory_sha256>/{files/,descriptor.json,inventory.json}``.

    Same identity, inventory and payload checks as ``ModelArtifactStore``, but no lineage: a remote cache
    holds only the roles an operation binds, not every ancestor.
    """
    ref = ModelArtifactRef.model_validate(ref)
    location = _artifact_location(cache_root, ref)
    try:
        _check_ancestors(location)
        if {item.name for item in location.iterdir()} != {"files", "descriptor.json", "inventory.json"}:
            raise ValueError("unexpected model artifact metadata files")
        descriptor_raw = _read_json(location / "descriptor.json")
        inventory_raw = _read_json(location / "inventory.json")
        descriptor = ModelDescriptor.model_validate(descriptor_raw)
        if digest(canonical(descriptor_raw)) != ref.artifact_id:
            raise ValueError("model descriptor hash mismatch")
        if not isinstance(inventory_raw, list):
            raise ValueError("invalid model artifact inventory")
        inventory = tuple(_FileEntry.model_validate(entry) for entry in inventory_raw)
        if digest(_inventory_bytes(inventory)) != ref.inventory_sha256:
            raise ValueError("model inventory hash mismatch")
        if _inventory(location / "files") != inventory:
            raise ValueError("model inventory files/hash mismatch")
        _validate_payload(location / "files", descriptor, inventory)
        return descriptor
    except OSError as exc:
        raise ValueError("model artifact is missing or inaccessible") from exc


def hf_root_location(root: HFRootRef, hf_home: Path) -> Path:
    owner, name = root.repo_id.split("/")
    return Path(hf_home).absolute() / HF_ROOTS / owner / name / root.revision   # nested: no name collisions


def resolve_hf_root(root: HFRootRef, hf_home: Path) -> Path:
    """Download (or reuse) a link-free local snapshot of an exact HF root and verify it; return its path.

    Same GPU-host download behaviour as v1 LLaMA-Factory loading, but into ``local_dir`` (real files)
    rather than the symlinked hub cache. The config hash and tokenizer fingerprint must equal the root.
    """
    root = HFRootRef.model_validate(root)
    home = Path(hf_home).absolute()
    _check_ancestors(home)
    target = hf_root_location(root, home)
    _check_ancestors(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    from huggingface_hub import snapshot_download
    returned = snapshot_download(root.repo_id, revision=root.revision, local_dir=str(target),
                                 allow_patterns=list(HF_ALLOW_PATTERNS))
    _check_ancestors(target)
    if Path(returned).resolve() != target.resolve():
        raise ValueError("HF snapshot was not written to the approved local directory")
    try:
        _files(target)   # the whole tree is link-free, including .cache/huggingface/ metadata
        with _open_regular(target / "config.json") as stream:
            config_sha256 = hashlib.file_digest(stream, "sha256").hexdigest()
    except OSError as exc:
        raise ValueError("HF snapshot is incomplete or inaccessible") from exc
    if config_sha256 != root.config_sha256:
        raise ValueError("model config snapshot fingerprint mismatch")
    if local_tokenizer_fingerprint(target) != root.tokenizer_fingerprint:
        raise ValueError("tokenizer snapshot fingerprint mismatch")
    check_full_model_directory(target)
    return target


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
    models: tuple[VerifiedModelPath, ...]
    roots: tuple[Path, ...]              # approved cache roots (model cache, HF_HOME)

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
    cache = Path(model_cache).absolute()
    _check_ancestors(cache)
    describe = _cached(lambda ref: verify_cached_artifact(cache, ref))
    if _derive(operation, describe) != resolved:
        raise ValueError("staged model inputs differ from the operation's verified roles")
    home = Path(hf_home).absolute() if hf_home is not None else None
    snapshots, entries = {}, []
    for staged in resolved.models:
        if staged.artifact is not None:
            path, descriptor = _artifact_location(cache, staged.artifact) / "files", describe(staged.artifact)
            if staged.storage_kind == "full_model":
                check_full_model_directory(path)
        else:
            if home is None:
                raise ValueError("HF root inputs require an approved HF_HOME cache")
            key = staged.hf_root.model_dump_json()
            if key not in snapshots:
                snapshots[key] = resolve_hf_root(staged.hf_root, home)
            path, descriptor = snapshots[key], None
        entries.append(VerifiedModelPath(role=staged.role, index=staged.index, path=path, staged=staged,
                                         descriptor=descriptor))
    verified = VerifiedModelPaths(models=tuple(entries), roots=(cache, *((home,) if home is not None else ())))
    if isinstance(operation, TrainOperation):
        if operation.config.training_method == "qlora":
            config = _read_json(verified.path("policy_base") / "config.json")
            if not isinstance(config, dict) or "quantization_config" in config:
                raise ValueError("QLoRA requires an unquantized full effective base")

        def evidence_for(base):
            if inspect_targets is None:
                return None
            return _verified_evidence(inspect_targets(verified.path("policy_base"), base), base, describe)
        _continuation(operation, describe, evidence_for)
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
    _check_ancestors(path)
    if not path.is_dir():
        raise ValueError("bound model path is not a directory")
    return path


def _inconsistent(key: str):
    return ValueError(f"bound training plan is inconsistent with verified model roles: {key}")


def bind_training_plan(template: Mapping, verified_bindings: VerifiedModelPaths) -> BoundTrainingPlan:
    """Replace only allowlisted role sentinels with verified local paths; refuse anything else."""
    if not isinstance(verified_bindings, VerifiedModelPaths):
        raise TypeError("binding requires VerifiedModelPaths from verify_model_paths")
    if not isinstance(template, Mapping):
        raise TypeError("training template must be a mapping")
    template = json.loads(canonical(dict(template)))   # detached, JSON-normalized copy
    roots = tuple(Path(root).absolute() for root in verified_bindings.roots)
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
    for key, value in _walk(plan):
        if key in slots and key in plan:
            continue
        if isinstance(value, str) and value.startswith(ROLE_PREFIX):
            raise ValueError(f"unbound or misplaced model role placeholder: {key}")
        if isinstance(value, str) and _PATH_LIKE.match(value) and RELATIVE_PATHS.get(key) != value:
            raise ValueError(f"training plan contains an unapproved path: {key}")
    if any(plan.get(key) != value for key, value in RELATIVE_PATHS.items()):
        raise ValueError("training plan contains an unapproved path: dataset_dir/output_dir")
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
    """Write train.bound.yaml, model-binding.json and binding-evidence.json with deterministic bytes."""
    bound = BoundTrainingPlan.model_validate(bound.model_dump(mode="json"))
    directory = Path(directory)
    evidence = {"template_sha256": bound.template_sha256, "bound_sha256": bound.bound_sha256,
                "binding_sha256": bound.binding_sha256}
    for name, data in (("train.bound.yaml", yaml.safe_dump(bound.plan, sort_keys=True).encode("utf-8")),
                       ("model-binding.json", canonical(bound.binding)),
                       ("binding-evidence.json", canonical(evidence))):
        _write_atomic(directory / name, data)
