"""Control-plane v2 attempt runner (Task 7.5): the operation dispatcher that Task 8's controller injects.

``OperationAttemptRunner.run_claimed`` is the v2 counterpart of ``Factory.run_claimed``. For one attempt that
``GraphStore.claim_ready`` claimed, it:

1. refuses before launch (``abandon_unlaunched`` frees the lock, ``error.txt``): a non-v2 or local-selection
   manifest, simulation, a changed source, an untyped or foreign approval, an unapproved destination, an
   executor that is not the pinned destination (or is recovery-only), unexported data, or a forged launch ID;
2. reserves the launch once (``JobStore.begin_launch``) and prepares ``jobs/<job>/launches/<launch_id>/``:
   ``manifest.json``, the engine files or benchmark bytes, ``model-inputs.json`` (never weights) and
   ``command.json`` (the canonical ``WorkerRequest`` with the persisted launch ID);
3. launches through ``executor.run_operation`` with the staged ``CachedTransfer`` entries, keeps the returned
   result (``<launch_id>.result.json`` beside the launch directory, out of reach of downloads), freezes model
   outputs with exact lineage or recomputes evaluation metrics, then COMPLETED and ``release_gpu``;
4. seeds the remote model cache from the launch's own output (best effort, ``<launch_id>.cache-seed.json``).

``ExecutionUncertain`` means the remote process may still run: the attempt is FAILED with ``uncertain=True``
and keeps its lock. So does a local I/O or database error after an accepted COMPLETED result (11.5 R1 I-3): the
paid result is collected and frozen again, never discarded. ``reconcile`` never launches: it collects the same
launch and finishes the attempt (CANCELED after a job or run cancel, as ``_launch``), or returns None while there
is no result (then a canceled attempt gets the remote cancel marker). ``outputs_for`` rebuilds identical outputs
of a completed attempt from local files (freezing is content-addressed, so a rebuild never makes a second
artifact). A crash at any step therefore resumes (before ``begin_launch``) or reconciles (after it), and never
launches twice.

No MLflow, scheduling, retries or ``record_outputs`` here: those are Task 8's.
"""
from __future__ import annotations

import contextlib
import json
import logging
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import TypeAdapter

from .config import ResourceProfile
from .execution import CachedTransfer, ExecutionUncertain
from .executor_profiles import ResourceObservation, approval_destination, observation_fits
from .gpu_servers import SSHServerProfile
from .graph_planner import (
    ApprovalGrant,
    DatasetSnapshot,
    ModelTargetEvidence,
    NodeEvaluationBinding,
    NodeModelBinding,
    snapshot_dataset,
)
from .graph_store import (
    CLASSIFIED,
    REMOTE_ATTEMPT,
    SETTLED_AS,
    FailureKind,
    GraphStore,
    LedgerIntegrityError,
    NodeOutputs,
    accepted_result,
)
from .jobs import ACTIVE, AttemptLease
from .manifest import source_hash, verify_manifest
from .operation_dispatch import MODEL_OP_PARENT, write_benchmark_data
from .operation_outputs import FOREIGN_RESULT, TRAIN_OUTPUT, evaluation_record, freeze_operation_output
from .operation_runner import (
    MODEL_INPUTS,
    ResolvedModelInputs,
    _write_atomic,
    stage_model_inputs,
    validated_operation,
)
from .operations import EvaluateOperation, TrainOperation
from .registry import canonical, digest
from .training import OFT_UNFREEZABLE, CanceledError, LlamaFactoryBackend
from .worker_protocol import COMMAND, OperationResult, WorkerRequest

# Local failures that say nothing about an accepted result's content (a download, a freeze rename an antivirus holds,
# a locked ledger): the attempt stays uncertain and is collected again (11.5 R1 I-3).
TRANSIENT = (OSError, sqlite3.OperationalError)
OUTPUTS_INTERRUPTED = "the remote result was accepted but its outputs were not frozen; reconcile the same launch"


@dataclass(frozen=True)
class OperationOutcome:
    """What one attempt produced. ``uncertain`` means the lock is kept and the same launch must be reconciled.

    ``outputs`` carries no inspected targets (``GraphStore.record_outputs`` refuses them); the verified
    ``inspected_targets`` of a full-model output travel separately for Task 8's verifier.
    """
    job_id: str
    attempt_id: str
    status: Literal["COMPLETED", "FAILED", "CANCELED"]
    uncertain: bool
    outputs: NodeOutputs | None
    inspected_targets: tuple[ModelTargetEvidence, ...] = ()
    error: str | None = None


@dataclass(frozen=True)
class _PinnedResources:
    """What ``executor.validate_config`` reads: the operation's pinned resource budget."""
    resource_profile: ResourceProfile


class OperationAttemptRunner:
    heartbeat_seconds = 10

    def __init__(self, graph_store: GraphStore, *, root: Path):
        if not isinstance(graph_store, GraphStore):
            raise TypeError("the attempt runner requires the GraphStore ledger")
        self.graph, self.jobs = graph_store, graph_store.jobs
        self.root = Path(root).resolve()
        if self.root != self.jobs.path.parent.resolve():
            raise ValueError("attempt runner root must be the state directory that holds jobs.db")

    # -- public interface (Task 8) --------------------------------------------------------------

    def run_claimed(self, claimed: dict, executor, *, approval: ApprovalGrant) -> OperationOutcome:
        """Run one claimed attempt once. Raises ValueError for a stale lease, an attempt already launched, a graph
        run that is not RUNNING (``GRAPH_LAUNCH_REFUSED``) or a job-level cancel (``GRAPH_CANCEL_REFUSED``): in the
        last two cases the attempt stays unlaunched and holds its lock until the controller returns it with
        ``GraphStore.return_unlaunched``."""
        lease = claimed.get("lease") if isinstance(claimed, dict) else None
        if not isinstance(lease, AttemptLease):
            raise ValueError("a claimed attempt lease is required")
        job = self.jobs.get(lease.job_id)
        try:
            manifest, operation = self._verify_before_launch(job, claimed, lease, executor, approval)
            # The continuation's evidence comes from the trusted resolver before the launch begins (8.5 review
            # Minor 1): a ledger integrity failure leaves the attempt unlaunched for the controller to return,
            # uncounted, instead of failing it after begin_launch.
            evidence = self._continuation_evidence(job, operation)
        except LedgerIntegrityError:
            raise
        except Exception as exc:
            error = self.jobs.filtered(f"{type(exc).__name__}: {exc}")
            if not self.jobs.abandon_unlaunched(lease, error):   # a stale lease raises here
                raise   # started, legacy or no longer provisioning: never failed from here
            self._write_error(job["id"], error)
            return OperationOutcome(job["id"], lease.attempt_id, "FAILED", False, None, error=error)
        launch_id = self.jobs.begin_launch(lease)   # once per attempt: "launch already started or uncertain"
        return self._launch(job, manifest, operation, lease, launch_id, executor, evidence)

    def reconcile(self, job_id: str, executor) -> OperationOutcome | None:
        """Finish an uncertain or crashed attempt from its one launch; never launches.

        Only an attempt that still holds its lock is eligible: a FAILED attempt kept uncertain, a crashed one that
        ``JobStore.recover`` marked FAILED, or a COMPLETED or CANCELED one whose release a crash interrupted. An
        attempt whose lock is released is final and is never reopened (``outputs_for`` rebuilds a completed one's
        outputs). None: no remote result yet, or no proof whether the launch reached the host (the lock stays held
        until a result appears or an operator confirms that execution stopped).
        """
        job = self.jobs.get(job_id)
        manifest = self._manifest(job)
        if job["status"] in ACTIVE:
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
        if job["status"] == "CANCELED":
            # A crash between transition(CANCELED) and release_gpu (7.5 re-review Minor B). In ``_launch`` CANCELED
            # always follows an accepted result, so execution stopped: release only on that persisted proof for the
            # attempt's own launch ID; otherwise the lock stays for the operator's release-lock.
            if accepted_result(self.root, job_id, self._attempt(lease)["launch_id"], lease.attempt_id) is None:
                return None
            self.jobs.release_gpu(lease, execution_stopped=True)
            return OperationOutcome(job_id, lease.attempt_id, "CANCELED", False, None, error=job["error"])
        if job["status"] != "FAILED":
            raise ValueError("only FAILED, CANCELED or COMPLETED attempts can be reconciled")
        attempt = self._attempt(lease)
        if not attempt["launch_started"]:
            error = job["error"] or "attempt stopped before its launch"
            self.jobs.reconcile_result(lease, "FAILED", error)   # begin_launch never ran: no remote process
            return OperationOutcome(job_id, lease.attempt_id, "FAILED", False, None, error=error)
        launch = self._launch_dir(job_id, attempt["launch_id"])
        if not (launch / COMMAND).is_file() or not (launch / REMOTE_ATTEMPT).is_file():
            return None
        operation = validated_operation(manifest["operation"])
        # From the ledger, never from local files: the executor refuses a command.json that differs from it.
        request = self._request(lease.attempt_id, attempt["launch_id"], manifest, operation)
        # A canceled attempt without a result yet gets the remote cancel marker (11.5 R1 I-1): only _await_result
        # sent it, so a launch whose response was lost would otherwise train on to its own deadline.
        raw = executor.collect_operation_result(self._job_dir(job_id), launch, request,
                                                cancel=self._cancel_requested(job))
        if raw is None:
            return None
        result = self._accept(raw, request, launch)
        status, error, outputs, targets = result.status, result.error, None, ()
        if self._cancel_requested(self.jobs.get(job_id)):
            # As _launch (11.5 R1 I-1): a result collected after a cancel is CANCELED, whatever it says, so a late
            # completion never freezes an output, unblocks a child or registers a model.
            status, error = "CANCELED", "cancellation requested"
        elif status == "COMPLETED":
            try:
                outputs, targets = self._outputs(job, manifest, operation, lease.attempt_id, launch, result)
            except TRANSIENT:
                raise   # 11.5 R1 I-3: the lock stays held; the next reconcile downloads and freezes again (idempotent)
            except Exception as exc:   # the remote result exists, so execution stopped: fail, never retrain
                status, error = "FAILED", f"{type(exc).__name__}: {exc}"
        elif error is None:
            error = f"remote operation {status.lower()}"
        if error:
            self._write_error(job_id, error)
        self.jobs.reconcile_result(lease, status, error)
        if status == "COMPLETED":
            self._seed_cache(executor, operation, launch, outputs)
        return OperationOutcome(job_id, lease.attempt_id, status, False, outputs, targets, error)

    def failure_kind(self, job_id: str) -> FailureKind | None:
        """How a finished attempt ended, from the ledger and this runner's persisted records; None for COMPLETED.

        - CANCELED: ``canceled`` once its lock is released or its accepted result exists (then
          ``GraphStore.classify_attempt`` may release it); otherwise ``uncertain``.
        - FAILED, never launched (``launch_started=0``): ``refused_before_launch``. The store counts a refusal by
          the runner's checks (lock already released) and not a stale recover of a gated attempt (lock held).
        - FAILED holding its lock: ``uncertain`` (reconcile the same launch).
        - FAILED with an accepted result: ``remote_failed`` for a FAILED result, else ``output_invalid``.
        - FAILED after ``begin_launch`` without a result: ``failed_before_submit``.

        An attempt the store already settled answers from its disposition, so the two never contradict each other
        (8.2 review M-4): a classified kind as recorded, ``released_before_contact`` as ``failed_before_submit`` and
        ``returned_unlaunched`` as ``canceled`` (or ``refused_before_launch``); ``classify_attempt`` accepts those
        equivalents as no-ops.
        """
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
        attempt = self._attempt(lease)
        held = self.jobs.holds_lock(lease)
        result = accepted_result(self.root, job_id, attempt["launch_id"], lease.attempt_id)
        if job["status"] == "CANCELED":
            return "canceled" if not held or result is not None else "uncertain"
        if not attempt["launch_started"]:
            return "refused_before_launch"
        if held:
            return "uncertain"
        if result is not None:
            return "remote_failed" if result.status == "FAILED" else "output_invalid"
        return "failed_before_submit"

    def outputs_for(self, job_id: str) -> OperationOutcome:
        """Rebuild a completed attempt's outputs from local files; identical on every call."""
        job = self.jobs.get(job_id)
        manifest = self._manifest(job)
        if job["status"] != "COMPLETED":
            raise ValueError("outputs are rebuilt only for a completed attempt")
        lease = self.jobs.current_lease(job_id)
        launch = self._launch_dir(job_id, self._attempt(lease)["launch_id"])
        result = OperationResult.model_validate_json(self._record(launch, "result").read_bytes())
        operation = validated_operation(manifest["operation"])
        outputs, targets = self._outputs(job, manifest, operation, lease.attempt_id, launch, result)
        return OperationOutcome(job_id, lease.attempt_id, "COMPLETED", False, outputs, targets)

    # -- launch ---------------------------------------------------------------------------------

    def _launch(self, job, manifest, operation, lease, launch_id, executor, evidence=((), ())) -> OperationOutcome:
        job_id = job["id"]
        launch = self._launch_dir(job_id, launch_id)

        def canceled():
            return bool(self.jobs.get(job_id)["cancel_requested"])

        status, error, uncertain, outputs, targets, result = "FAILED", None, False, None, (), None
        stop = self._heartbeat(lease)
        try:
            self.jobs.materialize_job_evidence(job_id)
            request, transfers = self._prepare(job, manifest, operation, lease, launch, evidence)
            self.jobs.transition(lease, "RUNNING")
            raw = executor.run_operation(self._job_dir(job_id), launch, request, transfers, canceled)
            result = self._accept(raw, request, launch)
            if canceled():
                raise CanceledError("cancellation requested")
            if result.status == "COMPLETED":
                self.jobs.transition(lease, "EVALUATING")
                outputs, targets = self._outputs(job, manifest, operation, lease.attempt_id, launch, result)
            status, error = result.status, result.error
            if status != "COMPLETED" and error is None:
                error = f"remote operation {status.lower()}"
        except CanceledError as exc:
            status, error = "CANCELED", str(exc)
        except ExecutionUncertain as exc:
            error, uncertain = str(exc), True
        except Exception as exc:
            error = self.jobs.filtered(f"{type(exc).__name__}: {exc}")
            if isinstance(exc, TRANSIENT) and result is not None and result.status == "COMPLETED":
                # 11.5 R1 I-3: the paid result is accepted, but a local I/O or database error stopped its freeze.
                # Keep the lock, so reconcile collects and freezes the same launch again (both idempotent); only a
                # content error (a ValueError: tampered, refused or invalid output) is final.
                error, uncertain = f"{OUTPUTS_INTERRUPTED}: {error}", True
        finally:
            stop.set()
        # Deliberately outside ``finally``: after a crash nothing below runs, and reconcile repairs the ledger.
        if status != "COMPLETED":
            outputs, targets = None, ()
        if error:
            self._write_error(job_id, error)
        self.jobs.transition(lease, status, error)
        if not uncertain:
            self.jobs.release_gpu(lease, execution_stopped=True)   # a result, or no launch, proves it stopped
        if status == "COMPLETED":
            self._seed_cache(executor, operation, launch, outputs)
        return OperationOutcome(job_id, lease.attempt_id, status, uncertain, outputs, targets, error)

    def _prepare(self, job, manifest, operation, lease, launch, evidence=((), ())):
        if launch.exists() or launch.is_symlink():
            raise ValueError("launch directory already exists; an attempt launches once")
        launch.mkdir(parents=True)
        (launch / "manifest.json").write_bytes(canonical(manifest))
        if isinstance(operation, TrainOperation):
            LlamaFactoryBackend().prepare(operation.config, launch, self._dataset_bytes(operation.dataset),
                                          operation.model_input)
        elif isinstance(operation, EvaluateOperation):
            write_benchmark_data(launch, self._dataset_bytes(operation.benchmark.dataset))
        targets, producers = evidence
        resolved = stage_model_inputs(operation, self.graph.artifacts, launch, target_evidence=targets,
                                      producers=producers)
        request = self._request(lease.attempt_id, launch.name, manifest, operation)
        (launch / COMMAND).write_bytes(canonical(request.model_dump(mode="json")))
        store = self.graph.artifacts.root
        transfers = tuple(CachedTransfer(ref=ref, local_location=store / ref.artifact_id / ref.inventory_sha256)
                          for ref in resolved.transfers)
        return request, transfers

    @staticmethod
    def _request(attempt_id: str, launch_id: str, manifest: dict, operation) -> WorkerRequest:
        """The attempt's ``WorkerRequest``, derived only from the ledger: attempt, launch and manifest."""
        return WorkerRequest(attempt_id=attempt_id, launch_id=launch_id, manifest_sha256=manifest["sha256"],
                             operation_sha256=digest(canonical(manifest["operation"])),
                             timeout_seconds=operation.resources.timeout_seconds)

    def _continuation_evidence(self, job, operation):
        """Inspected targets and producer stages for an adapter continuation, from the resolver compile used."""
        if not (isinstance(operation, TrainOperation) and operation.model_input.initialization == "continue_adapter"):
            return (), ()
        bindings = self.graph.bindings_for(job["graph_run_id"], job["graph_node_id"])   # the trusted resolver hook
        return bindings.base_targets, bindings.producers

    def _dataset_bytes(self, ref) -> bytes:
        registry = self.graph.registry
        raw = registry.dataset_path(registry.get_dataset(ref.dataset_id, ref.version)).read_bytes()
        if digest(raw) != ref.sha256:
            raise ValueError("dataset registration differs from manifest")
        return raw

    # -- checks before launch -------------------------------------------------------------------

    def _manifest(self, job) -> dict:
        manifest = job["manifest"]
        verify_manifest(manifest)
        if manifest.get("schema_version") != 2:
            raise ValueError("the attempt runner executes schema 2 operation manifests only")
        if not job["graph_run_id"] or not job["graph_node_id"]:
            raise ValueError("an operation attempt requires a graph-owned job")
        return manifest

    def _verify_before_launch(self, job, claimed, lease, executor, approval):
        manifest = self._manifest(job)
        body = manifest.get("operation")
        if not isinstance(body, dict) or body.get("kind") == "select":
            raise ValueError("local selection never dispatches to a GPU worker")
        operation = validated_operation(body)
        if operation.model_dump(mode="json") != body:
            raise ValueError("manifest operation is not in its resolved form")
        if operation.lineage.simulation:
            raise ValueError("simulation manifest cannot authorize GPU execution")
        if isinstance(operation, TrainOperation) and operation.config.training_method == "oft":
            raise ValueError(OFT_UNFREEZABLE)   # for plans approved before planning refused it
        if manifest.get("source_sha256") != source_hash():
            raise ValueError("source hash differs from immutable manifest; submit a new job")
        if getattr(executor, "recovery_only", False):
            raise ValueError("recovery-only executor cannot launch an operation")
        if not getattr(executor, "remote", False):
            raise ValueError("a real operation requires the approved remote executor")
        if getattr(executor, "model_cache_root", None) is None:
            raise ValueError("v2 operations require a configured remote model cache")
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
        self._check_destination(operation.resources, executor, lease)
        self._check_datasets(manifest, operation)
        persisted = self._attempt(lease)["launch_id"]
        if claimed.get("launch_id", persisted) != persisted:
            raise ValueError("claimed launch identity differs from the persisted attempt")
        return manifest, operation

    @staticmethod
    def _check_destination(resources, executor, lease) -> None:
        profile, pinned = resources.executor, resources.facts
        if lease.resource_id != pinned.exclusivity_key:
            raise ValueError("attempt resource differs from the operation's pinned destination")
        mismatch = ValueError("executor destination differs from the operation's pinned destination")
        if isinstance(profile, SSHServerProfile):
            facts = getattr(executor, "server_facts", None)
            if getattr(executor, "server_profile", None) != profile or facts is None or not observation_fits(
                    TypeAdapter(ResourceObservation).validate_python(facts.model_dump()), pinned):
                raise mismatch
        elif (getattr(executor, "server_profile", None) is not None
              or (getattr(executor, "pod", None) or {}).get("id") != profile.pod_id):
            raise mismatch
        # The profile a provider opened this executor for (8.5 review Important 1): an alias of the same Pod or
        # server is not the pinned destination, so a changed or deleted profile never runs through another one.
        resolved_for = getattr(executor, "destination_profile", None)
        if resolved_for is not None and resolved_for != profile:
            raise mismatch
        # Live GPU type, count and price against the pinned budget (RunPod Pod or observed SSH selection).
        executor.validate_config(_PinnedResources(pinned.legacy_resources(resources.timeout_seconds)))

    def _check_datasets(self, manifest, operation) -> None:
        expected = ([operation.dataset] if isinstance(operation, TrainOperation) else
                    [operation.benchmark.dataset] if isinstance(operation, EvaluateOperation) else [])
        snapshots = [DatasetSnapshot.model_validate(item) for item in manifest.get("datasets", [])]
        if [item.dataset for item in snapshots] != expected:
            raise ValueError("manifest datasets differ from the operation")
        for snapshot in snapshots:
            if snapshot_dataset(self.graph.registry, snapshot.dataset) != snapshot:
                raise ValueError("dataset registration differs from manifest")
            if not snapshot.export_allowed:
                raise ValueError("dataset export has not been authorized in registry")

    # -- results and outputs --------------------------------------------------------------------

    def _accept(self, raw, request: WorkerRequest, launch: Path) -> OperationResult:
        """Keep the executor's identity-checked result beside the launch directory (downloads never reach it).

        Anything but a valid result naming this launch leaves the attempt uncertain: the remote process may run.
        """
        try:
            result = OperationResult.model_validate(raw)
        except ValueError as exc:
            raise ExecutionUncertain("executor returned no valid operation result; reconcile the same launch") from exc
        identities = (result.attempt_id, result.manifest_sha256, result.operation_sha256)
        if result.launch_id != request.launch_id or result.status == "COMPLETED" and identities != (
                request.attempt_id, request.manifest_sha256, request.operation_sha256):
            raise ExecutionUncertain(FOREIGN_RESULT)
        if result.error:   # the remote's own text may echo a credential the controller knows (8.5 review Minor 7)
            result = result.model_copy(update={"error": self.jobs.filtered(result.error)})
        _write_atomic(self._record(launch, "result"), canonical(result.model_dump(mode="json")))
        return result

    def _outputs(self, job, manifest, operation, attempt_id, launch, result):
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

    def _seed_cache(self, executor, operation, launch: Path, outputs) -> None:
        """Seed the destination's model cache from this launch's remote output, so it is never re-uploaded.

        An optimization only: the output is frozen and the execution stopped, so a failure is recorded, not raised.
        """
        if outputs is None or outputs.model is None or getattr(executor, "recovery_only", False):
            return
        ref, source = outputs.model.model, None
        try:
            remote = json.loads((launch / REMOTE_ATTEMPT).read_bytes())["remote_path"]
            source = remote + "/" + (TRAIN_OUTPUT if isinstance(operation, TrainOperation)
                                     else f"{MODEL_OP_PARENT}/output")
            location = self.graph.artifacts.root / ref.artifact_id / ref.inventory_sha256
            executor.publish_cached_artifact(CachedTransfer(ref=ref, local_location=location),
                                             source_remote_dir=source, launch_id=launch.name)
            status, error = "published", None
        except Exception as exc:
            status, error = "failed", f"{type(exc).__name__}: {exc}"
        _write_atomic(self._record(launch, "cache-seed"), canonical({
            "status": status, "artifact": ref.model_dump(mode="json"), "source_remote_dir": source, "error": error}))

    # -- ledger and file helpers ----------------------------------------------------------------

    def _cancel_requested(self, job) -> bool:
        """A job-level cancel, or a canceled run (a run canceled before 11.5 never flagged an uncertain attempt)."""
        return bool(job["cancel_requested"]) or self.graph.get_run(job["graph_run_id"])["status"] == "CANCELED"

    def _attempt(self, lease: AttemptLease) -> dict:
        return self.jobs.validated_attempt(lease)

    def _heartbeat(self, lease: AttemptLease) -> threading.Event:
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
