"""LoRA-targetable module evidence: a meta-device inspector and its verifier.

``inspect_target_modules`` needs the pinned model-operation runtime (imported lazily);
``verify_inspected_targets`` is pure and checks evidence against a verified full-model inventory.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import TYPE_CHECKING

from .model_artifacts import BaseRef, HFRootRef, ModelArtifactRef, ModelDescriptor, _open_regular
from .registry import canonical, digest

if TYPE_CHECKING:
    from .graph_planner import ModelTargetEvidence

# Composite (vision/audio + text) model types the app trains on text only. LLaMA-Factory 0.9.5 loads them with
# AutoModelForImageTextToText, keeps the vision/audio towers and projectors frozen and records the adapted modules by
# their full names; every one of them lies under the language model.
COMPOSITE_MODEL_TYPES = frozenset({"qwen3_5", "qwen3_5_moe", "gemma4"})
LANGUAGE_MODEL_PREFIX = "model.language_model."
# Checkpoint tensors transformers 5.6.0 never loads for these model types (Qwen3.5's multi-token-prediction head).
UNLOADED_PREFIXES = {"qwen3_5": ("mtp.",), "qwen3_5_moe": ("mtp.",)}


def unloaded_prefixes(config) -> tuple:
    return UNLOADED_PREFIXES.get(config.get("model_type"), ())


def is_composite_inventory(names) -> bool:
    return any(name.startswith(LANGUAGE_MODEL_PREFIX) for name in names)


def target_leaves(targets) -> tuple:
    """The leaf names of adapter targets: how planned and recorded target sets are compared."""
    return tuple(sorted({target.rsplit(".", 1)[-1] for target in targets}))


def resolved_lora_targets(lora_target: str, module_names) -> tuple:
    """The ``target_modules`` LLaMA-Factory 0.9.5 passes to PEFT for ``lora_target`` over a base's inspected Linear
    modules: leaf names for text models, full language-model module names for composite models
    (``patch_target_modules``). PEFT 0.18.1 records a list of 20 or more names as the shortest suffixes that still
    select only those modules (Gemma 4: ``language_model.layers.0.self_attn.q_proj``, ``1.self_attn.q_proj``), so
    recorded targets are compared by leaf (``target_leaves``) or by suffix matching, never as strings."""
    names = tuple(module_names)
    wanted = set(target_leaves(names)) if lora_target == "all" else set(lora_target.split(","))
    if not is_composite_inventory(names):
        return tuple(sorted(wanted))
    return tuple(sorted(name for name in names if name.rsplit(".", 1)[-1] in wanted))


def _not_a_target(name: str) -> bool:
    module = name[:-len(".weight")]
    return ("lm_head" in module or module.startswith(("v_head.", "value_head.", "score."))
            or any("embed" in part or "norm" in part for part in module.split(".")))


def verify_inspected_targets(evidence, descriptor: ModelDescriptor, ref: ModelArtifactRef) -> ModelTargetEvidence:
    """Accept inspector evidence only if it is the complete linear inventory of the verified full model."""
    from .graph_planner import ModelTargetEvidence
    evidence = ModelTargetEvidence.model_validate(evidence)
    descriptor = ModelDescriptor.model_validate(descriptor)
    ref = ModelArtifactRef.model_validate(ref)
    if evidence.base != ref:
        raise ValueError("inspected target evidence belongs to another base")
    if digest(canonical(descriptor.model_dump(mode="json"))) != ref.artifact_id:
        raise ValueError("inspected target descriptor differs from the artifact identity")
    if descriptor.storage_kind != "full_model":
        raise ValueError("inspected targets require a full-model inventory")
    weights = {tensor.name: tensor.shape for tensor in descriptor.tensors}
    for module in evidence.modules:
        if weights.get(module.name + ".weight") != (module.output_dimension, module.input_dimension):
            raise ValueError("inspected base dimensions differ from verified tensor inventory")
    expected = {name[:-len(".weight")] for name, shape in weights.items()
                if name.endswith(".weight") and len(shape) == 2 and not _not_a_target(name)}
    names = {module.name for module in evidence.modules}
    # Composite models: vision/audio towers, projectors and mtp.* are never adapted. Evidence that lists modules
    # outside the language model (gemma3's, inspected through its text-and-vision class) keeps the full rule.
    if is_composite_inventory(weights) and all(name.startswith(LANGUAGE_MODEL_PREFIX) for name in names):
        expected = {name for name in expected if name.startswith(LANGUAGE_MODEL_PREFIX)}
    if expected != names:
        raise ValueError("inspected target modules differ from verified tensor inventory")
    return evidence


def inspect_target_modules(model_dir: Path, base: BaseRef) -> ModelTargetEvidence:
    """Meta-device inventory of every LoRA-targetable Linear module (LLaMA-Factory's rule)."""
    from .graph_planner import ModelTargetEvidence
    model_dir = Path(model_dir).absolute()
    if isinstance(base, HFRootRef):
        with _open_regular(model_dir / "config.json") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != base.config_sha256:
                raise ValueError("model config snapshot fingerprint mismatch")
    return ModelTargetEvidence(base=base, modules=inspect_linear_modules(model_dir))


def inspect_linear_modules(model_dir: Path) -> tuple:
    """The sorted ``TargetModuleEvidence`` of a local full model, before it has an artifact identity.

    The worker writes these for full-model outputs; the control plane binds them to the frozen
    artifact and accepts them only through ``verify_inspected_targets``. Composite models:
    AutoModelForImageTextToText, language-model modules only (LLaMA-Factory's rule).
    """
    from .graph_planner import TargetModuleEvidence
    from .runtime_profiles import model_ops_runtime
    model_dir = Path(model_dir).absolute()
    torch, _, _, transformers = model_ops_runtime()
    config = transformers.AutoConfig.from_pretrained(str(model_dir), local_files_only=True)
    composite = config.model_type in COMPOSITE_MODEL_TYPES
    loader = transformers.AutoModelForImageTextToText if composite else transformers.AutoModelForCausalLM
    with torch.device("meta"):
        model = loader.from_config(config)
    excluded = {"lm_head"}
    if config.model_type == "chatglm":
        excluded.add("output_layer")
    elif config.model_type == "internlm2":
        excluded.add("output")
    # The prefix comes first: Gemma 4's vision Gemma4ClippableLinear wrappers have no in_features.
    modules = sorted(
        (TargetModuleEvidence(name=name, input_dimension=module.in_features, output_dimension=module.out_features)
         for name, module in model.named_modules()
         if (not composite or name.startswith(LANGUAGE_MODEL_PREFIX))
         and "Linear" in type(module).__name__ and "Embedding" not in type(module).__name__
         and not any(item in name for item in excluded)), key=lambda item: item.name)
    return tuple(modules)
