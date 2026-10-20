"""Immutable model snapshots and path-free, exact effective-model input contracts.

This module inspects safetensors headers with the standard library only. Numerical
finite-value/reload checks belong to the separately pinned model-operation runtime.
An HFRootRef records inspected metadata; it grants no download or transfer permission.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import stat
import struct
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .registry import canonical, digest

SHA256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Identifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")]
ModuleName = Annotated[str, Field(pattern=r"^[A-Za-z0-9_][A-Za-z0-9_.]*$", max_length=1024)]
Dimension = Annotated[int, Field(strict=True, ge=0)]
DTYPE_BYTES = {
    "BOOL": 1, "U8": 1, "I8": 1, "I16": 2, "U16": 2, "F16": 2, "BF16": 2,
    "I32": 4, "U32": 4, "F32": 4, "I64": 8, "U64": 8, "F64": 8,
    "F8_E4M3": 1, "F8_E5M2": 1, "F8_E8M0": 1,
}
JSON_LIMIT = 32 * 1024 * 1024
TOKENIZER_FILES = frozenset({
    "tokenizer.json", "tokenizer_config.json", "special_tokens_map.json", "added_tokens.json",
    "tokenizer.model", "spiece.model", "vocab.json", "merges.txt", "vocab.txt", "chat_template.jinja",
})


class ArtifactModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False, revalidate_instances="always")


class HFRootRef(ArtifactModel):
    repo_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}/[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$")
    revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    config_sha256: SHA256
    tokenizer_fingerprint: SHA256


class ModelArtifactRef(ArtifactModel):
    artifact_id: SHA256
    inventory_sha256: SHA256


BaseRef = HFRootRef | ModelArtifactRef


def _relative(value: str) -> str:
    # Reject Windows drive/ADS names and ambiguous normalization on every platform.
    if (not value or "\\" in value or ":" in value or value.startswith("/")
            or any(part in {"", ".", ".."} or part[-1:] in {" ", "."}
                   or re.search(r'[<>"|?*\x00-\x1f]', part)
                   or re.fullmatch(r"CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9]", part.split(".")[0], re.I)
                   for part in value.split("/"))):
        raise ValueError("invalid relative artifact path")
    return value


class TensorMetadata(ArtifactModel):
    file: str
    name: ModuleName
    shape: tuple[Dimension, ...]
    dtype: str

    _file_path = field_validator("file")(_relative)

    @field_validator("dtype")
    @classmethod
    def known_dtype(cls, value):
        if value not in DTYPE_BYTES:
            raise ValueError("unsupported safetensors dtype")
        return value


class RankPattern(ArtifactModel):
    module: ModuleName
    rank: int = Field(strict=True, ge=1)


class AlphaPattern(ArtifactModel):
    module: ModuleName
    alpha: float = Field(gt=0)


class AdapterMetadata(ArtifactModel):
    """Effective loader settings, with defaults resolved rather than omitted."""

    method: Literal["lora", "qlora", "oft"]
    rank: int = Field(strict=True, ge=0)
    alpha: float = Field(gt=0)
    target_modules: tuple[ModuleName, ...] = Field(min_length=1)
    dropout: float = Field(default=0, ge=0, lt=1)
    fan_in_fan_out: bool = Field(default=False, strict=True)
    task_type: Literal["CAUSAL_LM"] | None = None
    inference_mode: bool = Field(default=False, strict=True)
    use_rslora: bool = Field(default=False, strict=True)
    use_dora: bool = Field(default=False, strict=True)
    pissa_init: bool = Field(default=False, strict=True)
    pissa_iter: int = Field(default=16, strict=True, ge=-1)
    pissa_convert: bool = Field(default=False, strict=True)
    quantization_bit: Literal[4, 8] | None = None
    bias: Literal["none", "all", "lora_only"] = "none"
    modules_to_save: tuple[ModuleName, ...] = ()
    rank_pattern: tuple[RankPattern, ...] = ()
    alpha_pattern: tuple[AlphaPattern, ...] = ()
    target_parameters: tuple[ModuleName, ...] = ()
    layer_replication: tuple[tuple[Dimension, Dimension], ...] = ()
    oft_block_size: int = Field(default=32, strict=True, ge=0)
    module_dropout: float = Field(default=0, ge=0, lt=1)
    loraplus_lr_ratio: float | None = Field(default=None, gt=0)
    loraplus_lr_embedding: float = Field(default=1e-6, gt=0)

    @model_validator(mode="after")
    def effective_settings(self):
        if self.method != "oft" and self.rank < 1:
            raise ValueError("LoRA rank must be positive")
        if self.method == "qlora" and self.quantization_bit is None:
            raise ValueError("QLoRA requires effective quantization")
        if self.method != "qlora" and self.quantization_bit is not None:
            raise ValueError("only QLoRA records quantized adapter loading")
        if len(set(self.target_modules)) != len(self.target_modules):
            raise ValueError("duplicate target modules")
        return self


class ModelDescriptor(ArtifactModel):
    schema_version: Literal[1] = 1
    producer_job_id: Identifier
    producer_attempt_id: Identifier
    producer_manifest_sha256: SHA256
    storage_kind: Literal["adapter", "full_model"]
    semantic_kind: Literal["causal_lm", "reward_model"]
    has_value_head: bool = Field(strict=True)
    root: HFRootRef
    effective_base: BaseRef
    parents: tuple[ModelArtifactRef, ...] = ()
    config_sha256: SHA256
    tokenizer_fingerprint: SHA256
    quantization_bit: Literal[4, 8] | None = None
    adapter: AdapterMetadata | None = None
    tensors: tuple[TensorMetadata, ...] = Field(min_length=1)
    synthetic: bool = Field(default=False, strict=True)
    simulation: bool = Field(default=False, strict=True)

    @model_validator(mode="after")
    def consistent_contract(self):
        if (self.storage_kind == "adapter") != (self.adapter is not None):
            raise ValueError("adapter storage requires adapter metadata; full model cannot have it")
        if self.semantic_kind == "reward_model" and not self.has_value_head:
            raise ValueError("reward_model requires a value head")
        names = [tensor.name for tensor in self.tensors]
        if len(set(names)) != len(names):
            raise ValueError("duplicate tensor names")
        if isinstance(self.effective_base, HFRootRef) and self.effective_base != self.root:
            raise ValueError("HF effective base must be the exact root")
        if (isinstance(self.effective_base, ModelArtifactRef)
                and self.effective_base not in self.parents):
            raise ValueError("effective base artifact must be recorded as a parent")
        if len(set((ref.artifact_id, ref.inventory_sha256) for ref in self.parents)) != len(self.parents):
            raise ValueError("duplicate parent references")
        return self

    @property
    def eligible_for_real_use(self) -> bool:
        """Provenance eligibility only; this does not establish model quality."""
        return not (self.synthetic or self.simulation)


class ModelLoadRef(ArtifactModel):
    base: BaseRef
    adapter: ModelArtifactRef | None = None


class ModelInput(ArtifactModel):
    policy: ModelLoadRef
    initialization: Literal["new_adapter", "continue_adapter", "full_weights"]
    frozen_reference: ModelLoadRef | None = None
    reward_model: ModelLoadRef | None = None

    @model_validator(mode="after")
    def initialization_matches_loader(self):
        if (self.initialization == "continue_adapter") != (self.policy.adapter is not None):
            raise ValueError("only continue_adapter requires a policy adapter")
        return self


class _FileEntry(ArtifactModel):
    path: str
    size: int = Field(strict=True, ge=0)
    sha256: SHA256

    _file_path = field_validator("path")(_relative)


def _not_link(path: Path):
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise ValueError("artifact symlink/reparse point is forbidden")
    return info


def _check_ancestors(path: Path):
    for current in (path, *path.parents):
        if current.exists() or current.is_symlink():
            _not_link(current)


def check_link_free(path: str | Path) -> None:
    """Refuse a symlink or reparse point at ``path`` or at any existing ancestor."""
    _check_ancestors(Path(path).absolute())


@contextmanager
def _open_regular(path: Path):
    before = _not_link(path)
    if not stat.S_ISREG(before.st_mode):
        raise ValueError("artifact payload must contain regular files")
    with path.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        if (opened.st_ino, opened.st_dev) != (before.st_ino, before.st_dev):
            raise ValueError("artifact file changed while opening")
        yield stream
        after = _not_link(path)
        if ((after.st_ino, after.st_dev, after.st_size, after.st_mtime_ns)
                != (before.st_ino, before.st_dev, before.st_size, before.st_mtime_ns)):
            raise ValueError("artifact file changed while reading")


def _files(directory: Path):
    _check_ancestors(directory)
    if not directory.is_dir():
        raise ValueError("artifact source is not a directory")
    result = []

    def visit(parent):
        for file in sorted(parent.iterdir()):
            info = _not_link(file)
            relative = _relative(file.relative_to(directory).as_posix())
            if stat.S_ISDIR(info.st_mode):
                visit(file)
            elif stat.S_ISREG(info.st_mode):
                result.append((relative, file))
            else:
                raise ValueError("artifact payload must contain regular files")

    visit(directory)
    paths = [name.casefold() for name, _ in result]
    if len(paths) != len(set(paths)):
        raise ValueError("artifact paths collide on Windows")
    return sorted(result)


def _inventory(directory: Path) -> tuple[_FileEntry, ...]:
    entries = []
    for relative, file in _files(directory):
        with _open_regular(file) as stream:
            checksum = hashlib.file_digest(stream, "sha256").hexdigest()
        entries.append(_FileEntry(path=relative, size=file.stat().st_size, sha256=checksum))
    return tuple(entries)


def _inventory_bytes(inventory) -> bytes:
    return canonical([entry.model_dump(mode="json") for entry in inventory])


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key in artifact metadata")
        result[key] = value
    return result


def _json(data):
    try:
        return json.loads(data, object_pairs_hook=_unique_object,
                          parse_constant=lambda value: (_ for _ in ()).throw(ValueError("nonfinite metadata")))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError("invalid artifact JSON metadata") from exc


def _read_json(path: Path):
    with _open_regular(path) as stream:
        raw = stream.read(JSON_LIMIT + 1)
    if len(raw) > JSON_LIMIT:
        raise ValueError("artifact JSON metadata exceeds size limit")
    return _json(raw)


def is_tokenizer_file(relative: str) -> bool:
    """A file of the tokenizer snapshot: fingerprinted, and copied byte-for-byte into derived models."""
    return relative in TOKENIZER_FILES or relative.startswith("chat_templates/")


def _tokenizer_hash(inventory) -> str:
    selected = tuple(entry for entry in inventory if is_tokenizer_file(entry.path))
    names = {entry.path for entry in selected}
    if "tokenizer_config.json" not in names or not names.intersection({
            "tokenizer.json", "tokenizer.model", "spiece.model", "vocab.json", "vocab.txt"}):
        raise ValueError("complete local tokenizer snapshot is required")
    return digest(_inventory_bytes(selected))


def tokenizer_fingerprint(directory: str | Path) -> str:
    """Hash the sorted tokenizer snapshot inventory, including template files."""
    return _tokenizer_hash(_inventory(Path(directory).absolute()))


def directory_inventory_sha256(directory: str | Path) -> str:
    """The ``inventory_sha256`` that ``ModelArtifactStore.freeze`` records for these exact file bytes."""
    return digest(_inventory_bytes(_inventory(Path(directory).absolute())))


def _tensor_header(path: Path, relative: str) -> tuple[TensorMetadata, ...]:
    with _open_regular(path) as stream:
        prefix = stream.read(8)
        if len(prefix) != 8:
            raise ValueError("truncated safetensors header")
        length = struct.unpack("<Q", prefix)[0]
        if not 1 <= length <= JSON_LIMIT:
            raise ValueError("invalid safetensors header length")
        header = _json(stream.read(length))
        data_size = os.fstat(stream.fileno()).st_size - 8 - length
    if not isinstance(header, dict) or data_size < 0:
        raise ValueError("invalid safetensors tensor header")
    tensors, ranges = [], []
    for name, metadata in header.items():
        if name == "__metadata__":
            if not isinstance(metadata, dict) or any(not isinstance(v, str) for v in metadata.values()):
                raise ValueError("invalid safetensors tensor metadata")
            continue
        if not isinstance(metadata, dict) or set(metadata) != {"dtype", "shape", "data_offsets"}:
            raise ValueError("invalid safetensors tensor metadata")
        tensor = TensorMetadata(file=relative, name=name, shape=metadata["shape"], dtype=metadata["dtype"])
        offsets = metadata["data_offsets"]
        if (not isinstance(offsets, list) or len(offsets) != 2
                or any(type(value) is not int for value in offsets)):
            raise ValueError("invalid safetensors tensor offsets")
        start, end = offsets
        expected = math.prod(tensor.shape) * DTYPE_BYTES[tensor.dtype]
        if not 0 <= start <= end <= data_size or end - start != expected:
            raise ValueError("safetensors tensor geometry/offset mismatch")
        tensors.append(tensor)
        ranges.append((start, end))
    position = 0
    for start, end in sorted(ranges):
        if start != position:
            raise ValueError("safetensors tensor offsets overlap or leave a gap")
        position = end
    if not tensors or position != data_size:
        raise ValueError("safetensors tensor geometry does not cover payload")
    return tuple(tensors)


def _validate_adapter(directory: Path, adapter: AdapterMetadata):
    actual = _read_json(directory / "adapter_config.json")
    if not isinstance(actual, dict):
        raise ValueError("invalid adapter configuration")
    expected = {"peft_type": "OFT" if adapter.method == "oft" else "LORA", "r": adapter.rank,
                "target_modules": sorted(adapter.target_modules), "modules_to_save": sorted(adapter.modules_to_save),
                "fan_in_fan_out": adapter.fan_in_fan_out, "task_type": adapter.task_type,
                "inference_mode": adapter.inference_mode, "bias": adapter.bias}
    defaults = {"modules_to_save": [], "bias": "none", "use_rslora": False, "use_dora": False,
                "lora_dropout": 0, "rank_pattern": {}, "alpha_pattern": {}, "target_parameters": [],
                "layer_replication": [], "init_lora_weights": True, "fan_in_fan_out": False,
                "task_type": None, "inference_mode": False}
    # Reviewed PEFT 0.18.1 export defaults. Features without typed effective
    # metadata must remain at these defaults; unknown options fail closed even
    # when their value is None/False. Do not silently inherit new loader variants.
    unsupported_defaults = {"auto_mapping": None, "exclude_modules": None,
                            "layers_to_transform": None, "layers_pattern": None}
    if adapter.method != "oft":
        expected.update(lora_alpha=adapter.alpha, lora_dropout=adapter.dropout, bias=adapter.bias,
                        use_rslora=adapter.use_rslora, use_dora=adapter.use_dora,
                        rank_pattern={p.module: p.rank for p in adapter.rank_pattern},
                        alpha_pattern={p.module: p.alpha for p in adapter.alpha_pattern},
                        target_parameters=sorted(adapter.target_parameters),
                        layer_replication=[list(pair) for pair in adapter.layer_replication],
                        init_lora_weights=("pissa" if adapter.pissa_iter == -1
                                           else f"pissa_niter_{adapter.pissa_iter}")
                        if adapter.pissa_init else True)
        unsupported_defaults.update(
            megatron_config=None, megatron_core="megatron.core", trainable_token_indices=None,
            loftq_config={}, eva_config=None, corda_config=None, alora_invocation_tokens=None,
            use_qalora=False, qalora_group_size=16, lora_bias=False, arrow_config=None,
            ensure_weight_tying=False,
        )
    else:
        expected.update(oft_block_size=adapter.oft_block_size, module_dropout=adapter.module_dropout)
        unsupported_defaults.update(init_weights=True, coft=False, eps=6e-5, block_share=False,
                                    use_cayley_neumann=True, num_cayley_neumann_terms=5)
    # These strings are export provenance, never instructions for locating or
    # loading the base. ModelDescriptor.effective_base is authoritative.
    provenance = {"base_model_name_or_path", "revision", "peft_version"}
    unknown = set(actual) - set(expected) - set(unsupported_defaults) - provenance
    if unknown:
        raise ValueError(f"unsupported adapter configuration fields: {', '.join(sorted(unknown))}")
    for key in provenance & set(actual):
        if actual[key] is not None and not isinstance(actual[key], str):
            raise ValueError(f"invalid adapter provenance: {key}")
    for key, default in unsupported_defaults.items():
        if key in actual and canonical(actual[key]) != canonical(default):
            raise ValueError(f"unsupported nondefault adapter configuration: {key}")
    for key, wanted in expected.items():
        value = actual.get(key, defaults.get(key))
        if key in {"modules_to_save", "target_parameters", "layer_replication"} and value is None:
            value = []
        if isinstance(wanted, list) and isinstance(value, list) and key != "layer_replication":
            if any(not isinstance(item, str) for item in value):
                raise ValueError(f"invalid adapter effective configuration: {key}")
            value = sorted(value)
        if (value != wanted or isinstance(wanted, bool) and type(value) is not bool
                or type(wanted) in {int, float} and type(value) not in {int, float}):
            raise ValueError(f"adapter effective configuration mismatch: {key}")
    # base_model_name_or_path is intentionally never used as the effective base.


def _validate_payload(directory: Path, descriptor: ModelDescriptor, inventory):
    entries = {entry.path: entry for entry in inventory}
    if "config.json" not in entries or entries["config.json"].sha256 != descriptor.config_sha256:
        raise ValueError("model config snapshot fingerprint mismatch")
    if _tokenizer_hash(inventory) != descriptor.tokenizer_fingerprint:
        raise ValueError("tokenizer snapshot fingerprint mismatch")
    prefix = "adapter_model" if descriptor.storage_kind == "adapter" else "model"
    if f"{prefix}.safetensors" not in entries and f"{prefix}.safetensors.index.json" not in entries:
        raise ValueError("model storage requires weights or a complete shard index")
    if f"{prefix}.safetensors" in entries and any(
            re.fullmatch(rf"{prefix}-\d+-of-\d+\.safetensors", name) for name in entries):
        raise ValueError("ambiguous single and sharded model weights")
    actual = []
    for name in entries:
        if name.endswith(".safetensors"):
            actual.extend(_tensor_header(directory / name, name))
        elif name.startswith(("pytorch_model", "adapter_model")) and name.endswith(".bin"):
            raise ValueError("model tensors require inspectable safetensors storage")
    actual_by_name = {tensor.name: tensor for tensor in actual}
    if len(actual_by_name) != len(actual) or set(actual_by_name) != {tensor.name for tensor in descriptor.tensors}:
        raise ValueError("tensor inventory names do not match snapshot")
    if any(actual_by_name[tensor.name] != tensor for tensor in descriptor.tensors):
        raise ValueError("tensor inventory geometry does not match snapshot")
    actual_value_head = any(name.startswith(("v_head.", "value_head.", "score."))
                            for name in actual_by_name)
    if actual_value_head != descriptor.has_value_head:
        raise ValueError("model value head tensor metadata differs from descriptor")
    for filename in entries:
        if filename.endswith(".safetensors.index.json"):
            index = _read_json(directory / filename)
            weights = index.get("weight_map") if isinstance(index, dict) else None
            if not isinstance(weights, dict) or not weights:
                raise ValueError("invalid model shard index")
            for name, shard in weights.items():
                if not isinstance(shard, str):
                    raise ValueError("invalid model shard path")
                shard = _relative(shard)
                if shard not in entries or name not in actual_by_name or actual_by_name[name].file != shard:
                    raise ValueError("missing or mismatched model shard")
            if set(weights) != set(actual_by_name):
                raise ValueError("model shard index does not cover tensor inventory")
    if descriptor.adapter is not None:
        if "adapter_config.json" not in entries:
            raise ValueError("missing adapter configuration")
        _validate_adapter(directory, descriptor.adapter)
    elif "adapter_config.json" in entries:
        raise ValueError("full model snapshot cannot contain an adapter loader configuration")


ARTIFACT_LAYOUT = frozenset({"files", "descriptor.json", "inventory.json"})


def verify_artifact_directory(location: str | Path, ref: ModelArtifactRef) -> ModelDescriptor:
    """Verify one stored artifact: layout, descriptor identity, inventory and payload; no lineage.

    ``location`` is ``<root>/<artifact_id>/<inventory_sha256>`` holding ``files/``, ``descriptor.json``
    and ``inventory.json``. Every file is hashed once; links anywhere on the path or inside are refused.
    """
    ref = ModelArtifactRef.model_validate(ref)
    location = Path(location).absolute()
    try:
        _check_ancestors(location)
        if {file.name for file in location.iterdir()} != ARTIFACT_LAYOUT:
            raise ValueError("unexpected model artifact metadata files")
        descriptor_raw = _read_json(location / "descriptor.json")
        inventory_raw = _read_json(location / "inventory.json")
        descriptor = ModelDescriptor.model_validate(descriptor_raw)
        # Identity belongs to the canonical content originally stored, not
        # to defaults added by a newer metadata validator. The normalized
        # descriptor must still pass all current loader/payload checks below.
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


class ArtifactVerifier:
    """Verifies each artifact at most once while it lives; create one per public operation.

    ``lineage=True`` (the control-plane store) also verifies every ancestor and the lineage rules.
    ``lineage=False`` verifies each entry alone, for the GPU worker's own cache that holds only the
    roles an operation binds; ``ModelArtifactStore`` never offers it. ``verify``, ``path``,
    ``validate_input`` and lineage share one private completed-cache, so a shared base is hashed once per
    operation, and nothing verified survives the instance or reaches another verifier.
    """

    def __init__(self, root: str | Path, *, lineage: bool = True):
        self.root = Path(root).absolute()
        _check_ancestors(self.root)
        self.lineage = lineage
        self._completed = {}

    def location(self, ref: ModelArtifactRef) -> Path:
        ref = ModelArtifactRef.model_validate(ref)
        return self.root / ref.artifact_id / ref.inventory_sha256

    def verify(self, ref: ModelArtifactRef) -> ModelDescriptor:
        return self._verify(ref, frozenset())

    def path(self, ref: ModelArtifactRef) -> Path:
        self.verify(ref)
        return self.location(ref) / "files"

    def _verify(self, ref: ModelArtifactRef, active: frozenset) -> ModelDescriptor:
        ref = ModelArtifactRef.model_validate(ref)
        identity = (ref.artifact_id, ref.inventory_sha256)
        if identity in active:
            raise ValueError("cyclic model artifact lineage")
        if identity in self._completed:
            return self._completed[identity]
        descriptor = verify_artifact_directory(self.location(ref), ref)
        if self.lineage:
            self.validate_lineage(descriptor, active | {identity})
        # Cache only fully verified evidence, and only in this operation.
        self._completed[identity] = descriptor
        return descriptor

    def validate_lineage(self, descriptor: ModelDescriptor, active: frozenset = frozenset()) -> None:
        parents = {ref: self._verify(ref, frozenset(active)) for ref in descriptor.parents}
        if any(parent.synthetic and not descriptor.synthetic or parent.simulation and not descriptor.simulation
               for parent in parents.values()):
            raise ValueError("synthetic/simulation provenance must be preserved from all parents")
        if any(parent.root != descriptor.root for parent in parents.values()):
            raise ValueError("model artifact parent has a different HF root")
        base = descriptor.effective_base
        if isinstance(base, ModelArtifactRef):
            parent = parents[base]
            if parent.storage_kind != "full_model":
                raise ValueError("effective base must be a full model")
        else:
            parent = base
        if descriptor.storage_kind == "adapter" and (
                parent.config_sha256 != descriptor.config_sha256
                or parent.tokenizer_fingerprint != descriptor.tokenizer_fingerprint):
            raise ValueError("adapter effective base config/tokenizer mismatch")

    def _validate_load(self, load: ModelLoadRef, kind: str, require_real: bool):
        base = self.verify(load.base) if isinstance(load.base, ModelArtifactRef) else load.base
        if isinstance(base, ModelDescriptor) and base.storage_kind != "full_model":
            raise ValueError("model input base must be a full model")
        effective = base
        if load.adapter is not None:
            effective = self.verify(load.adapter)
            if effective.storage_kind != "adapter":
                raise ValueError("model input adapter must have adapter storage")
            if effective.effective_base != load.base:
                raise ValueError("adapter effective base differs from model input base")
            if (effective.config_sha256 != base.config_sha256
                    or effective.tokenizer_fingerprint != base.tokenizer_fingerprint):
                raise ValueError("adapter/base config or tokenizer differs")
        semantic = effective.semantic_kind if isinstance(effective, ModelDescriptor) else "causal_lm"
        if semantic != kind:
            raise ValueError(f"model input requires semantic kind {kind}")
        if require_real and any(isinstance(item, ModelDescriptor) and not item.eligible_for_real_use
                                for item in (base, effective)):
            raise ValueError("synthetic/simulation artifact is ineligible for real use")
        return effective

    def validate_input(self, model_input: ModelInput, *, require_real: bool = False) -> ModelInput:
        """Resolve and verify exact storage, semantics, lineage and tokenizer bindings."""
        model_input = ModelInput.model_validate(model_input)
        if model_input.frozen_reference is not None and model_input.frozen_reference != model_input.policy:
            raise ValueError("frozen reference must bind the exact policy parent")
        policy = self._validate_load(model_input.policy, "causal_lm", require_real)
        for load, kind in ((model_input.frozen_reference, "causal_lm"),
                           (model_input.reward_model, "reward_model")):
            if load is not None:
                other = self._validate_load(load, kind, require_real)
                if other.tokenizer_fingerprint != policy.tokenizer_fingerprint:
                    raise ValueError("reference/reward tokenizer differs from policy")
                root = other.root if isinstance(other, ModelDescriptor) else other
                policy_root = policy.root if isinstance(policy, ModelDescriptor) else policy
                if root != policy_root:
                    raise ValueError("reference/reward HF root differs from policy")
        return model_input


class ModelArtifactStore:
    """Content-addressed local copies; every public read verifies both identities."""

    def __init__(self, root: str | Path):
        self.root = Path(root).absolute()
        _check_ancestors(self.root)
        self.root.mkdir(parents=True, exist_ok=True)

    def verifier(self) -> ArtifactVerifier:
        """A lineage verifier whose completed-cache spans one public operation (each artifact hashed once).

        The control-plane store always verifies lineage; entry-only verification is the worker's alone.
        """
        return ArtifactVerifier(self.root)

    def _location(self, ref: ModelArtifactRef) -> Path:
        return self.root / ref.artifact_id / ref.inventory_sha256

    def freeze(self, directory: str | Path, descriptor: ModelDescriptor) -> ModelArtifactRef:
        descriptor = ModelDescriptor.model_validate(descriptor)
        source = Path(directory).absolute()
        sources = _files(source)
        if self.root == source or self.root.is_relative_to(source):
            raise ValueError("artifact store must not be inside source directory")
        _check_ancestors(self.root)
        verifier = ArtifactVerifier(self.root)
        temporary = Path(tempfile.mkdtemp(prefix=".freeze-", dir=self.root))
        try:
            payload = temporary / "files"
            payload.mkdir()
            for relative, file in sources:
                target = payload / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                with _open_regular(file) as reader, target.open("xb") as writer:
                    shutil.copyfileobj(reader, writer, length=1024 * 1024)
                    writer.flush()
                    os.fsync(writer.fileno())
            inventory = _inventory(payload)
            _validate_payload(payload, descriptor, inventory)
            verifier.validate_lineage(descriptor)
            body = canonical(descriptor.model_dump(mode="json"))
            inventory_body = _inventory_bytes(inventory)
            ref = ModelArtifactRef(artifact_id=digest(body), inventory_sha256=digest(inventory_body))
            for filename, data in (("descriptor.json", body), ("inventory.json", inventory_body)):
                with (temporary / filename).open("xb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
            destination = self._location(ref)
            _check_ancestors(destination)
            destination.parent.mkdir(parents=True, exist_ok=True)
            try:
                temporary.rename(destination)
            except OSError:
                if not destination.exists():
                    raise
                verifier.verify(ref)
            return ref
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)

    def get(self, ref: ModelArtifactRef) -> ModelDescriptor:
        return self.verify(ref)

    def verify(self, ref: ModelArtifactRef) -> ModelDescriptor:
        return self.verifier().verify(ref)

    def path(self, ref: ModelArtifactRef) -> Path:
        return self.verifier().path(ref)

    def validate_input(self, model_input: ModelInput, *, require_real: bool = False) -> ModelInput:
        """Resolve and verify exact storage, semantics, lineage and tokenizer bindings."""
        return self.verifier().validate_input(model_input, require_real=require_real)
