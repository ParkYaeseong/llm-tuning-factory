"""Exact model operations: compatible LoRA ``cat`` merge and full-model materialization.

Importing this module touches only the standard library, pydantic and local contracts. torch,
PEFT, Transformers and safetensors are imported inside the numerical functions, after every input
refusal and after the stdlib finite scan of the (small) adapter inputs. The scanner lives in
``safetensors_scan`` and the target-module inspector/verifier in ``target_inspection``; both are
re-exported here. Policy ``model-ops-f32-v1`` computes in FP32 from an exact upcast of
the inputs and stores every output tensor as F32. Inputs are ``LocalModel`` directories that the
caller has already verified against their identity; the checks here repeat the cheap ones.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from types import MappingProxyType
from typing import Literal

from pydantic import ValidationError

from .config import StrictModel
from .model_artifacts import (
    AdapterMetadata,
    AlphaPattern,
    BaseRef,
    HFRootRef,
    ModelArtifactRef,
    ModelDescriptor,
    RankPattern,
    _check_ancestors,
    _FileEntry,
    _files,
    _open_regular,
    _read_json,
    _tensor_header,
    _tokenizer_hash,
    _validate_adapter,
    is_tokenizer_file,
)
from .runtime_profiles import model_ops_runtime
from .safetensors_scan import scan_finite_safetensors
from .target_inspection import inspect_target_modules, verify_inspected_targets

__all__ = [
    "POLICY_F32_V1", "TOLERANCE_POLICIES", "LocalModel", "MaterializeReceipt", "MergePlan", "MergeReceipt",
    "StagedLoad", "TolerancePolicy", "adapter_metadata_from_config", "inspect_target_modules",
    "materialize_model", "merge_adapters", "scan_finite_safetensors", "validate_materialize_input",
    "validate_merge_inputs", "verify_inspected_targets", "write_adapter_config",
]

POLICY_F32_V1 = "model-ops-f32-v1"
PEFT_PREFIX = "base_model.model."
PROBE_TEXT = "factory model operations numerical probe"
VARIANT = "unsupported adapter conversion variant"
DELTA_FAILED = "model-operation output failed delta verification"
LOGITS_FAILED = "reloaded logits differ beyond tolerance policy"
# Input dtypes with an exact FP32 upcast; F64 would be narrowed and integers are not weights to merge.
POLICY_DTYPES = frozenset({"F16", "BF16", "F32"})
SHARD_INDEX = "model.safetensors.index.json"


def _quantized(operation: str) -> str:
    return f"{operation} requires unquantized effective base weights"


def _dtype_refusal(operation: str) -> str:
    return f"{operation} requires F16, BF16 or F32 tensors under policy {POLICY_F32_V1}"


class TolerancePolicy(StrictModel):
    policy_id: Literal["model-ops-f32-v1"] = POLICY_F32_V1
    storage_dtype: Literal["F32"] = "F32"   # every merge/materialize output tensor is stored F32
    delta_atol: float = 1e-6
    delta_rtol: float = 1e-5
    logits_atol: float = 1e-5
    logits_rtol: float = 1e-4


TOLERANCE_POLICIES: Mapping[str, TolerancePolicy] = MappingProxyType({POLICY_F32_V1: TolerancePolicy()})


@dataclass(frozen=True)
class LocalModel:
    """A verified local directory plus its identity; descriptor is None only for an HF root."""
    path: Path
    identity: ModelArtifactRef | HFRootRef
    descriptor: ModelDescriptor | None
    config_sha256: str
    tokenizer_fingerprint: str


@dataclass(frozen=True)
class StagedLoad:
    base: LocalModel      # full, unquantized effective base
    adapter: LocalModel   # descriptor.storage_kind == "adapter"


class MergePlan(StrictModel):
    inputs: tuple[ModelArtifactRef, ...]
    normalized_weights: tuple[float, ...]
    scales: tuple[float, ...]
    ranks: tuple[int, ...]
    output_rank: int
    effective_base: BaseRef
    target_modules: tuple[str, ...]


class MergeReceipt(StrictModel):
    kind: Literal["merge"] = "merge"
    policy_id: str
    inputs: tuple[ModelArtifactRef, ...]
    normalized_weights: tuple[float, ...]
    output_rank: int
    max_abs_delta_error: float
    max_abs_logits_error: float
    finite_tensors_checked: int
    output_files: tuple[str, ...]


class MaterializeReceipt(StrictModel):
    kind: Literal["materialize"] = "materialize"
    policy_id: str
    base: BaseRef
    adapter: ModelArtifactRef
    max_abs_delta_error: float
    max_abs_logits_error: float
    non_target_tensors_identical: Literal[True]
    tokenizer_fingerprint: str
    finite_tensors_checked: int
    output_files: tuple[str, ...]


def _policy(policy) -> TolerancePolicy:
    if not isinstance(policy, str) or policy not in TOLERANCE_POLICIES:
        raise ValueError("unsupported tolerance policy")
    return TOLERANCE_POLICIES[policy]


def _validation_message(error: ValidationError) -> str:
    return str(error.errors()[0]["msg"]).removeprefix("Value error, ")


def _linear_layout(descriptor: ModelDescriptor):
    # The numerical delta is B @ A for torch.nn.Linear (out, in) weights only.
    if descriptor.adapter.fan_in_fan_out:
        raise ValueError(VARIANT)


# -- input validation (no torch) -----------------------------------------------------------------

def validate_merge_inputs(refs, descriptors, weights, *, require_real: bool = True) -> MergePlan:
    """Reuse graph compilation's conversion rules; only the real-only rule is relaxable."""
    from .graph_planner import _merge_descriptors
    from .graph_schema import MergeNode

    refs = tuple(ModelArtifactRef.model_validate(ref) for ref in refs)
    descriptors = tuple(ModelDescriptor.model_validate(item) for item in descriptors)
    if len(refs) != len(descriptors):
        raise ValueError("merge requires one verified descriptor for each input")
    checked = descriptors if require_real else tuple(
        item.model_copy(update={"synthetic": False, "simulation": False}) for item in descriptors)
    _merge_descriptors(refs, checked)
    for descriptor in descriptors:
        _linear_layout(descriptor)
    weights = tuple(weights)
    if len(weights) != len(refs) or any(isinstance(weight, bool) or not isinstance(weight, (int, float))
                                        or not math.isfinite(weight) or weight <= 0 for weight in weights):
        raise ValueError("one positive finite weight is required for each model")
    try:
        MergeNode(id="merge", label="merge", models=refs, weights=weights, executor_profile_ids=("model-ops",))
    except ValidationError as exc:
        raise ValueError(_validation_message(exc)) from None
    total = sum(Fraction(str(weight)) for weight in weights)
    return MergePlan(
        inputs=refs, normalized_weights=tuple(float(Fraction(str(weight)) / total) for weight in weights),
        scales=tuple(item.adapter.alpha / item.adapter.rank for item in descriptors),
        ranks=tuple(item.adapter.rank for item in descriptors),
        output_rank=sum(item.adapter.rank for item in descriptors),
        effective_base=descriptors[0].effective_base,
        target_modules=tuple(sorted(descriptors[0].adapter.target_modules)))


def _check_adapter_local(model: LocalModel) -> ModelDescriptor:
    descriptor = model.descriptor
    if (not isinstance(model.identity, ModelArtifactRef) or descriptor is None
            or (model.config_sha256, model.tokenizer_fingerprint)
            != (descriptor.config_sha256, descriptor.tokenizer_fingerprint)):
        raise ValueError("local model identity differs from its verified descriptor")
    return descriptor


def _local_tokenizer_fingerprint(directory: Path) -> str:
    # Same algorithm as model_artifacts.tokenizer_fingerprint, hashing only tokenizer files.
    entries = []
    for relative, file in _files(directory):
        if is_tokenizer_file(relative):
            with _open_regular(file) as stream:
                checksum = hashlib.file_digest(stream, "sha256").hexdigest()
            entries.append(_FileEntry(path=relative, size=file.stat().st_size, sha256=checksum))
    return _tokenizer_hash(tuple(entries))


def _weight_layout(directory: Path) -> tuple[tuple[str, ...], dict | None]:
    """The safetensors files a loader reads: the indexed shards, else model.safetensors, else all.

    Other top-level copies (for example Mistral's consolidated.safetensors) are ignored, as loaders do.
    """
    names = {relative for relative, _ in _files(directory) if "/" not in relative}
    if SHARD_INDEX in names:
        index = _read_json(directory / SHARD_INDEX)
        weight_map = index.get("weight_map") if isinstance(index, dict) else None
        if (not isinstance(weight_map, dict) or not weight_map
                or any(not isinstance(shard, str) for shard in weight_map.values())):
            raise ValueError("invalid model shard index")
        shards = tuple(sorted(set(weight_map.values())))
        if any(shard not in names or not shard.endswith(".safetensors") for shard in shards):
            raise ValueError("missing or mismatched model shard")
        return shards, weight_map
    if "model.safetensors" in names:
        return ("model.safetensors",), None
    return tuple(sorted(name for name in names if name.endswith(".safetensors"))), None


def _weight_files(directory: Path) -> tuple[str, ...]:
    return _weight_layout(directory)[0]


def _header_inventory(directory: Path) -> dict:
    files, weight_map = _weight_layout(directory)
    inventory = {}
    for relative in files:
        for tensor in _tensor_header(directory / relative, relative):
            if tensor.name in inventory:
                raise ValueError("duplicate tensor names in local weights")
            inventory[tensor.name] = tensor
    if not inventory:
        raise ValueError("model operations require inspectable safetensors weights")
    if weight_map is not None and {name: tensor.file for name, tensor in inventory.items()} != weight_map:
        raise ValueError("model shard index does not cover tensor inventory")
    return inventory


def _verified_base(base: LocalModel, operation: str) -> dict:
    """Check the base's identity, local config/tokenizer and dtypes; return its tensor headers."""
    base = LocalModel(Path(base.path).absolute(), base.identity, base.descriptor, base.config_sha256,
                      base.tokenizer_fingerprint)
    if isinstance(base.identity, HFRootRef):
        if base.descriptor is not None:
            raise ValueError("local model identity differs from its verified descriptor")
        expected = (base.identity.config_sha256, base.identity.tokenizer_fingerprint)
    else:
        descriptor = base.descriptor
        if descriptor is None:
            raise ValueError("local model identity differs from its verified descriptor")
        if descriptor.storage_kind != "full_model":
            raise ValueError("effective base must be a full model")
        if descriptor.quantization_bit is not None:
            raise ValueError(_quantized(operation))
        expected = (descriptor.config_sha256, descriptor.tokenizer_fingerprint)
    if (base.config_sha256, base.tokenizer_fingerprint) != expected:
        raise ValueError("local model identity differs from its verified descriptor")
    config = _read_json(base.path / "config.json")
    if not isinstance(config, dict) or "quantization_config" in config:
        raise ValueError(_quantized(operation))
    with _open_regular(base.path / "config.json") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != base.config_sha256:
            raise ValueError("model config snapshot fingerprint mismatch")
    if _local_tokenizer_fingerprint(base.path) != base.tokenizer_fingerprint:
        raise ValueError("tokenizer snapshot fingerprint mismatch")
    inventory = _header_inventory(base.path)
    if base.descriptor is not None and set(inventory.values()) != set(base.descriptor.tensors):
        raise ValueError("tensor inventory geometry does not match snapshot")
    if any(tensor.dtype not in POLICY_DTYPES for tensor in inventory.values()):
        raise ValueError(_dtype_refusal(operation))
    return inventory


def _check_policy_dtypes(descriptor: ModelDescriptor, operation: str) -> None:
    if any(tensor.dtype not in POLICY_DTYPES for tensor in descriptor.tensors):
        raise ValueError(_dtype_refusal(operation))


def _adapter_pairs(descriptor: ModelDescriptor) -> dict:
    from .graph_planner import _adapter_tensor_shapes
    pairs = {}
    for module, pair in _adapter_tensor_shapes(descriptor).items():
        if not module.startswith(PEFT_PREFIX) or any(
                tensor.name != f"{module}.lora_{letter}.weight" for letter, tensor in pair.items()):
            raise ValueError("adapter tensors must use PEFT base_model.model.<module>.lora_{A,B}.weight names")
        pairs[module[len(PEFT_PREFIX):]] = pair
    return pairs


def _check_base_dimensions(descriptor: ModelDescriptor, inventory: dict, message: str) -> dict:
    pairs = _adapter_pairs(descriptor)
    for module, pair in pairs.items():
        tensor = inventory.get(module + ".weight")
        if tensor is None or tensor.shape != (pair["B"].shape[0], pair["A"].shape[1]):
            raise ValueError(message)
    return pairs


def validate_materialize_input(load: StagedLoad) -> None:
    from .graph_planner import _materializable
    base, adapter = load.base, load.adapter
    descriptor = adapter.descriptor
    if descriptor is None or descriptor.storage_kind != "adapter":
        raise ValueError("materialize requires a causal LoRA/QLoRA adapter without value head")
    _check_adapter_local(adapter)
    if base.identity != descriptor.effective_base:
        raise ValueError("materialize base differs from adapter effective base")
    _materializable(descriptor)
    _linear_layout(descriptor)
    _check_policy_dtypes(descriptor, "materialization")
    inventory = _verified_base(base, "materialization")
    if (base.config_sha256, base.tokenizer_fingerprint) != (descriptor.config_sha256,
                                                             descriptor.tokenizer_fingerprint):
        raise ValueError("adapter/base config or tokenizer differs")
    _check_base_dimensions(descriptor, inventory, "adapter dimensions differ from inspected effective base")


def _check_adapter_files(model: LocalModel) -> int:
    """PEFT loads the local directory: its loader config and tensors must equal the verified descriptor.

    The (small) adapter tensors are scanned for NaN/inf with the stdlib before any heavy import.
    Returns the number of tensors checked.
    """
    descriptor = model.descriptor
    directory = Path(model.path).absolute()
    _validate_adapter(directory, descriptor.adapter)
    for file in sorted({tensor.file for tensor in descriptor.tensors}):
        expected = {tensor for tensor in descriptor.tensors if tensor.file == file}
        if set(_tensor_header(directory / file, file)) != expected:
            raise ValueError("tensor inventory geometry does not match snapshot")
        scan_finite_safetensors(directory / file)
    return len(descriptor.tensors)


# -- adapter metadata from a PEFT export ---------------------------------------------------------

# PEFT keeps these as sets and serializes them in PYTHONHASHSEED-dependent order.
_SET_VALUED_ADAPTER_FIELDS = ("target_modules", "modules_to_save", "exclude_modules", "target_parameters")


def write_adapter_config(path: Path, config: Mapping, repo_id: str) -> None:
    """Write a PEFT adapter config with deterministic bytes (sorted set-valued lists, LF, sorted keys).

    ``base_model_name_or_path`` becomes the root repository ID: provenance only, never a host path.
    """
    config = dict(config)
    for key in _SET_VALUED_ADAPTER_FIELDS:
        if isinstance(config.get(key), (list, tuple)):
            config[key] = sorted(config[key])
    config["base_model_name_or_path"] = repo_id
    Path(path).write_bytes(json.dumps(config, indent=2, sort_keys=True).encode("utf-8"))


def adapter_metadata_from_config(config: Mapping, *, method: Literal["lora", "qlora"],
                                 quantization_bit: Literal[4, 8] | None = None,
                                 loraplus_lr_ratio: float | None = None,
                                 loraplus_lr_embedding: float = 1e-6) -> AdapterMetadata:
    """Effective loader metadata of an actual PEFT LoRA export, with sorted target modules."""
    if not isinstance(config, Mapping) or config.get("peft_type") != "LORA" or method not in {"lora", "qlora"}:
        raise ValueError("adapter metadata requires a LoRA export")
    targets = config.get("target_modules")
    if not isinstance(targets, (list, tuple)) or not all(isinstance(item, str) for item in targets):
        raise ValueError("adapter export requires an explicit target module list")
    initialization = config.get("init_lora_weights", True)
    pissa = re.fullmatch(r"pissa_niter_([0-9]+)", initialization) if isinstance(initialization, str) else None
    if initialization is True:
        pissa_init, pissa_iter = False, 16
    elif initialization == "pissa":
        pissa_init, pissa_iter = True, -1
    elif pissa:
        pissa_init, pissa_iter = True, int(pissa.group(1))
    else:
        raise ValueError("unsupported adapter initialization")
    return AdapterMetadata(
        method=method, rank=config.get("r"), alpha=config.get("lora_alpha"),
        target_modules=tuple(sorted(targets)), dropout=config.get("lora_dropout", 0),
        fan_in_fan_out=config.get("fan_in_fan_out", False), task_type=config.get("task_type"),
        inference_mode=config.get("inference_mode", False), use_rslora=config.get("use_rslora", False),
        use_dora=config.get("use_dora", False), pissa_init=pissa_init, pissa_iter=pissa_iter,
        quantization_bit=quantization_bit, bias=config.get("bias", "none"),
        modules_to_save=tuple(sorted(config.get("modules_to_save") or ())),
        rank_pattern=tuple(RankPattern(module=key, rank=value)
                           for key, value in sorted((config.get("rank_pattern") or {}).items())),
        alpha_pattern=tuple(AlphaPattern(module=key, alpha=value)
                            for key, value in sorted((config.get("alpha_pattern") or {}).items())),
        target_parameters=tuple(sorted(config.get("target_parameters") or ())),
        layer_replication=tuple(tuple(pair) for pair in config.get("layer_replication") or ()),
        loraplus_lr_ratio=loraplus_lr_ratio, loraplus_lr_embedding=loraplus_lr_embedding)


# -- numerics (heavy imports only below this line, inside functions) ------------------------------

def _device(torch, device: str):
    if device != "cpu" and not re.fullmatch(r"cuda(:[0-9]+)?", device):
        raise ValueError("model operations run on cpu or cuda only")
    if device.startswith("cuda"):
        torch.backends.cuda.matmul.allow_tf32 = False
        torch.backends.cudnn.allow_tf32 = False
    return torch.device(device)


def _load_fp32(torch, transformers, path: Path, device):
    model = transformers.AutoModelForCausalLM.from_pretrained(
        str(path), dtype=torch.float32, local_files_only=True, use_safetensors=True)
    return model.to(device).eval()


def _logits(torch, model, ids, device):
    with torch.no_grad():
        tokens = torch.tensor([list(ids)], dtype=torch.long, device=device)
        output = model(input_ids=tokens, attention_mask=torch.ones_like(tokens))
    return output.logits.detach().to("cpu", torch.float64)


def _probe_ids(transformers, base_path: Path, vocab_size: int, probe_ids) -> tuple[int, ...]:
    if probe_ids is None:
        tokenizer = transformers.AutoTokenizer.from_pretrained(str(base_path), local_files_only=True)
        ids = list(tokenizer.encode(PROBE_TEXT, add_special_tokens=False))
        if len(ids) < 4:
            if tokenizer.eos_token_id is None:
                raise ValueError("numerical probe requires an eos token or explicit probe ids")
            ids += [tokenizer.eos_token_id] * (4 - len(ids))
        ids = ids[:32]
    else:
        ids = list(probe_ids)
    if not 1 <= len(ids) <= 32 or any(type(item) is not int or not 0 <= item < vocab_size for item in ids):
        raise ValueError("numerical probe ids must be 1-32 token ids inside the model vocabulary")
    return tuple(ids)


def _torch_scan(torch, directory: Path) -> int:
    from safetensors import safe_open
    checked = 0
    for relative in _weight_files(directory):
        with safe_open(str(directory / relative), framework="pt") as handle:
            for name in handle.keys():
                tensor = handle.get_tensor(name)
                if tensor.is_floating_point() and not bool(torch.isfinite(tensor).all()):
                    raise ValueError(f"non-finite tensor values in {relative}: {name}")
                checked += 1
    return checked


def _load_tensors(directory: Path) -> dict:
    from safetensors.torch import load_file
    tensors = {}
    for relative in _weight_files(directory):
        tensors.update(load_file(str(directory / relative), device="cpu"))
    return tensors


def _close(torch, actual, expected, atol: float, rtol: float) -> tuple[bool, float]:
    error = float((actual - expected).abs().max())
    return bool(torch.allclose(actual, expected, atol=atol, rtol=rtol)) and math.isfinite(error), error


def _require_f32(directory: Path) -> None:
    if any(tensor.dtype != "F32" for tensor in _header_inventory(directory).values()):
        raise ValueError("model-operation output tensors must be stored as F32")


def _check_output(output) -> Path:
    output = Path(output).absolute()
    if output.exists() or output.is_symlink():
        raise ValueError("model-operation output already exists")
    _check_ancestors(output.parent)
    if not output.parent.is_dir():
        raise ValueError("model-operation output parent directory is missing")
    return output


def _copy_snapshot_files(source: Path, target: Path, *, config: bool) -> None:
    """Copy tokenizer files (and the adapter's config.json) byte-for-byte from the effective base."""
    for relative, file in _files(source):
        if is_tokenizer_file(relative) or config and relative == "config.json":
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.unlink(missing_ok=True)
            with _open_regular(file) as reader, destination.open("xb") as writer:
                shutil.copyfileobj(reader, writer, length=1024 * 1024)


def _output_files(directory: Path) -> tuple[str, ...]:
    return tuple(relative for relative, _ in _files(directory))


def _expected_deltas(torch, models, weights, scales) -> dict:
    expected = {}
    for model, weight, scale in zip(models, weights, scales):
        tensors = _load_tensors(Path(model.path).absolute())
        for module in _adapter_pairs(model.descriptor):
            a = tensors[f"{PEFT_PREFIX}{module}.lora_A.weight"].to(torch.float64)
            b = tensors[f"{PEFT_PREFIX}{module}.lora_B.weight"].to(torch.float64)
            expected[module] = expected.get(module, 0) + weight * scale * (b @ a)
    return expected


def merge_adapters(inputs: Sequence[LocalModel], weights: Sequence[float], output: Path, *,
                   base: LocalModel, policy: str = POLICY_F32_V1, device: str = "cpu",
                   require_real: bool = True, probe_ids: Sequence[int] | None = None) -> MergeReceipt:
    """FP32 PEFT ``cat`` merge of compatible LoRA adapters into a new rank-sum adapter."""
    tolerance = _policy(policy)
    inputs = tuple(inputs)
    for model in inputs:
        _check_adapter_local(model)
    plan = validate_merge_inputs([model.identity for model in inputs], [model.descriptor for model in inputs],
                                 weights, require_real=require_real)
    first = inputs[0].descriptor
    for model in inputs:
        _check_policy_dtypes(model.descriptor, "merge")
    if base.identity != plan.effective_base:
        raise ValueError("merge input effective bases/tokenizers/targets differ")
    inventory = _verified_base(base, "merge")
    if (base.config_sha256, base.tokenizer_fingerprint) != (first.config_sha256, first.tokenizer_fingerprint):
        raise ValueError("merge input effective bases/tokenizers/targets differ")
    for model in inputs:
        _check_base_dimensions(model.descriptor, inventory, "merge base tensor dimensions differ")
    output = _check_output(output)
    checked = sum(_check_adapter_files(model) for model in inputs)

    torch, peft, safetensors, transformers = model_ops_runtime()
    target = _device(torch, device)
    base_path = Path(base.path).absolute()
    checked += _torch_scan(torch, base_path)
    model = peft.PeftModel.from_pretrained(_load_fp32(torch, transformers, base_path, target),
                                           str(Path(inputs[0].path).absolute()), adapter_name="a0")
    for index, item in enumerate(inputs[1:], start=1):
        model.load_adapter(str(Path(item.path).absolute()), adapter_name=f"a{index}")
    names = [f"a{index}" for index in range(len(inputs))]
    model.add_weighted_adapter(names, list(plan.normalized_weights), "merged", combination_type="cat")
    staging = Path(tempfile.mkdtemp(prefix=".model-op-", dir=output.parent))
    try:
        # Embedding/vocabulary LoRA is refused above, so PEFT's "auto" vocabulary probe (which may query
        # the HF Hub for base_model_name_or_path) is disabled to keep the operation hermetic.
        model.save_pretrained(str(staging / "export"), selected_adapters=["merged"], safe_serialization=True,
                              save_embedding_layers=False)
        del model
        payload = staging / "payload"
        payload.mkdir()
        for file in sorted((staging / "export" / "merged").iterdir()):
            if file.name != "README.md":
                file.rename(payload / file.name)
        write_adapter_config(payload / "adapter_config.json", _read_json(payload / "adapter_config.json"),
                             first.root.repo_id)
        config = _read_json(payload / "adapter_config.json")
        _copy_snapshot_files(base_path, payload, config=True)
        _require_f32(payload)
        if (config.get("r"), config.get("lora_alpha"), sorted(config.get("target_modules") or ())) != (
                plan.output_rank, plan.output_rank, list(plan.target_modules)):
            raise ValueError(DELTA_FAILED)
        checked += _torch_scan(torch, payload)
        delta_error, logits_error = _verify_merge(torch, peft, transformers, inputs, plan, base_path, payload,
                                                  target, tolerance, probe_ids)
        files = _output_files(payload)
        payload.rename(output)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return MergeReceipt(policy_id=tolerance.policy_id, inputs=plan.inputs, normalized_weights=plan.normalized_weights,
                        output_rank=plan.output_rank, max_abs_delta_error=delta_error,
                        max_abs_logits_error=logits_error, finite_tensors_checked=checked, output_files=files)


def _verify_merge(torch, peft, transformers, inputs, plan, base_path, payload, device, tolerance, probe_ids):
    expected = _expected_deltas(torch, inputs, plan.normalized_weights, plan.scales)
    merged = _load_tensors(payload)
    names = {f"{PEFT_PREFIX}{module}.lora_{letter}.weight" for module in expected for letter in "AB"}
    if set(merged) != names:
        raise ValueError(DELTA_FAILED)
    delta_error = 0.0
    for module, wanted in expected.items():
        a = merged[f"{PEFT_PREFIX}{module}.lora_A.weight"].to(torch.float64)
        b = merged[f"{PEFT_PREFIX}{module}.lora_B.weight"].to(torch.float64)
        ok, error = _close(torch, b @ a, wanted, tolerance.delta_atol, tolerance.delta_rtol)  # scaling is 1
        delta_error = max(delta_error, error)
        if not ok or a.shape[0] != plan.output_rank:
            raise ValueError(DELTA_FAILED)
    reference = _load_fp32(torch, transformers, base_path, device)
    ids = _probe_ids(transformers, base_path, reference.get_input_embeddings().weight.shape[0], probe_ids)
    with torch.no_grad():
        for module, wanted in expected.items():
            weight = reference.get_submodule(module).weight
            weight.copy_((weight.to(torch.float64) + wanted.to(weight.device)).to(torch.float32))
    wanted_logits = _logits(torch, reference, ids, device)
    del reference
    reloaded = peft.PeftModel.from_pretrained(_load_fp32(torch, transformers, base_path, device), str(payload))
    ok, logits_error = _close(torch, _logits(torch, reloaded.eval(), ids, device), wanted_logits,
                              tolerance.logits_atol, tolerance.logits_rtol)
    if not ok:
        raise ValueError(LOGITS_FAILED)
    return delta_error, logits_error


def materialize_model(input: StagedLoad, output: Path, *, policy: str = POLICY_F32_V1,
                      device: str = "cpu", probe_ids: Sequence[int] | None = None) -> MaterializeReceipt:
    """Merge one LoRA/QLoRA adapter into its exact unquantized effective base; store F32."""
    tolerance = _policy(policy)
    load = input
    validate_materialize_input(load)
    output = _check_output(output)
    checked = _check_adapter_files(load.adapter)

    torch, peft, safetensors, transformers = model_ops_runtime()
    target = _device(torch, device)
    base_path = Path(load.base.path).absolute()
    adapter_path = Path(load.adapter.path).absolute()
    checked += _torch_scan(torch, base_path)
    model = peft.PeftModel.from_pretrained(_load_fp32(torch, transformers, base_path, target), str(adapter_path))
    model.eval()
    ids = _probe_ids(transformers, base_path, model.get_input_embeddings().weight.shape[0], probe_ids)
    before = _logits(torch, model, ids, target)
    merged = model.merge_and_unload(safe_merge=True)
    staging = Path(tempfile.mkdtemp(prefix=".model-op-", dir=output.parent))
    try:
        payload = staging / "payload"
        merged.save_pretrained(str(payload), safe_serialization=True)
        del merged, model
        (payload / "README.md").unlink(missing_ok=True)
        _copy_snapshot_files(base_path, payload, config=False)
        _require_f32(payload)
        if _local_tokenizer_fingerprint(payload) != load.base.tokenizer_fingerprint:
            raise ValueError("tokenizer snapshot fingerprint mismatch")
        checked += _torch_scan(torch, payload)
        delta_error = _verify_materialized(torch, load.adapter, base_path, payload, tolerance)
        reloaded = _load_fp32(torch, transformers, payload, target)
        ok, logits_error = _close(torch, _logits(torch, reloaded, ids, target), before,
                                  tolerance.logits_atol, tolerance.logits_rtol)
        del reloaded
        if not ok:
            raise ValueError(LOGITS_FAILED)
        files = _output_files(payload)
        payload.rename(output)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return MaterializeReceipt(policy_id=tolerance.policy_id, base=load.base.identity, adapter=load.adapter.identity,
                              max_abs_delta_error=delta_error, max_abs_logits_error=logits_error,
                              non_target_tensors_identical=True, tokenizer_fingerprint=load.base.tokenizer_fingerprint,
                              finite_tensors_checked=checked, output_files=files)


def _verify_materialized(torch, adapter: LocalModel, base_path: Path, payload: Path, tolerance) -> float:
    base, result = _load_tensors(base_path), _load_tensors(payload)
    if set(base) != set(result):
        raise ValueError(DELTA_FAILED)
    metadata = adapter.descriptor.adapter
    expected = _expected_deltas(torch, (adapter,), (1.0,), (metadata.alpha / metadata.rank,))
    targets = {module + ".weight": delta for module, delta in expected.items()}
    delta_error = 0.0
    for name, tensor in result.items():
        original = base[name].to(torch.float32)
        if name in targets:
            ok, error = _close(torch, tensor.to(torch.float64) - original.to(torch.float64), targets[name],
                               tolerance.delta_atol, tolerance.delta_rtol)
            delta_error = max(delta_error, error)
            if not ok:
                raise ValueError(DELTA_FAILED)
        elif not torch.equal(tensor, original):
            raise ValueError("materialized non-target tensors differ from the effective base")
    return delta_error
