"""Exact model operations: compatible LoRA ``cat`` merge and full-model materialization.

Importing this module touches only the standard library, pydantic and local contracts. torch,
PEFT, Transformers and safetensors are imported inside the numerical functions, after every input
refusal and after the stdlib finite scan of the (small) adapter inputs. The scanner lives in
``safetensors_scan`` and the target-module inspector/verifier in ``target_inspection``; both are
re-exported here. Policy ``model-ops-f32-v1`` computes in FP32 from an exact upcast of
the inputs and stores every output tensor as F32. Inputs are ``LocalModel`` directories that the
caller has already verified against their identity; the checks here repeat the cheap ones. The
adapter conversion rules are graph_planner's public ``check_*`` rules, so planning and the worker agree.
Outputs are staged in ``.model-op-*`` under the output's parent (the worker gives each attempt its own
exclusive parent) and published by ``publish_output``: every file and directory is fsynced, and the
move never replaces an existing path. transformers' platform-newline JSON in a materialized model is
normalized to LF first, so the output inventory is the same on Windows and Linux.
The continuation helpers at the end decide whether a child training run may resume a parent adapter.
"""
from __future__ import annotations

import errno
import hashlib
import json
import math
import os
import re
import shutil
import stat
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
    _json,
    _not_link,
    _open_regular,
    _read_json,
    _tensor_header,
    _tokenizer_hash,
    _validate_adapter,
    fsync_directory,
    is_processor_file,
    is_tokenizer_file,
)
from .runtime_profiles import model_ops_runtime
from .safetensors_scan import scan_finite_safetensors
from .target_inspection import (
    COMPOSITE_MODEL_TYPES,
    inspect_linear_modules,
    inspect_target_modules,
    unloaded_prefixes,
    verify_inspected_targets,
)

__all__ = [
    "CONTINUATION_REFUSAL", "EXPORT_ONLY_ADAPTER_FIELDS", "POLICY_F32_V1", "TOLERANCE_POLICIES", "LocalModel",
    "MaterializeReceipt", "MergePlan", "MergeReceipt", "StagedLoad", "TolerancePolicy",
    "adapter_metadata_from_config", "check_continuation", "check_continuation_targets",
    "check_full_model_directory", "continuation_mismatches", "expected_continuation_adapter",
    "inspect_linear_modules", "inspect_target_modules", "local_config_sha256", "local_tokenizer_fingerprint",
    "materialize_model", "merge_adapters", "normalize_json_newlines", "publish_output", "scan_finite_safetensors",
    "validate_materialize_input", "validate_merge_inputs", "verify_inspected_targets", "write_adapter_config",
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
SINGLE_WEIGHTS = "model.safetensors"
# transformers loads config.transformers_weights instead of model.safetensors/the index (modeling_utils).
WEIGHT_REDIRECT = "config.json transformers_weights redirects the weight loader; refusing"
OUTPUT_EXISTS = "model-operation output already exists"
# JSON that transformers writes in text mode, i.e. with platform newlines (CRLF on Windows).
TRANSFORMERS_JSON = ("config.json", "generation_config.json", "model.safetensors.index.json")
AT_FDCWD, RENAME_NOREPLACE = -100, 1   # Linux renameat2(2)


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


# -- input validation (no torch) -----------------------------------------------------------------

def validate_merge_inputs(refs, descriptors, weights, *, require_real: bool = True) -> MergePlan:
    """Reuse graph compilation's conversion rules; only the real-only rule is relaxable."""
    from .graph_planner import check_merge_descriptors
    from .graph_schema import MergeNode

    refs = tuple(ModelArtifactRef.model_validate(ref) for ref in refs)
    descriptors = tuple(ModelDescriptor.model_validate(item) for item in descriptors)
    if len(refs) != len(descriptors):
        raise ValueError("merge requires one verified descriptor for each input")
    # Includes fan_in_fan_out: the numerical delta is B @ A for torch.nn.Linear (out, in) weights only.
    check_merge_descriptors(refs, descriptors, require_real=require_real)
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


def local_config_sha256(directory: Path) -> str:
    """SHA256 of a local model's config.json, read as a regular non-link file."""
    with _open_regular(Path(directory).absolute() / "config.json") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def local_tokenizer_fingerprint(directory: Path) -> str:
    """model_artifacts' tokenizer fingerprint, hashing only the tokenizer files (never multi-GB weights).

    Other files (weights, an HF ``local_dir`` download's ``.cache/huggingface/`` metadata) are walked for
    links but not hashed, so an HFRootRef fingerprint is computed on exactly the tree that is bound.
    """
    entries = []
    for relative, file in _files(directory):
        if is_tokenizer_file(relative):
            with _open_regular(file) as stream:
                checksum = hashlib.file_digest(stream, "sha256").hexdigest()
            entries.append(_FileEntry(path=relative, size=file.stat().st_size, sha256=checksum))
    return _tokenizer_hash(tuple(entries))


def _weight_layout(directory: Path, *, adapter: bool) -> tuple[tuple[str, ...], dict | None]:
    """The safetensors files a loader reads.

    Adapter directories: every top-level ``*.safetensors``. Full models: exactly one of
    ``model.safetensors`` or the shard index (transformers 5.6 loads ``model.safetensors`` first, so having
    both is ambiguous). Other top-level copies (for example Mistral's consolidated.safetensors) are ignored,
    as loaders do.
    """
    names = {relative for relative, _ in _files(directory) if "/" not in relative}
    if adapter:
        return tuple(sorted(name for name in names if name.endswith(".safetensors"))), None
    if SHARD_INDEX in names and SINGLE_WEIGHTS in names:
        raise ValueError("ambiguous single and sharded model weights")
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
    if SINGLE_WEIGHTS in names:
        return (SINGLE_WEIGHTS,), None
    raise ValueError("full model weights require model.safetensors or a shard index")


def _weight_files(directory: Path, *, adapter: bool) -> tuple[str, ...]:
    return _weight_layout(directory, adapter=adapter)[0]


def _header_inventory(directory: Path, *, adapter: bool) -> dict:
    files, weight_map = _weight_layout(directory, adapter=adapter)
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
    _check_loader_config(config)
    with _open_regular(base.path / "config.json") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != base.config_sha256:
            raise ValueError("model config snapshot fingerprint mismatch")
    if local_tokenizer_fingerprint(base.path) != base.tokenizer_fingerprint:
        raise ValueError("tokenizer snapshot fingerprint mismatch")
    inventory = _header_inventory(base.path, adapter=False)
    if base.descriptor is not None and set(inventory.values()) != set(base.descriptor.tensors):
        raise ValueError("tensor inventory geometry does not match snapshot")
    if any(tensor.dtype not in POLICY_DTYPES for tensor in inventory.values()):
        raise ValueError(_dtype_refusal(operation))
    return inventory


def _check_loader_config(config: dict) -> None:
    if "transformers_weights" in config:
        raise ValueError(WEIGHT_REDIRECT)


def check_full_model_directory(directory: Path) -> dict:
    """Loader-relevant checks of a local full-model directory; returns its config.json.

    The tree must be link-free, config.json must not redirect the weight loader, and the safetensors
    layout must be unambiguous with headers that cover the index. No tensor data is read.
    """
    directory = Path(directory).absolute()
    _check_ancestors(directory)
    _files(directory)
    config = _read_json(directory / "config.json")
    if not isinstance(config, dict):
        raise ValueError("invalid model config.json")
    _check_loader_config(config)
    _header_inventory(directory, adapter=False)
    return config


def _check_policy_dtypes(descriptor: ModelDescriptor, operation: str) -> None:
    if any(tensor.dtype not in POLICY_DTYPES for tensor in descriptor.tensors):
        raise ValueError(_dtype_refusal(operation))


def _adapter_pairs(descriptor: ModelDescriptor) -> dict:
    from .graph_planner import adapter_tensor_shapes
    pairs = {}
    for module, pair in adapter_tensor_shapes(descriptor).items():
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
    from .graph_planner import check_materializable
    base, adapter = load.base, load.adapter
    descriptor = adapter.descriptor
    if descriptor is None or descriptor.storage_kind != "adapter":
        raise ValueError("materialize requires a causal LoRA/QLoRA adapter without value head")
    _check_adapter_local(adapter)
    if base.identity != descriptor.effective_base:
        raise ValueError("materialize base differs from adapter effective base")
    check_materializable(descriptor)   # includes fan_in_fan_out: delta = B @ A for Linear weights only
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
    """FP32 load; composite models through their image-text class, so the vision/audio towers stay in the model
    and adapters recorded with full language-model names (LLaMA-Factory's) apply."""
    composite = _read_json(path / "config.json").get("model_type") in COMPOSITE_MODEL_TYPES
    loader = transformers.AutoModelForImageTextToText if composite else transformers.AutoModelForCausalLM
    model = loader.from_pretrained(str(path), dtype=torch.float32, local_files_only=True, use_safetensors=True)
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


def _torch_scan(torch, directory: Path, *, adapter: bool) -> int:
    from safetensors import safe_open
    checked = 0
    for relative in _weight_files(directory, adapter=adapter):
        with safe_open(str(directory / relative), framework="pt") as handle:
            for name in handle.keys():
                tensor = handle.get_tensor(name)
                if tensor.is_floating_point() and not bool(torch.isfinite(tensor).all()):
                    raise ValueError(f"non-finite tensor values in {relative}: {name}")
                checked += 1
    return checked


def _load_tensors(directory: Path, *, adapter: bool) -> dict:
    from safetensors.torch import load_file
    tensors = {}
    for relative in _weight_files(directory, adapter=adapter):
        tensors.update(load_file(str(directory / relative), device="cpu"))
    return tensors


def _close(torch, actual, expected, atol: float, rtol: float) -> tuple[bool, float]:
    error = float((actual - expected).abs().max())
    return bool(torch.allclose(actual, expected, atol=atol, rtol=rtol)) and math.isfinite(error), error


def _require_f32(directory: Path, *, adapter: bool) -> None:
    if any(tensor.dtype != "F32" for tensor in _header_inventory(directory, adapter=adapter).values()):
        raise ValueError("model-operation output tensors must be stored as F32")


def _check_output(output) -> Path:
    output = Path(output).absolute()
    if output.exists() or output.is_symlink():
        raise ValueError(OUTPUT_EXISTS)
    _check_ancestors(output.parent)
    if not output.parent.is_dir():
        raise ValueError("model-operation output parent directory is missing")
    return output


# -- durable, never-replacing publication of a finished output (stdlib only) ----------------------

def normalize_json_newlines(directory: Path, names: Sequence[str] = TRANSFORMERS_JSON) -> None:
    """Rewrite transformers' platform-newline JSON files with LF bytes, so output bytes match across hosts.

    Only the named top-level files are touched (never tokenizer files, which are base bytes), and the
    parsed JSON must be identical before and after.
    """
    directory = Path(directory).absolute()
    for name in names:
        path = directory / name
        if not (path.exists() or path.is_symlink()):
            continue
        with _open_regular(path) as stream:
            raw = stream.read()
        normalized = raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        if normalized == raw:
            continue
        try:
            unchanged = _json(raw) == _json(normalized)
        except ValueError as exc:
            raise ValueError(f"invalid JSON in {name}; refusing newline normalization") from exc
        if not unchanged:
            raise ValueError(f"JSON newline normalization would change {name}")
        with path.open("r+b") as stream:
            stream.write(normalized)
            stream.truncate()


def _fsync_file(path: Path) -> None:
    with Path(path).open("r+b") as stream:   # Windows commits only handles opened for writing
        os.fsync(stream.fileno())


_fsync_directory = fsync_directory   # module-level name, so tests can observe the order of fsyncs


def _renameat2_noreplace(source: Path, destination: Path, *, function=None) -> bool:
    """Atomic Linux rename that refuses any existing destination; False when unsupported here."""
    import ctypes
    if function is None:
        try:
            function = getattr(ctypes.CDLL(None, use_errno=True), "renameat2", None)
        except (OSError, TypeError):
            function = None
        if function is None:
            return False
        function.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
        function.restype = ctypes.c_int
    if function(AT_FDCWD, os.fsencode(source), AT_FDCWD, os.fsencode(destination), RENAME_NOREPLACE) == 0:
        return True
    code = ctypes.get_errno()
    if code in {errno.ENOSYS, errno.EINVAL, getattr(errno, "ENOTSUP", errno.EINVAL),
                getattr(errno, "EOPNOTSUPP", errno.EINVAL)}:
        return False   # kernel or filesystem without RENAME_NOREPLACE: use the portable move
    raise OSError(code, os.strerror(code), str(destination))


def _move_tree_noreplace(source: Path, destination: Path) -> None:
    """Portable no-replace move: create each directory exclusively and hard-link each file, then unlink.

    ``destination`` is created here (FileExistsError for any existing path, even an empty directory), so
    on any failure it is ours to remove: a half-filled output is never left behind.
    """
    source, destination = Path(source), Path(destination)
    os.mkdir(destination)
    try:
        _link_tree(source, destination)
    except BaseException:
        shutil.rmtree(destination, ignore_errors=True)
        raise


def _link_tree(source: Path, destination: Path) -> None:
    for child in sorted(source.iterdir()):
        target = destination / child.name
        if stat.S_ISDIR(_not_link(child).st_mode):
            os.mkdir(target)
            _link_tree(child, target)
        else:
            os.link(child, target)   # refuses an existing target, unlike rename
            os.unlink(child)
    _fsync_directory(destination)
    os.rmdir(source)


def _rename_noreplace(source: Path, destination: Path) -> None:
    if os.name == "nt":
        os.rename(source, destination)   # MoveFileExW without REPLACE_EXISTING refuses any existing path
    elif not _renameat2_noreplace(source, destination):
        _move_tree_noreplace(source, destination)


def publish_output(payload: Path, output: Path) -> None:
    """Durably move a finished payload to the new path ``output``; never replace an existing path.

    Every file and directory is fsynced before the move (as ``ModelArtifactStore.freeze`` does for its
    files) and the parent afterwards. The move itself refuses an existing destination, including an
    empty directory created after the check below.
    """
    payload, output = Path(payload).absolute(), Path(output).absolute()
    _check_ancestors(output.parent)
    if output.exists() or output.is_symlink():
        raise ValueError(OUTPUT_EXISTS)
    files = _files(payload)   # link-free regular files only
    for _, file in files:
        _fsync_file(file)
    directories = {payload, *(path for path in payload.rglob("*") if path.is_dir())}
    for directory in sorted(directories, key=lambda path: len(path.parts), reverse=True):
        _fsync_directory(directory)
    try:
        _rename_noreplace(payload, output)
    except FileExistsError as exc:
        raise ValueError(OUTPUT_EXISTS) from exc
    _fsync_directory(output.parent)


def _copy_snapshot_files(source: Path, target: Path, *, config: bool) -> None:
    """Copy tokenizer and processor files (and the adapter's config.json) byte-for-byte from the effective base."""
    for relative, file in _files(source):
        if is_tokenizer_file(relative) or is_processor_file(relative) or config and relative == "config.json":
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
        tensors = _load_tensors(Path(model.path).absolute(), adapter=True)
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
    checked += _torch_scan(torch, base_path, adapter=False)
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
        _require_f32(payload, adapter=True)
        if (config.get("r"), config.get("lora_alpha"), sorted(config.get("target_modules") or ())) != (
                plan.output_rank, plan.output_rank, list(plan.target_modules)):
            raise ValueError(DELTA_FAILED)
        checked += _torch_scan(torch, payload, adapter=True)
        delta_error, logits_error = _verify_merge(torch, peft, transformers, inputs, plan, base_path, payload,
                                                  target, tolerance, probe_ids)
        files = _output_files(payload)
        publish_output(payload, output)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return MergeReceipt(policy_id=tolerance.policy_id, inputs=plan.inputs, normalized_weights=plan.normalized_weights,
                        output_rank=plan.output_rank, max_abs_delta_error=delta_error,
                        max_abs_logits_error=logits_error, finite_tensors_checked=checked, output_files=files)


def _verify_merge(torch, peft, transformers, inputs, plan, base_path, payload, device, tolerance, probe_ids):
    expected = _expected_deltas(torch, inputs, plan.normalized_weights, plan.scales)
    merged = _load_tensors(payload, adapter=True)
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
    checked += _torch_scan(torch, base_path, adapter=False)
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
        # transformers wrote config.json/generation_config.json with platform newlines; the output
        # inventory must not depend on the host, so they become LF before anything is listed or hashed.
        normalize_json_newlines(payload)
        _copy_snapshot_files(base_path, payload, config=False)
        _require_f32(payload, adapter=False)
        if local_tokenizer_fingerprint(payload) != load.base.tokenizer_fingerprint:
            raise ValueError("tokenizer snapshot fingerprint mismatch")
        checked += _torch_scan(torch, payload, adapter=False)
        delta_error = _verify_materialized(torch, load.adapter, base_path, payload, tolerance)
        reloaded = _load_fp32(torch, transformers, payload, target)
        ok, logits_error = _close(torch, _logits(torch, reloaded, ids, target), before,
                                  tolerance.logits_atol, tolerance.logits_rtol)
        del reloaded
        if not ok:
            raise ValueError(LOGITS_FAILED)
        files = _output_files(payload)
        publish_output(payload, output)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return MaterializeReceipt(policy_id=tolerance.policy_id, base=load.base.identity, adapter=load.adapter.identity,
                              max_abs_delta_error=delta_error, max_abs_logits_error=logits_error,
                              non_target_tensors_identical=True, tokenizer_fingerprint=load.base.tokenizer_fingerprint,
                              finite_tensors_checked=checked, output_files=files)


def _verify_materialized(torch, adapter: LocalModel, base_path: Path, payload: Path, tolerance) -> float:
    skipped = unloaded_prefixes(_read_json(base_path / "config.json"))   # never loaded, so never saved
    base = {name: tensor for name, tensor in _load_tensors(base_path, adapter=False).items()
            if not name.startswith(skipped)}
    result = _load_tensors(payload, adapter=False)
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


# -- continuation of a parent adapter (shared by compilation, staging and the worker) -------------

# PEFT save state (plan F7): every export records inference_mode=True and task_type="CAUSAL_LM". They are not
# settings of the resumed adapter, so continuation ignores them; every other effective setting must match.
EXPORT_ONLY_ADAPTER_FIELDS = frozenset({"inference_mode", "task_type"})
CONTINUATION_REFUSAL = "parent adapter settings differ; explicit materialize is required"


def check_continuation_targets(lora_target: str, actual_targets: Sequence[str], evidence) -> None:
    """The child's requested targets, expanded over the inspected effective base with LLaMA-Factory's rule
    (``all`` = every inspected Linear module; plan F9), must select exactly the parent adapter's modules."""
    modules = {item.name for item in evidence.modules}
    if lora_target == "all":
        expected = modules
    else:
        wanted = set(lora_target.split(","))
        expected = {name for name in modules if name.split(".")[-1] in wanted}
    expanded = {name for name in modules
                if any(name == target or name.endswith("." + target) for target in actual_targets)}
    if expanded != expected or not expanded:
        raise ValueError("resolved adapter targets differ from requested targets")


def expected_continuation_adapter(config_or_recipe, actual_targets: Sequence[str]) -> AdapterMetadata:
    """The adapter metadata the child's settings would train, resolved like LLaMA-Factory 0.9.5.

    ``lora_alpha`` None resolves to ``2 * lora_rank``. ``actual_targets`` are the parent's own target names,
    which ``check_continuation_targets`` has matched against the child's request.
    """
    if config_or_recipe.training_method not in {"lora", "qlora"}:
        raise ValueError("adapter continuation requires LoRA/QLoRA")
    hyperparameters, options = config_or_recipe.hyperparameters, config_or_recipe.finetuning_options
    qlora = config_or_recipe.training_method == "qlora"
    return AdapterMetadata(
        method=config_or_recipe.training_method, rank=hyperparameters.lora_rank,
        alpha=options.lora_alpha or hyperparameters.lora_rank * 2, target_modules=tuple(sorted(actual_targets)),
        dropout=options.lora_dropout, use_rslora=options.use_rslora, use_dora=options.use_dora,
        pissa_init=options.pissa_init, pissa_iter=options.pissa_iter, pissa_convert=options.pissa_convert,
        quantization_bit=config_or_recipe.quantization_bit if qlora else None,
        loraplus_lr_ratio=options.loraplus_lr_ratio, loraplus_lr_embedding=options.loraplus_lr_embedding)


def continuation_mismatches(parent: AdapterMetadata, expected: AdapterMetadata) -> tuple[str, ...]:
    """Sorted names of the effective settings that differ, ignoring EXPORT_ONLY_ADAPTER_FIELDS.

    LoRA+ fields stay compared (plan §6 Q17): they are optimizer settings recorded with the parent.
    """
    actual = AdapterMetadata.model_validate(parent).model_dump()
    wanted = AdapterMetadata.model_validate(expected).model_dump()
    for values in (actual, wanted):
        values["target_modules"] = sorted(values["target_modules"])
    return tuple(sorted(name for name in actual
                        if name not in EXPORT_ONLY_ADAPTER_FIELDS and actual[name] != wanted[name]))


def check_continuation(config_or_recipe, parent: AdapterMetadata, evidence) -> AdapterMetadata:
    """Refuse resuming a parent adapter whose effective settings differ from the child's.

    LLaMA-Factory resumes the parent with the parent's own saved config and never compares it with the
    child's lora_rank/lora_alpha (plan F3), so any difference would otherwise be silently ignored.
    ``evidence`` is the inspected target inventory of the exact effective base.
    """
    if config_or_recipe.training_method not in {"lora", "qlora"}:
        raise ValueError("adapter continuation requires LoRA/QLoRA")
    if evidence is None:
        raise ValueError("adapter continuation requires inspected effective-base target/dimension evidence")
    parent = AdapterMetadata.model_validate(parent)
    check_continuation_targets(config_or_recipe.hyperparameters.lora_target, parent.target_modules, evidence)
    expected = expected_continuation_adapter(config_or_recipe, parent.target_modules)
    mismatches = continuation_mismatches(parent, expected)
    if mismatches:
        raise ValueError(f"{CONTINUATION_REFUSAL}: {', '.join(mismatches)}")
    return expected
