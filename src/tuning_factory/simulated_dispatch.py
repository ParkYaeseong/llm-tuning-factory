"""Simulated dispatch for dry-run graphs (Task 8.4; plan §5, Q21): a real attempt's ledger path that never leaves the PC.

``SimulatedOperationDispatcher`` has the protocol of ``attempt_runner.OperationAttemptRunner`` (``run_claimed``,
``reconcile``, ``outputs_for``, ``failure_kind``), for simulation plans only:

1. Before launch it refuses, through ``JobStore.abandon_unlaunched`` (lock released in the same transaction, so the
   refusal is counted and terminal): a manifest that is not a graph-owned schema-2 simulation (``SIMULATION_ONLY``), a
   changed source, any executor (``NO_EXECUTOR``), an untyped or foreign approval, an unapproved destination, a lease
   other than ``simulation:<pinned key>``, a changed dataset registration, a forged launch ID, a missing or mismatched
   HF-root snapshot (``ROOT_SNAPSHOT_MISSING``) and missing base target evidence (``TARGETS_REQUIRED``).
2. ``begin_launch`` runs once, so the D12 pause/cancel gate applies. The launch directory is prepared as the runner
   does: ``manifest.json``, the engine files or benchmark bytes, ``model-inputs.json`` from the real
   ``stage_model_inputs`` and ``command.json``.
3. ``transition(RUNNING)``, then the local record ``remote-attempt.json`` (``{"simulated": true, "contact": "none"}``,
   never a remote path), and only then the first effect: ``SimulatedWorker`` writes the worker's output layout with
   zero-valued tensors and a ``simulated.json`` marker, and returns an ``OperationResult`` naming every file. The result
   is published exclusively as ``<launch_id>.result.json``.
4. RUNNING -> EVALUATING, then the real ``freeze_operation_output`` (``build_output_descriptor``, ``check_result``) or
   ``evaluation_record``. Every descriptor and record is ``simulation=True``: never eligible for real use, never
   selected, never registered (D6/D7). Then COMPLETED and ``release_gpu``.

Crash recovery. A simulated attempt is fenced like a real one, so it settles without ever running twice:
- PROVISIONING after ``begin_launch``: nothing ran and no record exists, so ``GraphStore.release_uncontacted`` (D9)
  releases it truthfully; the fenced actor's ``transition(RUNNING)`` then fails before any effect.
- RUNNING/EVALUATING (FAILED holding its lock after ``recover_stale``): ``failure_kind`` is ``uncertain`` and
  ``reconcile`` settles it from the published result. Without one it publishes a FAILED result itself
  (``SIMULATION_STOPPED``), exclusively, so a suspended original actor can publish nothing afterwards, and the job is
  no longer RUNNING, so that actor can neither transition nor freeze. Nothing is re-run.
- COMPLETED or CANCELED still holding the lock: as the runner (rebuild and release, or release on an accepted result).

Module import touches only the standard library, pydantic and local contracts. graph_store and jobs are imported
inside the dispatcher only (``SimulatedWorker`` needs no ledger); execution, attempt_runner, desktop_runtime,
paramiko, mlflow and heavy libraries are never imported.
"""
from __future__ import annotations

import contextlib
import json
import logging
import math
import os
import struct
import tempfile
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol

from .executor_profiles import approval_destination
from .graph_planner import (
    ApprovalGrant,
    DatasetSnapshot,
    ModelTargetEvidence,
    NodeEvaluationBinding,
    NodeModelBinding,
    snapshot_dataset,
)
from .manifest import source_hash, verify_manifest
from .model_artifacts import (
    HFRootRef,
    ModelArtifactRef,
    ModelArtifactStore,
    check_link_free,
    directory_inventory_sha256,
    is_processor_file,
    is_tokenizer_file,
    list_files,
)
from .model_ops import (
    POLICY_F32_V1,
    MaterializeReceipt,
    MergeReceipt,
    local_config_sha256,
    local_tokenizer_fingerprint,
    validate_merge_inputs,
    verify_inspected_targets,
)
from .operation_dispatch import (
    BASE_SNAPSHOT,
    MODEL_OP_PARENT,
    OPERATION_RECEIPT,
    TARGET_INVENTORY,
    inspects_full_output,
    write_benchmark_data,
)
from .operation_outputs import PREDICTIONS, TRAIN_OUTPUT, evaluation_record, freeze_operation_output
from .operation_runner import (
    MODEL_INPUTS,
    ResolvedModelInputs,
    operation_sha256,
    stage_model_inputs,
    validated_operation,
)
from .operations import EvaluateOperation, MaterializeOperation, MergeOperation, TrainOperation
from .registry import canonical, digest, file_digest
from .runtime_profiles import operation_profile
from .target_inspection import resolved_lora_targets
from .training import OFT_UNFREEZABLE, CanceledError, LlamaFactoryBackend
from .worker_protocol import COMMAND, OperationResult, WorkerRequest

if TYPE_CHECKING:
    from .graph_store import FailureKind, GraphStore, NodeOutputs

SIMULATION_ONLY = "simulated dispatch requires a simulation manifest"
ROOT_SNAPSHOT_MISSING = "simulated execution requires a local config/tokenizer snapshot of {repo}@{revision}"
TARGETS_REQUIRED = "simulated {kind} requires inspected base target evidence"
NO_EXECUTOR = "simulated dispatch never uses an executor"
SIMULATION_STOPPED = "simulated execution stopped before its result; settled by reconcile without re-running it"
RESULT_PUBLISHED = "a result is already published for this launch; reconcile the same attempt"
# 8.4 review Important 2: zero-valued placeholders of a real-size base are still real bytes on disk (written, then
# copied by the freeze), so a dry run refuses before launch above this cap (configurable on the dispatcher).
SIMULATED_OUTPUT_TOO_LARGE = ("simulated outputs would exceed the dry-run size cap; simulate a smaller base or raise "
                              "the cap")
MAX_SIMULATED_OUTPUT_BYTES = 1024 ** 3
NO_HARD_LINKS = "the state directory's filesystem has no hard links; a simulated result cannot be published exclusively"
SIMULATED_MARKER = "simulated.json"
REMOTE_ATTEMPT = "remote-attempt.json"   # graph_store.REMOTE_ATTEMPT; here a local record of no contact
ENGINE_VERSION = "simulated"
_ACTIVE = frozenset({"PROVISIONING", "RUNNING", "EVALUATING"})
_PEFT_PREFIX = "base_model.model."
_VALUE_HEAD = {"v_head.summary.weight": (1, 1), "v_head.summary.bias": (1,)}
_CHUNK = 1024 * 1024


@dataclass(frozen=True)
class OperationOutcome:
    """Field for field ``attempt_runner.OperationOutcome`` (that module imports paramiko through ``execution``)."""
    job_id: str
    attempt_id: str
    status: Literal["COMPLETED", "FAILED", "CANCELED"]
    uncertain: bool
    outputs: NodeOutputs | None
    inspected_targets: tuple[ModelTargetEvidence, ...] = ()
    error: str | None = None


# -- HF-root snapshots ---------------------------------------------------------------------------

class RootSnapshotSource(Protocol):
    def snapshot(self, root: HFRootRef) -> Path | None: ...


def _matches_root(path, root: HFRootRef) -> bool:
    try:
        path = Path(path).absolute()
        check_link_free(path)
        return path.is_dir() and (local_config_sha256(path), local_tokenizer_fingerprint(path)) == (
            root.config_sha256, root.tokenizer_fingerprint)
    except (OSError, ValueError):
        return False


class DirectoryRootSnapshots:
    """``<directory>/<owner>/<name>/<revision>/``: a local copy of an HF root's config.json and tokenizer files.

    A snapshot is returned only when its config hash and tokenizer fingerprint equal the root's (HF roots carry only
    hashes on the PC, plan F14); the dispatcher re-checks whatever any source returns."""

    def __init__(self, directory: Path):
        self.directory = Path(directory).absolute()

    def snapshot(self, root: HFRootRef) -> Path | None:
        root = HFRootRef.model_validate(root)
        owner, name = root.repo_id.split("/")
        path = self.directory / owner / name / root.revision
        return path if _matches_root(path, root) else None


# -- files ---------------------------------------------------------------------------------------

@dataclass(frozen=True)
class _Tensors:
    """Zero-valued F32 tensors of a safetensors file, written by streaming (never held in memory)."""
    shapes: Mapping[str, tuple[int, ...]]


def _write_new(path: Path, content) -> None:
    """Create ``path`` (never replacing anything) with bytes, or a deterministic stdlib safetensors file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        if isinstance(content, _Tensors):
            header, offset = {}, 0
            for name in sorted(content.shapes):
                size = 4 * math.prod(content.shapes[name])
                header[name] = {"dtype": "F32", "shape": list(content.shapes[name]), "data_offsets": [offset, offset + size]}
                offset += size
            body = canonical(header)
            body += b" " * (-len(body) % 8)   # the format pads its header to 8 bytes with spaces
            stream.write(struct.pack("<Q", len(body)) + body)
            zeros = bytes(min(offset, _CHUNK))
            while offset:
                stream.write(zeros[:min(offset, _CHUNK)])
                offset -= min(offset, _CHUNK)
        else:
            stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())


def _publish_exclusive(path: Path, data: bytes) -> bool:
    """Create ``path`` atomically with ``data`` unless it already exists (a hard link never replaces); False if it did."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            return False
        try:
            _fsync_directory(path.parent)
        except OSError as exc:   # the link already published the result; durability is best effort here
            logging.getLogger(__name__).warning("directory fsync after publishing failed: %s", type(exc).__name__)
        return True
    finally:
        os.unlink(temporary)


def _fsync_directory(directory: Path) -> None:
    """Make a new directory entry durable where the platform allows opening a directory (not on Windows)."""
    if os.name == "nt":
        return
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def hard_links_supported(directory: Path) -> bool:
    """Whether ``directory``'s filesystem supports the hard links ``_publish_exclusive`` relies on (8.4 review Minor 2):
    probed before ``begin_launch``, so such a filesystem is refused before anything runs."""
    directory.mkdir(parents=True, exist_ok=True)
    descriptor, source = tempfile.mkstemp(prefix=".link-probe.", dir=directory)
    os.close(descriptor)
    target = source + ".link"
    try:
        os.link(source, target)
    except OSError:
        return False
    else:
        os.unlink(target)
        return True
    finally:
        os.unlink(source)


def _tensor_bytes(files) -> int:
    """The bytes of every zero-valued tensor in a layout (the files that would be written, then frozen)."""
    return sum(4 * math.prod(shape) for _, content in files if isinstance(content, _Tensors)
               for shape in content.shapes.values())


def _json_file(value) -> bytes:
    """PEFT's export format: indented, sorted keys, LF (as ``model_ops.write_adapter_config``)."""
    return json.dumps(value, indent=2, sort_keys=True).encode("utf-8")


# -- the simulated worker ------------------------------------------------------------------------

@dataclass(frozen=True)
class _Layout:
    files: tuple[tuple[str, bytes | _Tensors], ...]   # launch-relative path -> content, in writing order
    prepared: tuple[str, ...] = ()                     # files the dispatcher prepared that the evidence names
    receipt: MergeReceipt | MaterializeReceipt | None = None


def _base_directory(base, verifier, roots) -> Path:
    """The effective base's config.json and tokenizer files: the store's copy, or a verified HF-root snapshot."""
    if isinstance(base, ModelArtifactRef):
        return verifier.path(base)
    path = roots.snapshot(base) if roots is not None else None
    if path is None or not _matches_root(path, base):
        raise ValueError(ROOT_SNAPSHOT_MISSING.format(repo=base.repo_id, revision=base.revision))
    return Path(path).absolute()


def _snapshot_files(directory: Path, prefix: str) -> list:
    return [(f"{prefix}/{relative}", file.read_bytes()) for relative, file in list_files(directory)
            if relative == "config.json" or is_tokenizer_file(relative) or is_processor_file(relative)]


def _evidence_for(base, target_evidence, verifier) -> ModelTargetEvidence | None:
    """The one inspected target inventory of ``base`` (plan plus persisted), re-verified for artifact bases."""
    matches = {canonical(item.model_dump(mode="json")): item for item in target_evidence if item.base == base}
    if len(matches) > 1:
        raise ValueError("ambiguous inspected base target evidence")
    evidence = next(iter(matches.values()), None)
    if evidence is not None and isinstance(base, ModelArtifactRef):
        evidence = verify_inspected_targets(evidence, verifier.verify(base), base)
    return evidence


def _inventory(evidence: ModelTargetEvidence) -> bytes:
    """``target-inventory.json`` as the worker writes it: the sorted inspected modules."""
    modules = sorted(evidence.modules, key=lambda item: item.name)
    return canonical({"schema_version": 1, "modules": [item.model_dump(mode="json") for item in modules]})


def _full_weights(evidence: ModelTargetEvidence) -> _Tensors:
    return _Tensors({f"{item.name}.weight": (item.output_dimension, item.input_dimension) for item in evidence.modules})


def _output_files(files, prefix: str) -> tuple[str, ...]:
    return tuple(sorted(relative[len(prefix) + 1:] for relative, _ in files if relative.startswith(prefix + "/")))


class SimulatedWorker:
    """Writes the worker's launch layout for one simulated operation, deterministically. No ledger access.

    Base ``config.json`` and tokenizer bytes come from the store (artifact bases) or from a ``RootSnapshotSource``
    (HF roots). Module names and dimensions come from the base target evidence. An explicit ``lora_target`` without
    evidence uses ``model.<target>`` with dimension 1: a documented placeholder that can never be real, and a child
    that needs evidence fails at compile exactly as after a real run. ``lora_target: all``, full/freeze training and
    materialization require evidence (``TARGETS_REQUIRED``).
    """

    def check(self, operation, *, artifacts: ModelArtifactStore, roots: RootSnapshotSource | None,
              target_evidence: tuple[ModelTargetEvidence, ...],
              max_output_bytes: int = MAX_SIMULATED_OUTPUT_BYTES) -> None:
        """Refuse what the simulated worker cannot invent, or outputs above ``max_output_bytes`` of tensors
        (``SIMULATED_OUTPUT_TOO_LARGE``); the dispatcher calls it before ``begin_launch``."""
        layout = self._layout(validated_operation(operation), artifacts.verifier(), roots, tuple(target_evidence))
        if _tensor_bytes(layout.files) > max_output_bytes:
            raise ValueError(SIMULATED_OUTPUT_TOO_LARGE)

    def execute(self, operation, launch_dir: Path, request: WorkerRequest, resolved: ResolvedModelInputs, *,
                artifacts: ModelArtifactStore, roots: RootSnapshotSource | None,
                target_evidence: tuple[ModelTargetEvidence, ...]) -> OperationResult:
        operation = validated_operation(operation)
        if not operation.lineage.simulation:
            raise ValueError(SIMULATION_ONLY)
        launch = Path(launch_dir).absolute()
        request = WorkerRequest.model_validate(request.model_dump(mode="json"))
        resolved = ResolvedModelInputs.model_validate(resolved.model_dump(mode="json"))
        if (request.launch_id, request.operation_sha256) != (launch.name, operation_sha256(operation)):
            raise ValueError("worker request differs from this launch or operation")
        if ((resolved.operation_kind, resolved.operation_sha256) != (operation.kind, operation_sha256(operation))
                or (launch / MODEL_INPUTS).read_bytes() != canonical(resolved.model_dump(mode="json"))):
            raise ValueError("staged model inputs belong to another operation")
        check_link_free(launch)
        if any((launch / name).exists() or (launch / name).is_symlink()
               for name in (TRAIN_OUTPUT, MODEL_OP_PARENT, SIMULATED_MARKER, OPERATION_RECEIPT, TARGET_INVENTORY)):
            raise ValueError("attempt output already exists")
        layout = self._layout(operation, artifacts.verifier(), roots, tuple(target_evidence))
        for relative, content in layout.files:
            _write_new(launch / relative, content)
        names = [MODEL_INPUTS, *layout.prepared, *(relative for relative, _ in layout.files)]
        if layout.receipt is not None:
            output = launch / MODEL_OP_PARENT / "output"
            _write_new(launch / OPERATION_RECEIPT, canonical({
                "schema_version": 1, "kind": operation.kind, "device": ENGINE_VERSION,
                "output": f"{MODEL_OP_PARENT}/output", "output_inventory_sha256": directory_inventory_sha256(output),
                "receipt": layout.receipt.model_dump(mode="json"), "simulated": True}))
            names.append(OPERATION_RECEIPT)
        _write_new(launch / SIMULATED_MARKER, canonical({
            "schema_version": 1, "simulated": True, "kind": operation.kind,
            "note": "zero-valued placeholder outputs of a dry-run graph; never real, never selected"}))
        names.append(SIMULATED_MARKER)
        return OperationResult(status="COMPLETED", launch_id=launch.name, attempt_id=request.attempt_id,
                               manifest_sha256=request.manifest_sha256, operation_sha256=request.operation_sha256,
                               kind=operation.kind, engine_version=ENGINE_VERSION,
                               evidence={name: file_digest(launch / name) for name in names})

    # -- layouts ---------------------------------------------------------------------------------

    def _layout(self, operation, verifier, roots, target_evidence) -> _Layout:
        if isinstance(operation, TrainOperation):
            return self._train(operation, verifier, roots, target_evidence)
        if isinstance(operation, EvaluateOperation):
            benchmark = operation.benchmark
            return _Layout(files=((PREDICTIONS, canonical({
                "ordered_sample_ids": list(benchmark.ordered_sample_ids),
                "predictions": [""] * len(benchmark.ordered_sample_ids), "source": ENGINE_VERSION})),))
        if isinstance(operation, MergeOperation):
            return self._merge(operation, verifier, roots)
        return self._materialize(operation, verifier, roots, target_evidence)

    def _train(self, operation: TrainOperation, verifier, roots, target_evidence) -> _Layout:
        config, model_input = operation.config, operation.model_input
        if config.training_method == "oft":
            raise ValueError(OFT_UNFREEZABLE)
        base = model_input.policy.base
        directory = _base_directory(base, verifier, roots)
        root = base if isinstance(base, HFRootRef) else verifier.verify(base).root
        evidence = _evidence_for(base, target_evidence, verifier)
        files = _snapshot_files(directory, BASE_SNAPSHOT)
        if config.training_method in {"lora", "qlora"}:
            targets, modules = self._adapter_modules(operation, evidence, verifier)
            rank, options = config.hyperparameters.lora_rank, config.finetuning_options
            initialization = (True if not options.pissa_init else
                              "pissa" if options.pissa_iter == -1 else f"pissa_niter_{options.pissa_iter}")
            files.append((f"{TRAIN_OUTPUT}/adapter_config.json", _json_file({
                "peft_type": "LORA", "r": rank, "lora_alpha": options.lora_alpha or 2 * rank,
                "lora_dropout": float(options.lora_dropout), "target_modules": list(targets), "bias": "none",
                "fan_in_fan_out": False, "task_type": "CAUSAL_LM", "inference_mode": True,
                "use_rslora": options.use_rslora, "use_dora": options.use_dora, "init_lora_weights": initialization,
                "base_model_name_or_path": root.repo_id})))
            shapes = {}
            for name, inputs, outputs in modules:
                shapes[f"{_PEFT_PREFIX}{name}.lora_A.weight"] = (rank, inputs)
                shapes[f"{_PEFT_PREFIX}{name}.lora_B.weight"] = (outputs, rank)
            files.append((f"{TRAIN_OUTPUT}/adapter_model.safetensors", _Tensors(shapes)))
        else:
            if evidence is None:
                raise ValueError(TARGETS_REQUIRED.format(kind=operation.kind))
            files.append((f"{TRAIN_OUTPUT}/config.json", (directory / "config.json").read_bytes()))
            files.append((f"{TRAIN_OUTPUT}/model.safetensors", _full_weights(evidence)))
            if inspects_full_output(operation, operation_profile("train", operation.resources.runtime.profile)):
                files.append((TARGET_INVENTORY, _inventory(evidence)))
        if config.training_stage in {"rm", "ppo"}:
            files.append((f"{TRAIN_OUTPUT}/value_head.safetensors", _Tensors(_VALUE_HEAD)))
        return _Layout(files=tuple(files), prepared=("train.yaml",))

    @staticmethod
    def _adapter_modules(operation: TrainOperation, evidence, verifier):
        """The adapter's target names and (module, in, out) of every targeted base module."""
        config, model_input = operation.config, operation.model_input
        if model_input.initialization == "continue_adapter":   # the parent adapter resumes with its own targets
            if evidence is None:
                raise ValueError(TARGETS_REQUIRED.format(kind=operation.kind))
            targets = tuple(sorted(verifier.verify(model_input.policy.adapter).adapter.target_modules))
        elif evidence is not None:   # LLaMA-Factory: leaf names, or full language-model names for composite models
            targets = resolved_lora_targets(config.hyperparameters.lora_target,
                                            [item.name for item in evidence.modules])
        elif config.hyperparameters.lora_target == "all":
            raise ValueError(TARGETS_REQUIRED.format(kind=operation.kind))
        else:
            targets = tuple(sorted(config.hyperparameters.lora_target.split(",")))
        if evidence is None:
            return targets, tuple((f"model.{target}", 1, 1) for target in targets)

        def targeted(name):
            return any(name == target or name.endswith("." + target) for target in targets)

        modules = tuple((item.name, item.input_dimension, item.output_dimension)
                        for item in sorted(evidence.modules, key=lambda item: item.name) if targeted(item.name))
        if not modules or any(not any(name == target or name.endswith("." + target) for name, _, _ in modules)
                              for target in targets):
            raise ValueError("simulated adapter target is absent from the inspected base modules")
        return targets, modules

    @staticmethod
    def _merge(operation: MergeOperation, verifier, roots) -> _Layout:
        """PEFT ``cat``: rank and alpha are the sum of the input ranks; A/B dimensions are concatenated."""
        descriptors = tuple(verifier.verify(ref) for ref in operation.models)
        plan = validate_merge_inputs(operation.models, descriptors, operation.weights,
                                     require_real=not operation.lineage.simulation)
        first, rank = descriptors[0], plan.output_rank
        directory = _base_directory(plan.effective_base, verifier, roots)
        shapes = {}
        for tensor in first.tensors:   # check_materializable: exactly the LoRA A/B pairs
            shapes[tensor.name] = (rank, tensor.shape[1]) if ".lora_A" in tensor.name else (tensor.shape[0], rank)
        prefix = f"{MODEL_OP_PARENT}/output"
        files = [(f"{prefix}/adapter_config.json", _json_file({
            "peft_type": "LORA", "r": rank, "lora_alpha": rank, "lora_dropout": float(first.adapter.dropout),
            "target_modules": list(plan.target_modules), "bias": "none", "fan_in_fan_out": False,
            "task_type": "CAUSAL_LM", "inference_mode": True, "base_model_name_or_path": first.root.repo_id})),
                 (f"{prefix}/adapter_model.safetensors", _Tensors(shapes)), *_snapshot_files(directory, prefix)]
        receipt = MergeReceipt(policy_id=POLICY_F32_V1, inputs=plan.inputs, normalized_weights=plan.normalized_weights,
                               output_rank=rank, max_abs_delta_error=0.0, max_abs_logits_error=0.0,
                               finite_tensors_checked=len(shapes), output_files=_output_files(files, prefix))
        return _Layout(files=tuple(files), receipt=receipt)

    @staticmethod
    def _materialize(operation: MaterializeOperation, verifier, roots, target_evidence) -> _Layout:
        adapter = verifier.verify(operation.model)
        base = adapter.effective_base
        directory = _base_directory(base, verifier, roots)
        evidence = _evidence_for(base, target_evidence, verifier)
        if evidence is None:
            raise ValueError(TARGETS_REQUIRED.format(kind=operation.kind))
        prefix = f"{MODEL_OP_PARENT}/output"
        files = [(f"{prefix}/model.safetensors", _full_weights(evidence)), *_snapshot_files(directory, prefix)]
        receipt = MaterializeReceipt(policy_id=POLICY_F32_V1, base=base, adapter=operation.model,
                                     max_abs_delta_error=0.0, max_abs_logits_error=0.0,
                                     non_target_tensors_identical=True,
                                     tokenizer_fingerprint=adapter.tokenizer_fingerprint,
                                     finite_tensors_checked=len(evidence.modules),
                                     output_files=_output_files(files, prefix))
        return _Layout(files=(*files, (TARGET_INVENTORY, _inventory(evidence))), receipt=receipt)


# -- the dispatcher ------------------------------------------------------------------------------

def _stopped_by_reconcile(result: OperationResult) -> bool:
    """The FAILED result ``reconcile`` publishes for an actor that stopped without one (no engine ever ran it)."""
    return result.status == "FAILED" and result.error == SIMULATION_STOPPED and result.engine_version is None


class SimulatedOperationDispatcher:
    """The simulated ``OperationDispatcher`` of 8.5's controller for simulation plans; the executor is always None."""
    heartbeat_seconds = 10

    def __init__(self, graph_store: GraphStore, *, root: Path, roots: RootSnapshotSource | None = None,
                 max_output_bytes: int = MAX_SIMULATED_OUTPUT_BYTES):
        from .graph_store import GraphStore
        if not isinstance(graph_store, GraphStore):
            raise TypeError("the simulated dispatcher requires the GraphStore ledger")
        self.graph, self.jobs = graph_store, graph_store.jobs
        self.root = Path(root).resolve()
        if self.root != self.jobs.path.parent.resolve():
            raise ValueError("simulated dispatcher root must be the state directory that holds jobs.db")
        self.roots = roots
        if isinstance(max_output_bytes, bool) or not isinstance(max_output_bytes, int) or max_output_bytes < 1:
            raise ValueError("the simulated output cap must be a positive number of bytes")
        self.max_output_bytes = max_output_bytes
        self.worker = SimulatedWorker()

    # -- public interface (the OperationAttemptRunner protocol) ---------------------------------

    def run_claimed(self, claimed: dict, executor, *, approval: ApprovalGrant) -> OperationOutcome:
        """Run one claimed simulated attempt once. Raises ValueError like the runner: a stale lease, an attempt
        already launched, or the ``begin_launch`` gates (``GRAPH_LAUNCH_REFUSED``/``GRAPH_CANCEL_REFUSED``, after which
        the attempt stays unlaunched with its lock until the controller calls ``return_unlaunched``)."""
        from .jobs import AttemptLease
        lease = claimed.get("lease") if isinstance(claimed, dict) else None
        if not isinstance(lease, AttemptLease):
            raise ValueError("a claimed attempt lease is required")
        job = self.jobs.get(lease.job_id)
        try:
            manifest, operation, evidence, producers = self._verify_before_launch(job, claimed, lease, executor,
                                                                                  approval)
        except Exception as exc:
            from .graph_store import LedgerIntegrityError
            if isinstance(exc, LedgerIntegrityError):
                raise   # run-wide evidence no longer verifies: returned unlaunched by the controller, uncounted
            error = self.jobs.filtered(f"{type(exc).__name__}: {exc}")
            if not self.jobs.abandon_unlaunched(lease, error):   # lock released in the same transaction
                raise   # started, legacy or no longer provisioning: never failed from here
            self._write_error(job["id"], error)
            return OperationOutcome(job["id"], lease.attempt_id, "FAILED", False, None, error=error)
        launch_id = self.jobs.begin_launch(lease)
        return self._launch(job, manifest, operation, lease, launch_id, evidence, producers)

    def reconcile(self, job_id: str, executor) -> OperationOutcome | None:
        """Settle a crashed simulated attempt that still holds its lock; never runs anything.

        FAILED after ``begin_launch``: from its published result, or with a FAILED result published here first
        (``SIMULATION_STOPPED``), and CANCELED instead after a job or run cancel. COMPLETED: rebuild and release.
        CANCELED: release on an accepted result, else None. A released attempt is final (``outputs_for`` rebuilds a
        completed one).
        """
        if executor is not None:
            raise ValueError(NO_EXECUTOR)
        job = self.jobs.get(job_id)
        manifest, operation = self._simulation(job)
        if job["status"] in _ACTIVE:
            raise ValueError("recover stale active attempts before reconciling; only FAILED, CANCELED or COMPLETED "
                             "attempts are eligible")
        lease = self.jobs.current_lease(job_id)
        if not self.jobs.holds_lock(lease):
            raise ValueError("attempt is final: its lock is released, so reconcile never reopens it; use outputs_for "
                             "to rebuild a completed attempt's outputs")
        if job["status"] == "COMPLETED":
            outcome = self.outputs_for(job_id)
            self.jobs.release_gpu(lease, execution_stopped=True)   # idempotent; a crash may have kept it
            return outcome
        attempt = self.jobs.validated_attempt(lease)
        if job["status"] == "CANCELED":
            if self._result(job_id, attempt, lease) is None:
                return None
            self.jobs.release_gpu(lease, execution_stopped=True)
            return OperationOutcome(job_id, lease.attempt_id, "CANCELED", False, None, error=job["error"])
        if job["status"] != "FAILED":
            raise ValueError("only FAILED, CANCELED or COMPLETED attempts can be reconciled")
        if not attempt["launch_started"]:
            error = job["error"] or "attempt stopped before its launch"
            self.jobs.reconcile_result(lease, "FAILED", error)   # begin_launch never ran: nothing executed
            return OperationOutcome(job_id, lease.attempt_id, "FAILED", False, None, error=error)
        launch = self._launch_dir(job_id, attempt["launch_id"])
        result = self._result(job_id, attempt, lease)
        if result is None:
            # The original actor did not publish a result. Publish the deterministic FAILED one exclusively: a
            # suspended actor can then publish nothing, and it can no longer transition a FAILED job either.
            stopped = OperationResult(status="FAILED", launch_id=launch.name, attempt_id=lease.attempt_id,
                                      manifest_sha256=manifest["sha256"],
                                      operation_sha256=digest(canonical(manifest["operation"])),
                                      kind=operation.kind, error=SIMULATION_STOPPED)
            _publish_exclusive(self._record(launch, "result"), canonical(stopped.model_dump(mode="json")))
            result = self._result(job_id, attempt, lease)   # this one, or the original actor's if it came first
            if result is None:
                raise ValueError("the published simulated result is unreadable; inspect the launch directory")
        status, error, outputs, targets = result.status, result.error, None, ()
        current = self.jobs.get(job_id)
        if current["cancel_requested"] or self.graph.get_run(job["graph_run_id"])["status"] == "CANCELED":
            # As _launch and the real runner (11.5 R1 I-1): a result settled after a cancel is CANCELED.
            status, error = "CANCELED", "cancellation requested"
        elif status == "COMPLETED":
            try:
                outputs, targets = self._outputs(job, manifest, operation, lease.attempt_id, launch, result)
            except Exception as exc:   # the result exists, so execution stopped: fail, never re-run
                status, error = "FAILED", f"{type(exc).__name__}: {exc}"
        elif error is None:
            error = f"simulated operation {status.lower()}"
        if error:
            self._write_error(job_id, error)
        if self.jobs.reconcile_result(lease, status, error) != status:   # a cancel during the freeze (re-review M1)
            status, error, outputs, targets = "CANCELED", "cancellation requested", None, ()
            self._write_error(job_id, error)
        return OperationOutcome(job_id, lease.attempt_id, status, False, outputs, targets, error)

    def failure_kind(self, job_id: str) -> FailureKind | None:
        """How a finished attempt ended, exactly as ``OperationAttemptRunner.failure_kind`` reads the same records.

        A refusal before launch released its lock in ``abandon_unlaunched``, so it is ``refused_before_launch``
        (counted, terminal); a FAILED attempt holding its lock after ``begin_launch`` is ``uncertain`` (reconcile it);
        a published FAILED result is ``remote_failed``; a COMPLETED result whose outputs were refused is
        ``output_invalid``; a failure between ``begin_launch`` and the result is ``failed_before_submit``. An attempt the
        store already settled answers from its disposition, as the runner does (8.2 review M-4).
        """
        from .graph_store import CLASSIFIED, SETTLED_AS
        job = self.jobs.get(job_id)
        if job["status"] == "COMPLETED":
            return None
        if job["status"] not in {"FAILED", "CANCELED"}:
            raise ValueError("only a finished attempt has a failure kind")
        settled = self.graph.attempt_disposition(job_id)
        if settled is not None and settled["disposition"] in CLASSIFIED:
            return settled["disposition"]
        if settled is not None and settled["disposition"] in SETTLED_AS:
            if settled["disposition"] == "released_before_contact":
                return "failed_before_submit"
            return "canceled" if job["status"] == "CANCELED" else "refused_before_launch"
        lease = self.jobs.current_lease(job_id)
        attempt = self.jobs.validated_attempt(lease)
        locked = self.jobs.holds_lock(lease)
        result = self._result(job_id, attempt, lease)
        if job["status"] == "CANCELED":
            return "canceled" if not locked or result is not None else "uncertain"
        if not attempt["launch_started"]:
            return "refused_before_launch"
        if locked:
            return "uncertain"
        if result is not None and not _stopped_by_reconcile(result):
            return "remote_failed" if result.status == "FAILED" else "output_invalid"
        # No result, or only the one reconcile published for an actor that stopped without its own: as the real
        # path after an operator confirms the stop, counted and terminal (D4; 8.4 review Important 1).
        return "failed_before_submit"

    def outputs_for(self, job_id: str) -> OperationOutcome:
        """Rebuild a completed simulated attempt's outputs from local files; identical on every call."""
        job = self.jobs.get(job_id)
        manifest, operation = self._simulation(job)
        if job["status"] != "COMPLETED":
            raise ValueError("outputs are rebuilt only for a completed attempt")
        lease = self.jobs.current_lease(job_id)
        attempt = self.jobs.validated_attempt(lease)
        result = self._result(job_id, attempt, lease)
        if result is None or result.status != "COMPLETED":
            raise ValueError("a completed simulated attempt requires its published COMPLETED result")
        launch = self._launch_dir(job_id, attempt["launch_id"])
        outputs, targets = self._outputs(job, manifest, operation, lease.attempt_id, launch, result)
        return OperationOutcome(job_id, lease.attempt_id, "COMPLETED", False, outputs, targets)

    # -- launch ---------------------------------------------------------------------------------

    def _launch(self, job, manifest, operation, lease, launch_id, evidence, producers) -> OperationOutcome:
        job_id = job["id"]
        launch = self._launch_dir(job_id, launch_id)

        def canceled():
            return bool(self.jobs.get(job_id)["cancel_requested"])

        status, error, outputs, targets = "FAILED", None, None, ()
        stop = self._heartbeat(lease)
        try:
            self.jobs.materialize_job_evidence(job_id)
            request, resolved = self._prepare(job, manifest, operation, lease, launch, evidence, producers)
            self.jobs.transition(lease, "RUNNING")   # a fenced actor stops here, before any effect
            self._write_attempt_record(launch, request)
            try:
                result = self.worker.execute(operation, launch, request, resolved, artifacts=self.graph.artifacts,
                                             roots=self.roots, target_evidence=evidence)
            except Exception as exc:   # as the real worker: a failure inside the operation is its FAILED result
                result = OperationResult(status="FAILED", launch_id=launch.name, attempt_id=request.attempt_id,
                                         manifest_sha256=request.manifest_sha256,
                                         operation_sha256=request.operation_sha256, kind=operation.kind,
                                         engine_version=ENGINE_VERSION, error=f"{type(exc).__name__}: {exc}")
            result = self._publish(launch, result)
            if canceled():
                raise CanceledError("cancellation requested")
            if result.status == "COMPLETED":
                self.jobs.transition(lease, "EVALUATING")
                outputs, targets = self._outputs(job, manifest, operation, lease.attempt_id, launch, result)
            status, error = result.status, result.error
            if status != "COMPLETED" and error is None:
                error = f"simulated operation {status.lower()}"
        except CanceledError as exc:
            status, error = "CANCELED", str(exc)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
        finally:
            stop.set()
        # Deliberately outside ``finally``: after a crash nothing below runs, and reconcile repairs the ledger.
        if status != "COMPLETED":
            outputs, targets = None, ()
        if error:
            self._write_error(job_id, error)
        if self.jobs.transition(lease, status, error) != status:   # a cancel during the freeze (11.5 re-review M1)
            status, error, outputs, targets = "CANCELED", "cancellation requested", None, ()
            self._write_error(job_id, error)
        self.jobs.release_gpu(lease, execution_stopped=True)   # simulation is synchronous: it has stopped
        return OperationOutcome(job_id, lease.attempt_id, status, False, outputs, targets, error)

    def _prepare(self, job, manifest, operation, lease, launch, evidence, producers):
        if launch.exists() or launch.is_symlink():
            raise ValueError("launch directory already exists; an attempt launches once")
        launch.mkdir(parents=True)
        (launch / "manifest.json").write_bytes(canonical(manifest))
        if isinstance(operation, TrainOperation):
            LlamaFactoryBackend().prepare(operation.config, launch, self._dataset_bytes(operation.dataset),
                                          operation.model_input)
        elif isinstance(operation, EvaluateOperation):
            write_benchmark_data(launch, self._dataset_bytes(operation.benchmark.dataset))
        continuation = (isinstance(operation, TrainOperation)
                        and operation.model_input.initialization == "continue_adapter")
        resolved = stage_model_inputs(operation, self.graph.artifacts, launch,
                                      target_evidence=evidence if continuation else (),
                                      producers=producers if continuation else ())
        request = WorkerRequest(attempt_id=lease.attempt_id, launch_id=launch.name, manifest_sha256=manifest["sha256"],
                                operation_sha256=digest(canonical(manifest["operation"])),
                                timeout_seconds=operation.resources.timeout_seconds)
        (launch / COMMAND).write_bytes(canonical(request.model_dump(mode="json")))
        return request, resolved

    @staticmethod
    def _write_attempt_record(launch: Path, request: WorkerRequest) -> None:
        """The local record of a simulated attempt that passed RUNNING: no remote path, no contact."""
        _write_new(launch / REMOTE_ATTEMPT, canonical({
            "schema_version": 1, "simulated": True, "contact": "none", "attempt_id": request.attempt_id,
            "launch_id": request.launch_id, "manifest_sha256": request.manifest_sha256}))

    def _publish(self, launch: Path, result: OperationResult) -> OperationResult:
        result = OperationResult.model_validate(result.model_dump(mode="json"))
        if result.launch_id != launch.name:
            raise ValueError("simulated result names another launch")
        if not _publish_exclusive(self._record(launch, "result"), canonical(result.model_dump(mode="json"))):
            raise ValueError(RESULT_PUBLISHED)
        return result

    def _dataset_bytes(self, ref) -> bytes:
        registry = self.graph.registry
        raw = registry.dataset_path(registry.get_dataset(ref.dataset_id, ref.version)).read_bytes()
        if digest(raw) != ref.sha256:
            raise ValueError("dataset registration differs from manifest")
        return raw

    # -- checks before launch -------------------------------------------------------------------

    def _simulation(self, job):
        """A graph-owned schema-2 simulation manifest of a simulation run, else ``SIMULATION_ONLY``."""
        manifest = job["manifest"]
        verify_manifest(manifest)
        if manifest.get("schema_version") != 2 or not job["graph_run_id"] or not job["graph_node_id"]:
            raise ValueError(SIMULATION_ONLY)
        body = manifest.get("operation")
        if not isinstance(body, dict) or body.get("kind") == "select":
            raise ValueError("local selection never dispatches to a worker")
        operation = validated_operation(body)
        if operation.model_dump(mode="json") != body:
            raise ValueError("manifest operation is not in its resolved form")
        if not operation.lineage.simulation or self.graph.get_run(job["graph_run_id"])["plan"].simulation is not True:
            raise ValueError(SIMULATION_ONLY)
        return manifest, operation

    def _verify_before_launch(self, job, claimed, lease, executor, approval):
        manifest, operation = self._simulation(job)
        if isinstance(operation, TrainOperation) and operation.config.training_method == "oft":
            raise ValueError(OFT_UNFREEZABLE)
        if manifest.get("source_sha256") != source_hash():
            raise ValueError("source hash differs from immutable manifest; submit a new job")
        if executor is not None:
            raise ValueError(NO_EXECUTOR)
        if not isinstance(approval, ApprovalGrant):
            raise TypeError("explicit typed approval required")
        run = self.graph.get_run(job["graph_run_id"])
        approval.validate_plan(run["plan"])
        entry = next(item for item in run["plan"].nodes if item.node.id == job["graph_node_id"])
        if operation.resources not in entry.allowed_resources:
            raise ValueError("operation resources differ from the approved plan")
        if approval_destination(operation.resources.executor) not in [
                item.model_dump(mode="json") for item in approval.destination_profile_revisions]:
            raise ValueError("operation destination is not an approved destination revision")
        if lease.resource_id != "simulation:" + operation.resources.facts.exclusivity_key:
            raise ValueError("attempt resource differs from the operation's simulated destination")
        self._check_datasets(manifest, operation)
        persisted = self.jobs.validated_attempt(lease)["launch_id"]
        if claimed.get("launch_id", persisted) != persisted:
            raise ValueError("claimed launch identity differs from the persisted attempt")
        evidence = producers = ()
        if not isinstance(operation, EvaluateOperation):   # from the trusted resolver hook, as compile used
            bindings = self.graph.bindings_for(job["graph_run_id"], job["graph_node_id"])
            evidence, producers = tuple(bindings.base_targets), tuple(bindings.producers)
        self.worker.check(operation, artifacts=self.graph.artifacts, roots=self.roots, target_evidence=evidence,
                          max_output_bytes=self.max_output_bytes)
        if not hard_links_supported(self._job_dir(job["id"]) / "launches"):
            raise ValueError(NO_HARD_LINKS)
        return manifest, operation, evidence, producers

    def _check_datasets(self, manifest, operation) -> None:
        """Registered bytes unchanged. Export permission is not required: simulated data never leaves the PC."""
        expected = ([operation.dataset] if isinstance(operation, TrainOperation) else
                    [operation.benchmark.dataset] if isinstance(operation, EvaluateOperation) else [])
        snapshots = [DatasetSnapshot.model_validate(item) for item in manifest.get("datasets", [])]
        if [item.dataset for item in snapshots] != expected:
            raise ValueError("manifest datasets differ from the operation")
        for snapshot in snapshots:
            if snapshot_dataset(self.graph.registry, snapshot.dataset) != snapshot:
                raise ValueError("dataset registration differs from manifest")

    # -- results and outputs --------------------------------------------------------------------

    def _result(self, job_id, attempt, lease) -> OperationResult | None:
        from .graph_store import accepted_result
        return accepted_result(self.root, job_id, attempt["launch_id"], lease.attempt_id)

    def _outputs(self, job, manifest, operation, attempt_id, launch, result):
        """The runner's own freezing: ``freeze_operation_output`` (``build_output_descriptor``) or
        ``evaluation_record``; content-addressed, so a rebuild never makes a second artifact."""
        from .graph_store import NodeOutputs
        options = dict(attempt_id=attempt_id, manifest_sha256=manifest["sha256"], launch_dir=launch)
        if isinstance(operation, EvaluateOperation):
            record = evaluation_record(operation, result, registry=self.graph.registry,
                                       approved_cache=self.graph.artifacts, **options)
            binding = NodeEvaluationBinding(node_id=job["graph_node_id"], evaluation=record,
                                            tracking_acknowledged=False)
            return NodeOutputs(evaluation=binding), ()
        resolved = ResolvedModelInputs.model_validate_json((launch / MODEL_INPUTS).read_bytes())
        synthetic = any(DatasetSnapshot.model_validate(item).synthetic for item in manifest.get("datasets", []))
        ref, targets = freeze_operation_output(operation, result, job_id=job["id"], resolved=resolved,
                                               approved_cache=self.graph.artifacts, dataset_synthetic=synthetic,
                                               **options)
        return NodeOutputs(model=NodeModelBinding(node_id=job["graph_node_id"], model=ref)), targets

    # -- ledger and file helpers ----------------------------------------------------------------

    def _heartbeat(self, lease) -> threading.Event:
        stop = threading.Event()

        def beat():
            failures = 0
            while not stop.wait(self.heartbeat_seconds):
                try:
                    self.jobs.heartbeat(lease)
                    failures = 0
                except ValueError:
                    return   # ownership or state changed; later mutations stay fenced
                except Exception as exc:   # e.g. a locked database: keep beating, never stop silently (8.4 Minor 3)
                    failures += 1
                    if failures == 1 or failures % 100 == 0:   # rate-limited (8.5 review Minor 8)
                        logging.getLogger(__name__).warning("attempt heartbeat failed %d time(s); retrying: %s",
                                                            failures, type(exc).__name__)

        threading.Thread(target=beat, daemon=True).start()
        return stop

    def _job_dir(self, job_id: str) -> Path:
        return self.root / "jobs" / job_id

    def _launch_dir(self, job_id: str, launch_id: str) -> Path:
        return self._job_dir(job_id) / "launches" / launch_id

    @staticmethod
    def _record(launch: Path, kind: str) -> Path:
        return launch.parent / f"{launch.name}.{kind}.json"

    def _write_error(self, job_id: str, error: str) -> None:
        with contextlib.suppress(OSError):
            directory = self._job_dir(job_id)
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "error.txt").write_text(self.jobs.filtered(error), encoding="utf-8")
