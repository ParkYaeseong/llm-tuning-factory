"""Worker-side dispatch of one verified v2 operation: train, evaluate, merge or materialize.

``execute_operation`` runs inside a launch directory whose request, manifest, runtime lock and engine
``worker.main_v2`` has already checked. It verifies the uploaded data and the staged model inputs
against the worker's own cache roots, then dispatches through the kind allowlist (LocalSelect never
reaches a worker). One monotonic deadline bounds the whole operation: every engine call receives what
verification left, and a model operation that returns past the deadline or after cancellation is never
reported COMPLETED. The worker passes its bound preflight functions in, so this module never imports it.

Module import touches only the standard library, pydantic, PyYAML and local contracts.
"""
from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import yaml

from . import model_ops as default_model_ops
from .capabilities import STAGES
from .model_artifacts import check_link_free, directory_inventory_sha256
from .model_ops import (
    POLICY_F32_V1,
    LocalModel,
    MaterializeReceipt,
    MergeReceipt,
    StagedLoad,
    check_full_model_directory,
)
from .operation_runner import (
    MODEL_INPUTS,
    WEIGHT_SUFFIXES,
    ResolvedModelInputs,
    VerifiedModelPath,
    VerifiedModelPaths,
    approved_model_path,
    bind_training_plan,
    operation_sha256,
    validated_operation,
    verify_model_paths,
    write_bound_plan,
)
from .operations import EvaluateOperation, MaterializeOperation, MergeOperation, TrainOperation
from .registry import canonical, dataset_descriptor, digest, file_digest, parse_dataset
from .runtime_profiles import MODEL_OPS_RUNTIME, model_ops_pin_drift, operation_profile
from .training import CanceledError, LlamaFactoryBackend, check_generation_benchmark
from .worker_protocol import OperationResult, WorkerRequest

MODEL_OP_PARENT = "model-op"   # merge/materialize: exclusive output parent, created by the execution itself
MODEL_BINDING, PREFLIGHT = "model-binding.json", "device-preflight.json"
OPERATION_RECEIPT, TARGET_INVENTORY = "operation-receipt.json", "target-inventory.json"
_HEX32 = re.compile(r"^[0-9a-f]{32}$")
_SMALL_FILE = 16 * 1024 * 1024
_clock = time.monotonic   # the one monotonic clock of the operation deadline (replaced in tests)


class _Budget:
    """One deadline for the whole operation; each check also honours cancellation."""

    def __init__(self, timeout: float, canceled: Callable[[], bool]):
        self.deadline, self.canceled = _clock() + timeout, canceled

    def remaining(self, step: str) -> float:
        if self.canceled():
            raise CanceledError(f"cancellation requested {step}")
        left = self.deadline - _clock()
        if left <= 0:
            raise TimeoutError(f"attempt deadline exceeded {step}")
        return left


def write_benchmark_data(directory: Path, raw: bytes) -> Path:
    """Write the registered benchmark bytes of an evaluate launch as ``data/benchmark.<json|jsonl>``."""
    _, _, file_format = parse_dataset(raw, "instruction")
    data = Path(directory) / "data"
    data.mkdir(parents=True, exist_ok=True)
    path = data / f"benchmark.{file_format}"
    _write_new(path, raw)
    return path


def _write_new(path: Path, data: bytes) -> None:
    with Path(path).open("xb") as stream:   # never replaces an uploaded or earlier file
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def _read_small(path: Path) -> bytes:
    check_link_free(path)
    with Path(path).open("rb") as stream:
        raw = stream.read(_SMALL_FILE + 1)
    if len(raw) > _SMALL_FILE:
        raise ValueError(f"{Path(path).name} exceeds the size limit")
    return raw


def _evidence(directory: Path, names: Sequence[str]) -> dict[str, str]:
    evidence = {}
    for name in names:
        check_link_free(directory / name)
        evidence[name] = file_digest(directory / name)
    return evidence


def _dataset_file(directory: Path, stem: str, ref, kind: str) -> tuple[str, bytes]:
    """The uploaded ``data/<stem>.<json|jsonl>``: exactly one, hash-equal to the pinned DatasetRef."""
    data = directory / "data"
    present = [name for name in (f"{stem}.json", f"{stem}.jsonl")
               if (data / name).exists() or (data / name).is_symlink()]
    if len(present) != 1:
        raise ValueError("transferred dataset is missing or ambiguous")
    name = present[0]
    check_link_free(data / name)
    raw = (data / name).read_bytes()
    if digest(raw) != ref.sha256:
        raise ValueError("transferred dataset hash mismatch")
    _, _, file_format = parse_dataset(raw, kind)
    if name != f"{stem}.{file_format}":
        raise ValueError("transferred dataset format differs from its file name")
    return name, raw


def _paths_binding(verified: VerifiedModelPaths) -> dict:
    return {"schema_version": 1, "models": [
        {"role": item.role, "index": item.index, "path": item.path.as_posix(),
         **item.staged.model_dump(mode="json", exclude={"role", "index"})}
        for item in sorted(verified.models, key=lambda item: (item.role, item.index))]}


def _entry(verified: VerifiedModelPaths, role: str, index: int = 0) -> VerifiedModelPath | None:
    return next((item for item in verified.models if (item.role, item.index) == (role, index)), None)


def _local_model(verified: VerifiedModelPaths, role: str, index: int = 0) -> LocalModel:
    entry = _entry(verified, role, index)
    if entry is None:
        raise ValueError(f"unbound model role: {role}")
    staged = entry.staged
    return LocalModel(path=entry.path, identity=staged.artifact if staged.artifact is not None else staged.hf_root,
                      descriptor=entry.descriptor, config_sha256=staged.config_sha256,
                      tokenizer_fingerprint=staged.tokenizer_fingerprint)


def _model_ops_device() -> str:
    try:
        import torch
    except ImportError:
        return "cpu"   # model_ops then refuses the missing runtime with its own message
    return "cuda" if torch.cuda.is_available() else "cpu"


def _dump(value) -> Any:
    return value.model_dump(mode="json") if hasattr(value, "model_dump") else value


def _write_target_inventory(directory: Path, output: Path, ops) -> None:
    """``target-inventory.json`` of a full-model output; 7.5 binds it to the frozen artifact and verifies it."""
    from .graph_planner import TargetModuleEvidence
    check_full_model_directory(output)
    modules = sorted((TargetModuleEvidence.model_validate(_dump(item)) for item in ops.inspect_linear_modules(output)),
                     key=lambda item: item.name)
    if not modules or len({item.name for item in modules}) != len(modules):
        raise ValueError("full-model output target inventory is empty or ambiguous")
    _write_new(directory / TARGET_INVENTORY, canonical({"schema_version": 1, "modules": [
        item.model_dump(mode="json") for item in modules]}))


def _inspects_full_output(operation, profile: dict) -> bool:
    # The inspector needs the pinned model-operation libraries, which only the primary runtime has.
    return isinstance(operation, MaterializeOperation) or (
        isinstance(operation, TrainOperation) and operation.config.training_method in {"full", "freeze"}
        and profile["profile"] == "primary")


def _sample_ordinals(sample_ids: Sequence[str], count: int) -> list[int]:
    ordinals = []
    for value in sample_ids:
        match = re.fullmatch(r"row:(0|[1-9][0-9]*)", value)
        if match is None or int(match.group(1)) >= count:
            raise ValueError("benchmark sample IDs must name rows of the registered dataset")
        ordinals.append(int(match.group(1)))
    return ordinals


def _execute_train(operation: TrainOperation, directory: Path, budget: _Budget, backend, resolved, caches, roots,
                   ops, profile, preflight) -> list[str]:
    if preflight is None:
        raise ValueError("training requires the worker's bound runtime preflight")
    config = backend.validate_config(operation.config)
    template = backend.translate(config, operation.model_input)
    if yaml.safe_load(_read_small(directory / "train.yaml").decode("utf-8")) != template:
        raise ValueError("training YAML differs from resolved configuration")
    kind = STAGES[config.training_stage][1]
    name, raw = _dataset_file(directory, "dataset", operation.dataset, kind)
    if json.loads(_read_small(directory / "data" / "dataset_info.json")) != {
            "factory_dataset": dataset_descriptor(raw, kind, name)}:
        raise ValueError("dataset_info.json differs from the transferred dataset")
    budget.remaining("before model verification")
    verified = verify_model_paths(resolved, operation, model_cache=caches[0], hf_home=caches[1],
                                  inspect_targets=ops.inspect_target_modules)
    budget.remaining("after model verification")   # cache hashing and HF snapshots can take long
    write_bound_plan(directory, bind_training_plan(template, verified, approved_roots=roots))
    _write_new(directory / PREFLIGHT, canonical(preflight(config, verified)))
    backend.run_config(directory, budget.remaining("before training"), budget.canceled, "train.bound.yaml",
                       "train.log")
    backend.validate_outputs(directory, config)
    backend.validate_training_result(directory, config)
    names = ["train.yaml", "train.bound.yaml", MODEL_BINDING, "binding-evidence.json", PREFLIGHT]
    if _inspects_full_output(operation, profile):
        _write_target_inventory(directory, directory / "output", ops)
        names.append(TARGET_INVENTORY)
    return names


def _execute_evaluate(operation: EvaluateOperation, directory: Path, budget: _Budget, backend, resolved, caches,
                      roots, evaluation_preflight) -> list[str]:
    if evaluation_preflight is None:
        raise ValueError("evaluation requires the worker's bound runtime preflight")
    benchmark = operation.benchmark
    check_generation_benchmark(benchmark)
    _, raw = _dataset_file(directory, "benchmark", benchmark.dataset, "instruction")
    budget.remaining("before model verification")
    verified = verify_model_paths(resolved, operation, model_cache=caches[0], hf_home=caches[1])
    budget.remaining("after model verification")
    base, adapter = _entry(verified, "operation_base"), _entry(verified, "operation_adapter")
    evaluated = adapter or base
    if benchmark.tokenizer_fingerprint != evaluated.staged.tokenizer_fingerprint:
        raise ValueError("benchmark tokenizer differs from the evaluated model")
    if evaluated.descriptor is None or evaluated.descriptor.semantic_kind != benchmark.semantic_model_kind:
        raise ValueError("v2 evaluation supports greedy causal-LM generation benchmarks only")
    rows, _, _ = parse_dataset(raw, "instruction")
    ordinals = _sample_ordinals(benchmark.ordered_sample_ids, len(rows))
    subset = json.dumps([rows[ordinal] for ordinal in ordinals], ensure_ascii=False).encode("utf-8")
    _write_new(directory / "data" / "validation.json", subset)
    _write_new(directory / "data" / "dataset_info.json", json.dumps({
        "factory_validation": dataset_descriptor(subset, "instruction", "validation.json")}).encode("utf-8"))
    _write_new(directory / MODEL_BINDING, canonical(_paths_binding(verified)))
    _write_new(directory / PREFLIGHT, canonical(evaluation_preflight(
        benchmark, verified, operation.resources.facts.gpu_count)))
    plan = backend.prediction_plan(
        benchmark, model_path=approved_model_path(base.path, roots).as_posix(),
        adapter_path=approved_model_path(adapter.path, roots).as_posix() if adapter is not None else None,
        max_samples=len(ordinals))
    backend.predict_bound(directory, plan, benchmark.ordered_sample_ids, budget.remaining("before prediction"),
                          budget.canceled)
    return ["data/validation.json", "data/dataset_info.json", MODEL_BINDING, PREFLIGHT, "predict.yaml",
            "output/predictions.json"]


def _execute_model_operation(operation, directory: Path, budget: _Budget, resolved, caches, ops) -> list[str]:
    budget.remaining("before model verification")
    verified = verify_model_paths(resolved, operation, model_cache=caches[0], hf_home=caches[1])
    budget.remaining("after model verification")
    base = _local_model(verified, "operation_base")
    parent = directory / MODEL_OP_PARENT
    try:
        parent.mkdir()   # exclusive: model_ops stages .model-op-* only inside this attempt's own parent
    except FileExistsError as exc:
        raise ValueError("model-operation output parent already exists; an attempt runs at most once") from exc
    output = parent / "output"
    _write_new(directory / MODEL_BINDING, canonical(_paths_binding(verified)))
    budget.remaining("before the model operation")
    device = _model_ops_device()
    if isinstance(operation, MergeOperation):
        inputs = tuple(_local_model(verified, "operation_adapter", index) for index in range(len(operation.models)))
        receipt = MergeReceipt.model_validate(_dump(ops.merge_adapters(
            inputs, operation.weights, output, base=base, policy=POLICY_F32_V1, device=device,
            require_real=not operation.lineage.simulation)))
        matches = receipt.inputs == operation.models
    else:
        adapter = _local_model(verified, "operation_adapter")
        receipt = MaterializeReceipt.model_validate(_dump(ops.materialize_model(
            StagedLoad(base=base, adapter=adapter), output, policy=POLICY_F32_V1, device=device)))
        matches = (receipt.base, receipt.adapter) == (base.identity, operation.model)
    check_link_free(output)
    files = sorted(path.relative_to(output).as_posix() for path in output.rglob("*") if path.is_file())
    if not matches or receipt.policy_id != POLICY_F32_V1 or sorted(receipt.output_files) != files:
        raise ValueError("model-operation receipt differs from the operation or its output")
    names = [MODEL_BINDING]
    if isinstance(operation, MaterializeOperation):
        _write_target_inventory(directory, output, ops)
        names.append(TARGET_INVENTORY)
    # merge/materialize cannot be interrupted: past the deadline or after cancellation they never complete.
    budget.remaining("after the model operation")
    _write_new(directory / OPERATION_RECEIPT, canonical({
        "schema_version": 1, "kind": operation.kind, "device": device, "output": f"{MODEL_OP_PARENT}/output",
        "output_inventory_sha256": directory_inventory_sha256(output), "receipt": receipt.model_dump(mode="json")}))
    return [*names, OPERATION_RECEIPT]


def _absolute_root(path, name: str) -> Path | None:
    if path is None:
        return None
    if not Path(path).is_absolute():
        raise ValueError(f"{name} must be an absolute path")
    return Path(path)


def execute_operation(operation, directory: Path, timeout: float, canceled: Callable[[], bool], *,
                      request: WorkerRequest, backend: LlamaFactoryBackend | None = None,
                      model_cache: Path | None = None, hf_home: Path | None = None, ops=None,
                      preflight: Callable | None = None,
                      evaluation_preflight: Callable | None = None) -> OperationResult:
    """Run one verified operation in its launch directory; return its COMPLETED result, or raise.

    ``timeout`` is what is left of the attempt budget now; one deadline is derived from it at once.
    ``request`` must name this launch directory and operation; its attempt and manifest identities go
    into the result. ``model_cache`` and ``hf_home`` are absolute worker configuration, never request
    data. ``ops`` defaults to ``model_ops``. ``preflight(config, paths)`` (train) and
    ``evaluation_preflight(benchmark, paths, gpu_count)`` (evaluate) are the worker's bound preflights.
    """
    budget = _Budget(timeout, canceled)
    operation = validated_operation(operation)
    request = WorkerRequest.model_validate(request.model_dump(mode="json"))
    directory = Path(directory).absolute()
    if not _HEX32.match(directory.name):
        raise ValueError("launch directory must be named by its 32-hex launch_id")
    if request.launch_id != directory.name:
        raise ValueError("worker request launch identity differs from launch directory")
    if request.operation_sha256 != operation_sha256(operation):
        raise ValueError("worker request operation hash differs from manifest operation")
    check_link_free(directory)
    if model_cache is None:
        raise ValueError("worker model cache is not configured")
    caches = (_absolute_root(model_cache, "FACTORY_MODEL_CACHE"), _absolute_root(hf_home, "HF_HOME"))
    roots = tuple(root for root in caches if root is not None)
    profile = operation_profile(operation.kind, operation.resources.runtime.profile)
    if "model_ops_pins" in profile or _inspects_full_output(operation, profile):
        drift = model_ops_pin_drift()   # before any work, without importing torch
        if drift:
            raise ValueError(f"{MODEL_OPS_RUNTIME}: {', '.join(drift)}")
    if any(path.suffix in WEIGHT_SUFFIXES for path in directory.rglob("*")):
        raise ValueError("attempt directory must not contain model weights; stage them through the model cache")
    if (directory / "output").exists() or (directory / "output").is_symlink():
        raise ValueError("attempt output already exists")
    resolved = ResolvedModelInputs.model_validate_json(_read_small(directory / MODEL_INPUTS))
    backend = backend or LlamaFactoryBackend()
    ops = ops or default_model_ops
    if isinstance(operation, TrainOperation):
        names = _execute_train(operation, directory, budget, backend, resolved, caches, roots, ops, profile,
                               preflight)
    elif isinstance(operation, EvaluateOperation):
        names = _execute_evaluate(operation, directory, budget, backend, resolved, caches, roots,
                                  evaluation_preflight)
    elif isinstance(operation, (MergeOperation, MaterializeOperation)):
        names = _execute_model_operation(operation, directory, budget, resolved, caches, ops)
    else:   # pragma: no cover - OperationSpec admits only the four kinds above
        raise ValueError("unsupported operation kind")
    return OperationResult(status="COMPLETED", launch_id=directory.name, attempt_id=request.attempt_id,
                           manifest_sha256=request.manifest_sha256, operation_sha256=request.operation_sha256,
                           kind=operation.kind, engine_version=operation.resources.runtime.engine_version,
                           evidence=_evidence(directory, (MODEL_INPUTS, *names)))
