"""Task 8.5: the persistent graph controller (plan §4 "8.5"; decisions D1-D14 as accepted).

One controller per state directory, fenced by the owner lease, schedules every managed graph run from the ledger:

- The DB is the source of truth. Each tick settles every job from its rows, so a crash and normal operation take the
  same path. Attempts that are in flight in this process are never touched.
- Ready nodes compile through the gated ``prepare_ready`` (the trusted ``LedgerBindingResolver`` pins each to an
  approved destination, D1). A run-wide ledger integrity error fails nothing: it becomes the ready nodes' wait reason.
- Local selections complete in one ledger transaction. GPU claims are made on the destination the resolver pinned,
  only after the provider re-checked it, and each claimed attempt runs once on its own dispatch thread. A refused
  launch (pause, cancel) is returned unlaunched at once, from the dispatch thread.
- Reconcile runs only for an attempt that still holds its lock, and only after ``takeover_attempt`` fenced the old
  actor. Only an accepted remote FAILED result is retried (D4); pause, cancel, restart and reconcile never train twice.
- A resource stays busy until its dispatch thread returns, cache seeding after ``release_gpu`` included (D8).
- Tracking is repaired through the outbox (8.3). Under ``run_forever`` it runs on its own thread, never concurrently,
  while the tick thread keeps renewing the owner and run leases. A heartbeat renews the owner lease, the run leases
  and the tracking claim being sent while a long upload or create runs.

``graph_status`` is the DB-only, secret-free view that Task 9 and Task 10 poll without a controller object.
"""
from __future__ import annotations

import collections
import contextlib
import json
import logging
import math
import re
import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Protocol

from .executor_profiles import SimulatedExecutorProvider, observation_fits
from .gpu_servers import SSHServerFacts
from .graph_store import (
    LEDGER_INTEGRITY,
    REMOTE_ATTEMPT,
    STALE_OWNER,
    UNPRICED_ATTEMPT,
    GraphStore,
    LedgerBindingResolver,
    LedgerIntegrityError,
    OwnerLease,
)
from .jobs import GRAPH_CANCEL_REFUSED, GRAPH_LAUNCH_REFUSED, NON_HARDWARE
from .operations import OperationResources
from .tracking import CONTROLLER_HTTP_MAX_RETRIES, CONTROLLER_HTTP_TIMEOUT_SECONDS, LazyMLflowTracker
from .tracking_outbox import LocalEvidenceSink, TrackingOutbox, TrackingPublisher, redact

Clock = Callable[[], float]
LOG = logging.getLogger(__name__)
ACTIVE = ("PROVISIONING", "RUNNING", "EVALUATING")
UNFINISHED_NODES = ("PENDING", "READY", "RUNNING")
REFUSED_LAUNCH = (GRAPH_LAUNCH_REFUSED, GRAPH_CANCEL_REFUSED)
FINAL_ATTEMPT = "attempt is final"
STALE_LEASE = "stale controller lease"
CONTROLLER_REQUEST_LIMITS = (CONTROLLER_HTTP_MAX_RETRIES, CONTROLLER_HTTP_TIMEOUT_SECONDS)

# Wait reasons: the controller's own words, shown by graph_status (never a credential, a remote path or a setting).
OPERATOR_CONFIRMATION = "operator confirmation required: release-lock --confirm-execution-stopped"
# 8.5 review Minor 6: before the launch's time limit plus this margin a late result may still arrive, so the operator
# is not asked to release the lock (that would free the host for a second job and discard the result).
RESULT_MARGIN_SECONDS = 600
AWAITING_RESULT = "waiting for the result of the same launch (its time limit has not passed)"
RESULT_OVERDUE = ("no result after the launch's time limit; if its remote execution stopped, "
                  + OPERATOR_CONFIRMATION)
REPLAN = "live destination facts differ from the approved plan (GPU or a higher price); re-plan before running"
BUDGET_EXHAUSTED = ("approved budget exhausted: {reserved:.6g} of {approved:.6g} USD already reserved (historical "
                    "attempts reserved by the Task 8 migration count too); re-plan with a larger budget")
CROSS_PROVIDER = ("waiting for a cross-provider hardware lock (D3 quarantine): another provider holds hardware "
                  "that cannot be told apart from this destination")
DESTINATION_BUSY = "waiting for destination {key}"
AT_CONCURRENCY = "waiting: the run is at its max_concurrent limit"
THREADS_BUSY = "waiting: every dispatch thread of this controller is busy"
RETRY_WAIT = "waiting for the retry backoff (attempt {next} of {max})"
RECONCILE_UNAVAILABLE = "reconcile could not reach the destination: {error}"
OUTPUTS_REFUSED = "the completed attempt's outputs could not be recorded; an operator must inspect it: {error}"
DESTINATION_UNAVAILABLE = "destination unavailable: {error}"
DISPATCH_FAILED = "dispatch failed: {error}"
SETTLE_FAILED = "settling the attempt failed; retrying next tick: {error}"
DESTINATION_PROFILE_DIFFERS = "the opened destination is not this job's pinned profile; re-plan before running"
DISPATCHER_STORE_DIFFERS = "the operation dispatcher must use the graph controller's own GraphStore"
RETURNED_AFTER_TAKEOVER = "returned unlaunched after takeover: the graph run is not running"
STOPPING = "returned unlaunched after takeover: the controller is stopping"
OPERATOR_PREFIXES = (OUTPUTS_REFUSED.split(":")[0], LEDGER_INTEGRITY.split(";")[0], OPERATOR_CONFIRMATION,
                     RESULT_OVERDUE.split(";")[0])
_REMOTE_PATH = re.compile(r"(?<![\w.:/-])/(?:[\w.-]+/)+[\w.-]*")
_PROVIDERS = ("existing_runpod:", "ssh_server:")


def _display(text, limit=500) -> str:
    """Text that may be shown or stored as a wait reason: credentials redacted and remote POSIX paths removed."""
    return _REMOTE_PATH.sub("[path]", redact(str(text), 4000))[:limit]


def _describe(exc) -> str:
    return _display(f"{type(exc).__name__}: {exc}")


@dataclass(frozen=True)
class ControllerSettings:
    """``lease_heartbeat_seconds``: how often (wall seconds) the heartbeat renews the owner lease, the run leases and
    the tracking claim being sent while a tick or a tracking pass blocks (long uploads, SSH connects). The tracking
    claim TTL must exceed the longest single MLflow upload or create the heartbeat cannot cover (a dead controller),
    and the controller's own tracker bounds each create request well below it (``CONTROLLER_REQUEST_LIMITS``)."""
    lease_ttl_seconds: float = 60
    tick_seconds: float = 5
    stale_attempt_seconds: float = 120
    reconcile_interval_seconds: float = 60
    max_dispatch_threads: int = 8
    lease_heartbeat_seconds: float = 20
    tracking_claim_ttl_seconds: float = 600
    tracking_batch: int = 16
    destination_retry_seconds: float = 60   # a refused or unreachable destination is not reopened sooner

    def __post_init__(self):
        for name in ("lease_ttl_seconds", "tick_seconds", "stale_attempt_seconds", "reconcile_interval_seconds",
                     "lease_heartbeat_seconds", "tracking_claim_ttl_seconds", "destination_retry_seconds"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be positive and finite")
        for name in ("max_dispatch_threads", "tracking_batch"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if self.lease_heartbeat_seconds * 2 > self.lease_ttl_seconds:
            raise ValueError("the lease heartbeat must be at most half the lease TTL")
        if self.lease_heartbeat_seconds * 3 > self.tracking_claim_ttl_seconds:
            raise ValueError("the tracking claim TTL must cover at least three lease heartbeats")


class OperationDispatcher(Protocol):
    """``OperationAttemptRunner`` and ``SimulatedOperationDispatcher``. Outcomes are used structurally (the two
    ``OperationOutcome`` classes are field-for-field copies), never by type."""

    def run_claimed(self, claimed, executor, *, approval): ...

    def reconcile(self, job_id, executor): ...

    def outputs_for(self, job_id): ...

    def failure_kind(self, job_id): ...


class DispatchPool(Protocol):
    def submit(self, key: str, fn: Callable[[], object]) -> Future: ...


class ThreadDispatchPool:
    """One thread per in-flight attempt, up to ``max_workers``; the controller never claims beyond that."""

    def __init__(self, max_workers: int = 8):
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="graph-dispatch")

    def submit(self, key: str, fn):
        return self._executor.submit(fn)

    def shutdown(self, wait: bool = False) -> None:
        self._executor.shutdown(wait=wait)


class DispatchRouter:
    """Routes each attempt by its manifest's ``lineage.simulation``: the real runner or the simulated dispatcher."""

    def __init__(self, real, simulated, *, store: GraphStore | None = None):
        self.real, self.simulated = real, simulated
        store = store or getattr(real, "graph", None) or getattr(simulated, "graph", None)
        if not isinstance(store, GraphStore):
            raise TypeError("the dispatch router requires the GraphStore ledger")
        self.store = store

    def _route(self, manifest):
        simulation = bool(manifest["operation"]["lineage"]["simulation"])
        target = self.simulated if simulation else self.real
        if target is None:
            raise ValueError(f"no dispatcher is configured for {'simulation' if simulation else 'real'} graph runs")
        return target

    def _for(self, job_id):
        return self._route(self.store.jobs.get(job_id)["manifest"])

    def run_claimed(self, claimed, executor, *, approval):
        return self._route(claimed["manifest"]).run_claimed(claimed, executor, approval=approval)

    def reconcile(self, job_id, executor):
        return self._for(job_id).reconcile(job_id, executor)

    def outputs_for(self, job_id):
        return self._for(job_id).outputs_for(job_id)

    def failure_kind(self, job_id):
        return self._for(job_id).failure_kind(job_id)


@dataclass(frozen=True)
class TickReport:
    prepared: tuple[str, ...] = ()
    claimed: tuple[str, ...] = ()
    settled: tuple[str, ...] = ()
    reconciled: tuple[str, ...] = ()
    retried: tuple[str, ...] = ()
    blocked: tuple[str, ...] = ()
    released: tuple[str, ...] = ()
    tracking_processed: int = 0


@dataclass
class _InFlight:
    job_id: str
    run_id: str
    node_id: str
    resource: str
    facts: object
    future: Future
    lease: object


@dataclass
class _Report:
    lists: dict = field(default_factory=lambda: {name: [] for name in (
        "prepared", "claimed", "settled", "reconciled", "retried", "blocked", "released")})
    tracking_processed: int = 0

    def add(self, name, *values):
        self.lists[name].extend(values)

    def freeze(self) -> TickReport:
        return TickReport(**{name: tuple(dict.fromkeys(values)) for name, values in self.lists.items()},
                          tracking_processed=self.tracking_processed)


def controller_tracker(uri: str) -> LazyMLflowTracker:
    """The tracker Task 9's broker gives the controller: built lazily, with each create request bounded."""
    return LazyMLflowTracker(uri, bounded_requests=CONTROLLER_REQUEST_LIMITS)


def _bound_requests(tracker) -> None:
    """The controller's own tracker bounds every create request (8.3 review Important 1); v1 trackers never do."""
    if tracker is None or getattr(tracker, "bounded_requests", "absent") is not None:
        return
    tracker.bounded_requests = CONTROLLER_REQUEST_LIMITS
    built = vars(tracker).get("_tracker")
    if built is not None and getattr(built, "bounded_requests", "absent") is None:
        built.bounded_requests = CONTROLLER_REQUEST_LIMITS


def _dispatcher_parts(dispatcher, seen=None):
    """The dispatcher and, structurally, a router's real and simulated dispatchers."""
    seen = seen if seen is not None else set()
    if dispatcher is None or id(dispatcher) in seen:
        return []
    seen.add(id(dispatcher))
    parts = [dispatcher]
    for name in ("real", "simulated"):
        parts += _dispatcher_parts(getattr(dispatcher, name, None), seen)
    return parts


def _dispatcher_stores(dispatcher):
    return [value for part in _dispatcher_parts(dispatcher) for value in (
        getattr(part, "graph", None), getattr(part, "store", None)) if isinstance(value, GraphStore)]


def _dispatcher_jobs(dispatcher):
    from .jobs import JobStore
    return [value for part in _dispatcher_parts(dispatcher) for value in [getattr(part, "jobs", None)]
            if isinstance(value, JobStore)]


def _provider_prefix(resource):
    return next((prefix for prefix in _PROVIDERS if resource.startswith(prefix)), None)


def _conflicts(held, held_facts, resource, facts) -> bool:
    """``JobStore._resource_blocked``'s rule, applied to a resource whose dispatch thread has not returned (D8): its
    DB lock may already be released (cache seeding), yet the host stays exclusive until the thread returns."""
    if held == resource:
        return True
    if held == "mock-cpu" or held.startswith(NON_HARDWARE) or resource.startswith(NON_HARDWARE):
        return False
    mine = _provider_prefix(resource)
    if mine is None:
        return False
    if _provider_prefix(held) != mine:
        return True   # D3: hardware of another provider cannot be told apart
    if mine == "ssh_server:":
        if not isinstance(facts, SSHServerFacts) or not isinstance(held_facts, SSHServerFacts):
            return True
        return held_facts.server_id == facts.server_id or bool(
            {gpu.uuid for gpu in held_facts.inventory} & {gpu.uuid for gpu in facts.inventory})
    return False


_UNKNOWN = object()


class GraphController:
    """The one persistent graph controller of a state directory (see the module docstring)."""

    def __init__(self, store: GraphStore, executor_provider, operation_dispatcher, tracker, clock: Clock, *,
                 simulated_provider=None, outbox: TrackingOutbox | None = None, pool: DispatchPool | None = None,
                 settings: ControllerSettings = ControllerSettings()):
        if not isinstance(store, GraphStore):
            raise TypeError("the graph controller requires the GraphStore ledger")
        if not isinstance(settings, ControllerSettings):
            raise TypeError("ControllerSettings required")
        if not callable(clock):
            raise TypeError("an injected wall clock is required")
        if any(item is not store for item in _dispatcher_stores(operation_dispatcher)) or any(
                item is not store.jobs for item in _dispatcher_jobs(operation_dispatcher)):
            # The error filter is installed on this store's JobStore: a dispatcher on another instance would write
            # unredacted errors (re-review Minor 3).
            raise ValueError(DISPATCHER_STORE_DIFFERS)
        self.store, self.provider, self.dispatcher = store, executor_provider, operation_dispatcher
        self.tracker, self.clock, self.settings = tracker, clock, settings
        self.simulated_provider = simulated_provider
        store.binding_resolver = LedgerBindingResolver(store)   # the trusted path compiles from verified ledger evidence
        _bound_requests(tracker)
        self.outbox = outbox or TrackingOutbox(store, clock=clock, claim_ttl_seconds=settings.tracking_claim_ttl_seconds)
        if self.outbox.store is not store:
            raise ValueError("the tracking outbox belongs to another GraphStore")
        self.publisher = TrackingPublisher(self.outbox, store, tracker, local_sink=LocalEvidenceSink(store.state_dir))
        self._own_pool = pool is None
        self.pool = pool if pool is not None else ThreadDispatchPool(settings.max_dispatch_threads)
        self._lock = threading.RLock()   # _inflight is read by status() from other threads (8.5 review Minor 8)
        self._inflight: dict[str, _InFlight] = {}
        self._outcomes: dict[str, object] = {}
        self._job_runs: dict[str, str] = {}
        self._refused: dict = {}   # (run, profile, revision) -> (retry at, reason): destination backoff
        self._run_leases: dict = {}
        self._reconciled_at: dict[str, float] = {}
        self._simulated: dict[str, SimulatedExecutorProvider] = {}
        self._reasons: dict = {}
        self._skipped: dict[str, str] = {}
        self._wake = threading.Event()
        self._stopping = False
        self._owner: OwnerLease | None = None
        self._tracking_thread: threading.Thread | None = None
        self._tracking_stop = threading.Event()
        self.errors: collections.deque = collections.deque(maxlen=20)
        # Every error text an executor or worker returns passes through this filter before the ledger stores it
        # (8.5 review Minor 7): the credentials only the providers hold, then MLflow's and bearer-style ones. It is
        # installed on this store's JobStore instance, so the last controller constructed on a store wins.
        store.jobs.error_filter = self.redact

    def redact(self, text) -> str:
        """``text`` without the credentials this controller's providers hold or MLflow's configured ones."""
        text = str(text)
        for provider in (self.provider, self.simulated_provider):
            redactor = getattr(provider, "redact", None)
            if callable(redactor):
                text = redactor(text)
        return redact(text, 4000)

    # -- leases ---------------------------------------------------------------------------------------

    def acquire_controller(self, owner_id: str) -> OwnerLease:
        """The owner lease (``controller already running`` for a duplicate), then the outbox backfill once."""
        owner = self.store.acquire_owner(owner_id, ttl_seconds=self.settings.lease_ttl_seconds)
        self._owner = owner
        self.outbox.backfill(owner)   # robust to unreadable rows (8.3 review Minor 7)
        return owner

    def _managed_runs(self) -> list[str]:
        """Runs with work left: RUNNING or PAUSED, an unfinished node, a held lock, a PENDING tracking event, or
        tracking events but no graph summary yet (a crash between the last settle and ``finalize_run``). A run that
        predates the outbox and is finished is left alone."""
        with self.store.jobs.connection() as db:
            return [row[0] for row in db.execute(
                "SELECT r.id FROM graph_runs r WHERE r.status IN ('RUNNING','PAUSED') "
                "OR EXISTS (SELECT 1 FROM nodes n WHERE n.run_id=r.id AND n.status IN ('PENDING','READY','RUNNING')) "
                "OR EXISTS (SELECT 1 FROM gpu_locks g JOIN jobs j ON g.job_id=j.id WHERE j.graph_run_id=r.id) "
                "OR EXISTS (SELECT 1 FROM tracking_events e WHERE e.run_id=r.id AND e.status='PENDING') "
                "OR (EXISTS (SELECT 1 FROM tracking_events t WHERE t.run_id=r.id) AND NOT EXISTS "
                "(SELECT 1 FROM tracking_events s WHERE s.run_id=r.id AND s.kind='graph_summary')) "
                "ORDER BY r.created, r.id")]

    def _leases(self, owner) -> dict:
        """Phase 1: renew the owner lease (a refusal stops the controller), then hold a run lease per managed run."""
        ttl = self.settings.lease_ttl_seconds
        self.store.renew_owner(owner, ttl_seconds=ttl)
        leases = {}
        for run_id in self._managed_runs():
            lease = self._run_leases.get(run_id)
            if lease is not None:
                try:
                    self.store.heartbeat_controller(lease, ttl)
                    leases[run_id] = lease
                    continue
                except ValueError:
                    self._run_leases.pop(run_id, None)
            try:
                lease = self.store.acquire_controller(run_id, ttl, owner=owner)
            except ValueError as exc:
                if str(exc) == STALE_OWNER:
                    raise
                self._skipped[run_id] = str(exc)   # an unexpired pre-Task-8 lease: wait until it expires
                continue
            self._skipped.pop(run_id, None)
            self._run_leases[run_id] = leases[run_id] = lease
        for run_id in set(self._run_leases) - set(leases):
            self._run_leases.pop(run_id, None)
        self._prune(set(leases))
        return leases

    def _prune(self, managed) -> None:
        """Forget in-process facts of runs that are no longer managed (8.5 review Minor 8)."""
        with self._lock:
            busy_runs = {entry.run_id for entry in self._inflight.values()}
        keep = set(managed) | busy_runs
        for key in [key for key in self._reasons if key[0] not in keep]:
            del self._reasons[key]
        for run_id in [run_id for run_id in self._simulated if run_id not in keep]:
            del self._simulated[run_id]
        for key in [key for key in self._refused if key[0] not in keep]:
            del self._refused[key]
        for job_id in [job_id for job_id, run_id in self._job_runs.items() if run_id not in keep]:
            self._job_runs.pop(job_id, None)
            self._outcomes.pop(job_id, None)
            self._reconciled_at.pop(job_id, None)

    @contextlib.contextmanager
    def _heartbeat(self, owner, leases):
        """Renew the owner lease, the run leases and the tracking claim being sent while this block runs."""
        stop = threading.Event()
        ttl = self.settings.lease_ttl_seconds

        def beat():
            failures = 0
            while not stop.wait(self.settings.lease_heartbeat_seconds):
                try:
                    self.store.renew_owner(owner, ttl_seconds=ttl)
                    failures = 0
                except ValueError:
                    return   # fenced: the next tick stops the controller
                except Exception as exc:
                    failures += 1
                    if failures == 1 or failures % 100 == 0:   # rate-limited (8.5 review Minor 8)
                        LOG.warning("controller heartbeat failed %d time(s); retrying: %s", failures,
                                    type(exc).__name__)
                    continue
                for lease in list(leases.values()):
                    with contextlib.suppress(Exception):
                        self.store.heartbeat_controller(lease, ttl)
                with contextlib.suppress(Exception):
                    self.publisher.renew_current()

        thread = threading.Thread(target=beat, name="graph-controller-heartbeat", daemon=True)
        thread.start()
        try:
            yield
        finally:
            stop.set()
            thread.join()

    # -- the tick -------------------------------------------------------------------------------------

    def tick(self, controller_lease: OwnerLease) -> TickReport:
        """One idempotent pass: leases, harvest, settle, prepare, local selections, GPU claims, tracking, finalize.
        Phases 5-6 are skipped while stopping; tracking runs here unless ``run_forever``'s tracking thread runs."""
        owner, report = controller_lease, _Report()
        leases = self._leases(owner)
        with self._heartbeat(owner, leases):
            self._harvest(report)
            for lease in list(leases.values()):
                try:
                    self._settle(lease, report)
                    run = self.store.get_run(lease.run_id)
                    if run["status"] == "RUNNING":
                        self._prepare(lease, report)
                        if not self._stopping:
                            self._local_selections(lease, report)
                            self._claims(lease, run, report)
                except Exception as exc:
                    self._run_failed(lease, exc)
            if self._tracking_thread is None:
                report.tracking_processed = self.publisher.process(owner, limit=self.settings.tracking_batch)
            for lease in list(leases.values()):
                try:
                    self.store.finalize_run(lease)
                except Exception as exc:
                    self._run_failed(lease, exc)
        return report.freeze()

    def _run_failed(self, lease, exc) -> None:
        if STALE_LEASE in str(exc):
            self._run_leases.pop(lease.run_id, None)
        self._error(exc)

    def _error(self, exc) -> None:
        text = _describe(exc)
        self.errors.append(text)
        LOG.warning("graph controller: %s", text)

    # -- phase 2: harvest -----------------------------------------------------------------------------

    def _harvest(self, report) -> None:
        with self._lock:
            done = [item for item in self._inflight.values() if item.future.done()]
            for entry in done:
                del self._inflight[entry.job_id]   # the dispatch thread returned: the resource is free again (D8)
        for entry in done:
            try:
                outcome = entry.future.result()
            except ValueError as exc:
                if str(exc) in REFUSED_LAUNCH or isinstance(exc, LedgerIntegrityError):
                    self._return_refused(entry.run_id, entry.lease, str(exc)[:500])   # usually done in the thread
                    report.add("released", entry.job_id)
                    if isinstance(exc, LedgerIntegrityError):
                        self._note_run(entry.run_id, entry.node_id, str(exc))
                    continue
                self._note_run(entry.run_id, entry.node_id, DISPATCH_FAILED.format(error=_describe(exc)))
            except Exception as exc:
                self._note_run(entry.run_id, entry.node_id, DISPATCH_FAILED.format(error=_describe(exc)))
            else:
                self._outcomes[entry.job_id] = outcome
                self._job_runs[entry.job_id] = entry.run_id

    def _return_refused(self, run_id, attempt_lease, reason) -> bool:
        """``begin_launch`` refused (pause or cancel, D12): the attempt never launched, so return it now."""
        lease = self._run_leases.get(run_id)
        if lease is None:
            return False
        try:
            self.store.return_unlaunched(lease, attempt_lease, reason=reason)
        except ValueError:
            return False   # already returned, or the lease moved on: the settle phase decides from the DB
        except Exception as exc:   # never hides the refusal itself; harvest or the settle phase returns it later
            LOG.warning("returning a refused launch failed; it is retried: %s", type(exc).__name__)
            return False
        return True

    # -- phase 3: settle ------------------------------------------------------------------------------

    def _job_rows(self, run_id, job_id=None):
        query = ("SELECT j.id,j.status,j.updated,j.graph_node_id,j.cancel_requested,j.active_attempt_id,j.manifest,"
                 "a.launch_started,a.launch_id,a.legacy,r.status AS run_status,"
                 "(EXISTS (SELECT 1 FROM gpu_locks g WHERE g.job_id=j.id) "
                 " OR EXISTS (SELECT 1 FROM resource_leases l WHERE l.job_id=j.id)) AS locked,"
                 "d.disposition,d.counted,"
                 "EXISTS (SELECT 1 FROM node_outputs o WHERE o.attempt_id=j.active_attempt_id) AS recorded,"
                 "n.status AS node_status,n.retry_not_before "
                 "FROM jobs j JOIN nodes n ON n.job_id=j.id JOIN graph_runs r ON r.id=j.graph_run_id "
                 "LEFT JOIN job_attempts a ON a.attempt_id=j.active_attempt_id "
                 "LEFT JOIN attempt_dispositions d ON d.attempt_id=j.active_attempt_id WHERE j.graph_run_id=?")
        args = (run_id,)
        if job_id is not None:
            query, args = query + " AND j.id=?", (run_id, job_id)
        with self.store.jobs.connection() as db:
            rows = [dict(row) for row in db.execute(query + " ORDER BY j.created,j.id", args)]
        for row in rows:
            row["operation"] = json.loads(row.pop("manifest"))["operation"]
        return rows

    def _settle(self, lease, report) -> None:
        run_id = lease.run_id
        self.store.settle_canceled_jobs(lease)
        self._reasons.update({(run_id, node["node_id"]): node["wait_reason"]
                              for node in self.store.get_run(run_id)["nodes"]})
        rows = self._job_rows(run_id)
        self._recover_stale(lease, rows)
        for row in rows:
            if row["id"] in self._inflight:
                continue   # in flight in this process: never touched
            try:
                for _ in range(6):   # one job may pass several rows of the settle table in one tick
                    current = self._job_rows(run_id, row["id"])
                    if not current or row["id"] in self._inflight or not self._step(lease, current[0], report):
                        break
            except Exception as exc:   # one job's problem never stops the others; it is shown on its node
                if STALE_LEASE in str(exc):
                    raise
                self._error(exc)
                self._note(lease, row["graph_node_id"], SETTLE_FAILED.format(error=_describe(exc)))

    def _recover_stale(self, lease, rows) -> None:
        """Stale RUNNING/EVALUATING attempts become FAILED with their lock kept (the run-scoped recover). The
        threshold never reaches an attempt in flight in this process."""
        threshold = self.clock() - self.settings.stale_attempt_seconds
        mine = [row["updated"] for row in rows if row["id"] in self._inflight and row["status"] in ACTIVE]
        if mine:
            threshold = min(threshold, min(mine))
        if any(row["id"] not in self._inflight and row["status"] in ("RUNNING", "EVALUATING")
               and row["updated"] < threshold for row in rows):
            self.store.recover_stale(lease, stale_before=threshold)

    def _step(self, lease, row, report) -> bool:
        """One row of the settle table for one job. True when it changed the job, so the next row may apply."""
        job_id, status = row["id"], row["status"]
        if row["active_attempt_id"] is None or row["legacy"]:
            return False
        locked, started = bool(row["locked"]), bool(row["launch_started"])
        if status == "PROVISIONING":
            if row["updated"] >= self.clock() - self.settings.stale_attempt_seconds:
                return False
            if not started:
                return self._resume_unstarted(lease, row, report)
            # D9 in one transaction after the token rotation (8.5 review Minor 3): released when provably never
            # contacted, else FAILED with the lock kept (uncertain); an actor that passed RUNNING meanwhile is left.
            settled = self.store.settle_stale_launch(lease, job_id)
            if settled == "released":
                report.add("released", job_id)
            return settled != "unchanged"
        if status in ACTIVE:
            return False   # RUNNING/EVALUATING: the run-scoped recover above, once stale
        if locked:
            if status == "COMPLETED":
                return self._reconcile(lease, row, report, contact=False)   # rebuild and release; no executor
            failure = self.dispatcher.failure_kind(job_id)
            if failure in ("refused_before_launch", "canceled"):
                # Classified before any reconcile (8.2 review M4): a never-launched attempt stays uncounted, and a
                # proven cancel is released by the store on its accepted result (7.5 Minor B).
                self.store.classify_attempt(lease, job_id, failure)
                report.add("settled", job_id)
                return True
            return self._reconcile(lease, row, report)
        if status == "COMPLETED":
            return False if row["recorded"] else self._record(lease, row, report)
        if row["disposition"] is None:
            failure = self.dispatcher.failure_kind(job_id)
            if failure in (None, "uncertain"):
                return False
            self.store.classify_attempt(lease, job_id, failure)
            report.add("settled", job_id)
            return True
        return self._retry(lease, row, report)

    def _resume_unstarted(self, lease, row, report) -> bool:
        """A stale claim whose launch never began: take it over (fencing whoever claimed it), then complete a local
        selection, dispatch the same attempt (it launches once) or return it unlaunched."""
        job_id = row["id"]
        taken = self.store.takeover_attempt(lease, job_id)
        if taken["manifest"]["operation"]["kind"] == "select":
            self.store.complete_local_selection(lease, taken)   # completes, returns (pause) or cancels it
            report.add("settled", job_id)
            return True
        run = self.store.get_run(lease.run_id)
        if run["status"] != "RUNNING" or self._stopping:
            self.store.return_unlaunched(lease, taken["lease"],
                                         reason=STOPPING if self._stopping else RETURNED_AFTER_TAKEOVER)
            report.add("released", job_id)
            return True
        operation = taken["manifest"]["operation"]
        resources = OperationResources.model_validate(operation["resources"])
        if self._busy(taken["lease"].resource_id, resources.facts):
            return False   # a dispatch thread on this resource has not returned yet
        stack = contextlib.ExitStack()
        try:
            destination = stack.enter_context(self._resolve(run, resources, operation["kind"]))
        except Exception as exc:
            stack.close()
            self.store.return_unlaunched(lease, taken["lease"], reason="destination refused after takeover")
            self._note(lease, row["graph_node_id"], self._provider_reason(exc))
            report.add("released", job_id)
            return True
        if destination.profile != resources.executor or not observation_fits(destination.observation, resources.facts):
            stack.close()
            self.store.return_unlaunched(lease, taken["lease"], reason=REPLAN)
            self._note(lease, row["graph_node_id"], REPLAN)
            report.add("released", job_id)
            return True
        self._note(lease, row["graph_node_id"], None)
        self._submit(lease.run_id, taken, run["approval"], destination, stack)
        report.add("claimed", job_id)
        return False

    def _reconcile(self, lease, row, report, *, contact=True) -> bool:
        """Only for an attempt holding its lock, and only after ``takeover_attempt`` fenced the old actor
        (coordinator items 2 and 7). With ``contact`` it runs every ``reconcile_interval_seconds`` through the
        provider's recovery executor; None keeps the lock and waits (for a late result, or the operator)."""
        job_id, now = row["id"], self.clock()
        if contact:
            last = self._reconciled_at.get(job_id)
            if last is not None and now - last < self.settings.reconcile_interval_seconds:
                return False
            self._reconciled_at[job_id] = now
        self.store.takeover_attempt(lease, job_id)
        try:
            if contact and row["operation"]["kind"] != "select":
                with self._recover(lease.run_id, row) as executor:
                    outcome = self.dispatcher.reconcile(job_id, executor)
            else:
                outcome = self.dispatcher.reconcile(job_id, None)
        except Exception as exc:
            if isinstance(exc, ValueError) and FINAL_ATTEMPT in str(exc):
                return True   # defensive (F16): released meanwhile, so settled as final; the next rows apply
            self._note(lease, row["graph_node_id"], RECONCILE_UNAVAILABLE.format(error=_describe(exc)))
            return False
        report.add("reconciled", job_id)
        if outcome is None:
            self._note(lease, row["graph_node_id"], _uncertain_reason(self.store, row, now))
            return False
        self._reconciled_at.pop(job_id, None)
        self._outcomes[job_id] = outcome
        self._job_runs[job_id] = lease.run_id
        return True

    @contextlib.contextmanager
    def _recover(self, run_id, row):
        run = self.store.get_run(run_id)
        operation = row["operation"]
        resources = OperationResources.model_validate(operation["resources"])
        profile = resources.executor
        manifest = self.store.jobs.get(row["id"])["manifest"]
        with self._provider(run).recover(profile.profile_id, profile.revision, pinned=profile,
                                         pinned_facts=resources.facts, manifest_sha256=manifest["sha256"],
                                         runtime_profile=resources.runtime.profile) as executor:
            yield executor

    def _record(self, lease, row, report) -> bool:
        """COMPLETED without recorded outputs: the cached outcome, else ``outputs_for`` (never reconcile: the attempt
        is final), recorded under a takeover lease with the outcome's verified inspected targets."""
        job_id = row["id"]
        outcome = self._outcomes.get(job_id)
        try:
            if (outcome is None or getattr(outcome, "attempt_id", None) != row["active_attempt_id"]
                    or outcome.status != "COMPLETED" or outcome.outputs is None):
                outcome = self.dispatcher.outputs_for(job_id)
            taken = self.store.takeover_attempt(lease, job_id)
            outputs = outcome.outputs
            targets = tuple(outcome.inspected_targets or ()) or tuple(outputs.inspected_targets)
            self.store.record_outputs(taken["lease"], replace(outputs, inspected_targets=targets))
        except ValueError as exc:
            if STALE_LEASE in str(exc):
                raise
            self._note(lease, row["graph_node_id"], OUTPUTS_REFUSED.format(error=_describe(exc)))
            return False
        self._outcomes.pop(job_id, None)
        report.add("settled", job_id)
        return True

    def _retry(self, lease, row, report) -> bool:
        if row["status"] != "FAILED" or row["node_status"] != "RUNNING" or row["retry_not_before"] is None:
            return False
        if row["retry_not_before"] <= self.clock() and row["run_status"] == "RUNNING":
            if self.store.requeue_retry(lease, row["id"]):
                report.add("retried", row["id"])
                return True
            return False
        plan = self.store.get_run(lease.run_id)["plan"]
        with self.store.jobs.connection() as db:
            attempts = db.execute("SELECT COUNT(*) FROM job_attempts a WHERE a.job_id=? AND NOT EXISTS (SELECT 1 FROM "
                                  "attempt_dispositions d WHERE d.attempt_id=a.attempt_id AND d.counted=0)",
                                  (row["id"],)).fetchone()[0]
        self._note(lease, row["graph_node_id"], RETRY_WAIT.format(
            next=attempts + 1, max=plan.graph.retry_policy.max_attempts))
        return False

    # -- phases 4-6: prepare, local selections, GPU claims --------------------------------------------

    def _prepare(self, lease, report) -> None:
        run_id = lease.run_id
        ready = [item.node_id for item in self.store.readiness(run_id) if item.state == "ready"]
        if ready:
            try:
                report.add("prepared", *self.store.prepare_ready(lease, node_ids=ready))
            except LedgerIntegrityError as exc:   # run-wide: fail nothing, show it, wait (8.2 review M-2)
                for node_id in ready:
                    self._note(lease, node_id, str(exc))
        tracking = None
        for item in self.store.readiness(run_id):
            if item.state == "waiting":
                reason = item.reason
                if reason and reason.startswith("waiting for tracking of "):
                    tracking = tracking or self.outbox.status(run_id)
                    parent = tracking["nodes"].get(reason.removeprefix("waiting for tracking of "), {})
                    if parent.get("tracking_status") in ("failed", "uncertain"):
                        reason += f" (tracking {parent['tracking_status']}: " + (
                            parent.get("action") or "re-arm the tracking event once MLflow is reachable") + ")"
                self._note(lease, item.node_id, reason)
            elif item.state == "blocked":
                for parent in item.failed_parents:
                    report.add("blocked", *self.store.block_descendants(lease, parent))

    def _local_selections(self, lease, report) -> None:
        for _ in range(64):
            claimed = self.store.claim_ready(None, lease)
            if claimed is None:
                return
            report.add("claimed", claimed["id"])
            try:
                self.store.complete_local_selection(lease, claimed)   # cancel wins; pause returns it unlaunched
            except ValueError as exc:
                if STALE_LEASE in str(exc):
                    raise
                self._note(lease, claimed["graph_node_id"], _describe(exc))
                return

    def _queued(self, run_id):
        with self.store.jobs.connection() as db:
            rows = db.execute("SELECT j.id,j.graph_node_id,j.manifest FROM jobs j JOIN nodes n ON n.job_id=j.id "
                              "WHERE n.run_id=? AND j.status='QUEUED' ORDER BY j.priority DESC,j.created",
                              (run_id,)).fetchall()
            active = db.execute("SELECT COUNT(*) FROM jobs WHERE graph_run_id=? AND status IN "
                                "('PROVISIONING','RUNNING','EVALUATING')", (run_id,)).fetchone()[0]
        queued = [(row["id"], row["graph_node_id"], json.loads(row["manifest"])["operation"]) for row in rows]
        return [item for item in queued if item[2]["kind"] != "select"], active

    def _claims(self, lease, run, report) -> None:
        """Phase 6: one claim per free destination. Skipped destinations (busy in the DB or in this process, the
        run at max_concurrent, the budget, a provider refusal) consume no attempt; the node's wait reason says why."""
        plan, run_id = run["plan"], lease.run_id
        queued, active = self._queued(run_id)
        now = self.clock()
        for job_id, node_id, operation in queued:
            resources = OperationResources.model_validate(operation["resources"])
            profile, facts = resources.executor, resources.facts
            resource = ("simulation:" if plan.simulation else "") + facts.exclusivity_key
            backoff = self._refused.get((run_id, profile.profile_id, profile.revision))
            reason = (AT_CONCURRENCY if active >= plan.graph.max_concurrent
                      else THREADS_BUSY if len(self._inflight) >= self.settings.max_dispatch_threads
                      else self._busy(resource, facts) or self._db_blocked(resource, facts, plan.simulation)
                      or self._budget_reason(run_id, plan, resources)
                      or (backoff[1] if backoff and backoff[0] > now else None)
                      or self._integrity_reason(run_id, node_id, operation, plan.simulation, now))
            if reason:
                self._note(lease, node_id, reason)
                continue
            stack = contextlib.ExitStack()
            try:
                destination = stack.enter_context(self._resolve(run, resources, operation["kind"]))
                if destination.profile != profile:   # never another profile's executor (8.5 review Important 1)
                    raise ValueError(DESTINATION_PROFILE_DIFFERS)
            except Exception as exc:   # a refusal (changed profile, transport, Pod) consumes no attempt
                stack.close()
                reason = self._provider_reason(exc)   # backed off, never reopened every tick (8.5 review Minor 8)
                self._refused[(run_id, profile.profile_id, profile.revision)] = (
                    now + self.settings.destination_retry_seconds, reason)
                self._note(lease, node_id, reason)
                continue
            self._refused.pop((run_id, profile.profile_id, profile.revision), None)
            try:   # this job only, on its own pinned profile: nothing is written for any other (Important 1)
                claimed = self.store.claim_ready(destination.observation, lease, job_id=job_id, executor=profile)
            except BaseException:
                stack.close()
                raise
            if claimed is None:
                stack.close()
                reason = self._unclaimed_reason(run_id, plan, resources, destination.observation, resource)
                if reason == REPLAN:   # a live price or GPU that differs: not reopened every tick (re-review Minor 2)
                    self._refused[(run_id, profile.profile_id, profile.revision)] = (
                        now + self.settings.destination_retry_seconds, reason)
                self._note(lease, node_id, reason)
                continue
            self._note(lease, claimed["graph_node_id"], None)
            self._submit(run_id, claimed, run["approval"], destination, stack)
            report.add("claimed", claimed["id"])
            active += 1

    def _submit(self, run_id, claimed, approval, destination, stack) -> None:
        """Hand one claimed attempt to its dispatch thread, which runs it once and closes the destination context. A
        refused launch (pause or cancel, D12) or a ledger integrity failure before the launch is returned unlaunched
        at once, from the thread, uncounted."""
        job_id, attempt_lease = claimed["id"], claimed["lease"]
        dispatcher, executor = self.dispatcher, destination.executor

        def work():
            try:
                return dispatcher.run_claimed(claimed, executor, approval=approval)
            except ValueError as exc:
                if str(exc) in REFUSED_LAUNCH or isinstance(exc, LedgerIntegrityError):
                    self._return_refused(run_id, attempt_lease, str(exc)[:500])
                raise
            finally:
                stack.close()

        future = self.pool.submit(job_id, work)
        with self._lock:
            self._inflight[job_id] = _InFlight(job_id, run_id, claimed["graph_node_id"], attempt_lease.resource_id,
                                               destination.observation, future, attempt_lease)
            self._job_runs[job_id] = run_id
        future.add_done_callback(lambda _: self._wake.set())

    def _integrity_reason(self, run_id, node_id, operation, simulation, now) -> str | None:
        """Check a node's recorded lineage before claiming it, so a record that no longer verifies holds the node
        without an attempt (8.5 review Minor 1). Only where its dispatcher re-resolves bindings before the launch:
        every non-evaluation in a simulation, and only an adapter continuation for the real runner (re-review Minor
        2). A failed check is held for ``destination_retry_seconds``, never re-hashed every tick."""
        needed = (operation["kind"] != "evaluate" if simulation else operation["kind"] == "train"
                  and operation["model_input"]["initialization"] == "continue_adapter")
        if not needed:
            return None
        held = self._refused.get((run_id, "integrity", node_id))
        if held and held[0] > now:
            return held[1]
        try:
            self.store.bindings_for(run_id, node_id)
        except LedgerIntegrityError as exc:
            self._refused[(run_id, "integrity", node_id)] = (now + self.settings.destination_retry_seconds, str(exc))
            return str(exc)
        self._refused.pop((run_id, "integrity", node_id), None)
        return None

    def _busy(self, resource, facts) -> str | None:
        """Why a dispatch thread that has not returned keeps this resource busy (D8), or None."""
        with self._lock:
            entries = list(self._inflight.values())
        for entry in entries:
            if _conflicts(entry.resource, entry.facts, resource, facts):
                foreign = _provider_prefix(entry.resource) != _provider_prefix(resource)
                return CROSS_PROVIDER if foreign else DESTINATION_BUSY.format(key=resource)
        return None

    def _db_blocked(self, resource, facts, simulation) -> str | None:
        jobs = self.store.jobs
        if not (jobs.gpu_locked(resource) if simulation else jobs.resource_blocked(facts)):
            return None
        if not simulation and jobs.foreign_provider_lock(resource):
            return CROSS_PROVIDER
        return DESTINATION_BUSY.format(key=resource)

    def _budget_reason(self, run_id, plan, resources) -> str | None:
        """M2: the 8.1 backfill reserves historical attempts too, so an old run can be exhausted before any claim."""
        reserved = self.store.reserved_budget(run_id)
        approved = plan.max_estimated_cost_usd
        if reserved + resources.facts.cost(resources.timeout_seconds) <= approved * (1 + 1e-9):
            return None
        text = BUDGET_EXHAUSTED.format(reserved=reserved, approved=approved)
        if any(isinstance(reason, str) and reason.startswith(UNPRICED_ATTEMPT)
               for (owner_run, _), reason in self._reasons.items() if owner_run == run_id):
            text += "; " + UNPRICED_ATTEMPT
        return text

    def _unclaimed_reason(self, run_id, plan, resources, observation, resource) -> str:
        if not observation_fits(observation, resources.facts):
            return REPLAN
        return (self._budget_reason(run_id, plan, resources) or self._db_blocked(resource, resources.facts,
                                                                                 plan.simulation)
                or DESTINATION_BUSY.format(key=resource))

    @staticmethod
    def _provider_reason(exc) -> str:
        """A provider's own refusal is a fixed text (PROFILE_CHANGED, TRANSPORT_CHANGED, ...); anything else is
        described without credentials or remote paths."""
        if isinstance(exc, ValueError):
            return _display(str(exc))
        return DESTINATION_UNAVAILABLE.format(error=_describe(exc))

    def _provider(self, run):
        plan = run["plan"]
        if plan.simulation:   # one provider per simulation run, from its own pinned observations (8.4)
            if self.simulated_provider is not None:
                return self.simulated_provider
            if run["id"] not in self._simulated:
                self._simulated[run["id"]] = SimulatedExecutorProvider.for_plan(plan, clock=self.clock)
            return self._simulated[run["id"]]
        if self.provider is None:
            raise ValueError("no executor provider is configured for real graph runs")
        return self.provider

    def _resolve(self, run, resources, kind):
        profile = resources.executor
        return self._provider(run).resolve(profile.profile_id, profile.revision, pinned=profile,
                                           runtime_profile=resources.runtime.profile,
                                           model_ops=kind in {"merge", "materialize"})

    # -- wait reasons ---------------------------------------------------------------------------------

    def _note(self, lease, node_id, reason) -> None:
        """Write a node's wait reason when it changed. The backfill's UNPRICED_ATTEMPT notice is never dropped."""
        key = (lease.run_id, node_id)
        current = self._reasons.get(key, _UNKNOWN)
        if current is _UNKNOWN:
            current = next((node["wait_reason"] for node in self.store.get_run(lease.run_id)["nodes"]
                            if node["node_id"] == node_id), None)
        if isinstance(current, str) and current.startswith(UNPRICED_ATTEMPT):
            if reason is None or reason.startswith(UNPRICED_ATTEMPT):
                return
            reason = f"{UNPRICED_ATTEMPT}; {reason}"
        if reason is not None:
            reason = _display(reason)
        if reason == current:
            return
        self.store.set_wait_reason(lease, node_id, reason)
        self._reasons[key] = reason

    def _note_run(self, run_id, node_id, reason) -> None:
        lease = self._run_leases.get(run_id)
        if lease is not None:
            with contextlib.suppress(ValueError):
                self._note(lease, node_id, reason)

    # -- the long-running loop ------------------------------------------------------------------------

    def run_forever(self, stop_event: threading.Event) -> None:
        """Tick until ``stop_event`` is set. Then stop claiming (phases 5-6), keep settling and tracking until no
        dispatch thread is left, and release the owner lease (D14). Tracking runs on its own thread meanwhile; its
        in-flight publish finishes before the release. A fenced owner stops at once and releases nothing."""
        owner = self._owner
        if owner is None:
            raise ValueError("acquire the controller owner lease first")
        self._tracking_stop.clear()
        self._tracking_thread = threading.Thread(target=self._tracking_loop, args=(owner,),
                                                 name="graph-controller-tracking", daemon=True)
        self._tracking_thread.start()
        fenced = False
        try:
            while True:
                self._stopping = stop_event.is_set()
                try:
                    self.tick(owner)
                except ValueError as exc:
                    if str(exc) == STALE_OWNER:
                        fenced = True
                        break
                    self._error(exc)
                except Exception as exc:
                    self._error(exc)
                with self._lock:
                    idle = not self._inflight
                if self._stopping and idle:
                    break
                if self._stopping:
                    self._wake.wait(self.settings.tick_seconds)   # woken when a dispatch thread returns
                    self._wake.clear()
                else:
                    stop_event.wait(self.settings.tick_seconds)
        finally:   # also when a BaseException escapes (8.5 review Minor 5): clean up, then let it propagate
            self._tracking_stop.set()
            self._tracking_thread.join()
            self._tracking_thread = None
            if self._own_pool:
                self.pool.shutdown(wait=False)
            if not fenced:
                with contextlib.suppress(Exception):
                    self.store.release_owner(owner)

    def _tracking_loop(self, owner) -> None:
        while not self._tracking_stop.is_set():
            try:
                with self._heartbeat(owner, {}):
                    self.publisher.process(owner, limit=self.settings.tracking_batch)
            except ValueError as exc:
                if str(exc) == STALE_OWNER:
                    return
                self._error(exc)
            except Exception as exc:
                self._error(exc)
            self._tracking_stop.wait(self.settings.tick_seconds)

    def status(self) -> dict:
        """``graph_status`` plus in-process facts: in-flight job IDs, busy resources and recent errors."""
        result = graph_status(self.store, now=self.clock())
        provider_status = getattr(self.provider, "status", None)
        with self._lock:
            entries = list(self._inflight.values())
        result["controller"].update(
            in_flight=sorted(entry.job_id for entry in entries), busy=sorted({entry.resource for entry in entries}),
            stopping=self._stopping, tracking_thread=self._tracking_thread is not None,
            skipped_runs=dict(sorted(self._skipped.items())), errors=list(self.errors),
            tracking_backfill_skipped=list(self.outbox.backfill_skipped),
            provider=provider_status() if callable(provider_status) else None)
        return result


# -- status for polling (Task 9 graph_run_get/controller_status; Task 10) --------------------------------

def graph_status(store: GraphStore, run_id: str | None = None, *, now: float) -> dict:
    """The DB-only, secret-free status of the controller and the graph runs (one run with ``run_id``).

    It needs no controller object, so any process can poll it. No credential, remote path or setting appears: errors
    and wait reasons pass through ``redact`` and lose remote POSIX paths. ``controller.state`` is ``running`` while
    the owner's heartbeat is fresh, ``stale`` once it expired, and ``absent`` with no owner or after a clean release.
    """
    if not isinstance(store, GraphStore):
        raise TypeError("graph_status requires the GraphStore ledger")
    owner = store.owner_state()
    if owner is None or owner["expires"] <= 0:
        controller = {"owner_id": owner["owner_id"] if owner else None, "state": "absent",
                      "heartbeat_age_seconds": None}
    else:
        controller = {"owner_id": owner["owner_id"], "state": "running" if owner["expires"] > now else "stale",
                      "heartbeat_age_seconds": round(max(0.0, now - owner["heartbeat"]), 3)}
    with store.jobs.connection() as db:
        run_ids = ([row[0] for row in db.execute("SELECT id FROM graph_runs ORDER BY created,id")]
                   if run_id is None else [run_id])
    outbox = TrackingOutbox(store)
    runs = []
    for item in run_ids:
        try:
            runs.append(_run_status(store, outbox, item, now))
        except Exception as exc:   # one unreadable run never hides the others (8.5 review Minor 4)
            if run_id is not None and isinstance(exc, ValueError) and str(exc) == "unknown graph run":
                raise
            runs.append({"run_id": item, "error": _display(f"unreadable graph run: {_describe(exc)}")})
    return {"controller": controller, "runs": runs}


def result_deadline(store, row) -> float | None:
    """When an uncertain attempt's late result stops being plausible (8.5 review Minor 6; anchored by the re-review,
    Minor 1): the time the attempt became uncertain, plus the operation's time limit, plus ``RESULT_MARGIN_SECONDS``.

    The anchor is the attempt's latest job event, the FAILED that made it uncertain. That is never before the remote
    launch: the actor uploads its inputs and launches while RUNNING, and the attempt fails only after its actor
    stopped (the runner gave up on a lost response, or a stale recover after its last heartbeat). A claim resumed
    long after it was made, or hours of input uploads, therefore never shorten the remote time limit, so no transfer
    allowance is needed. None without a pinned time limit (a selection)."""
    timeout = ((row.get("operation") or {}).get("resources") or {}).get("timeout_seconds")
    attempt_id = row.get("active_attempt_id")
    if not timeout or not attempt_id:
        return None
    with store.jobs.connection() as db:
        anchor = db.execute("SELECT MAX(e.timestamp) FROM events e JOIN job_attempts a ON a.attempt_id=? "
                            "WHERE e.job_id=a.job_id AND e.timestamp>=a.created", (attempt_id,)).fetchone()
    return None if anchor is None or anchor[0] is None else float(anchor[0]) + float(timeout) + RESULT_MARGIN_SECONDS


def _has_remote_record(store, row) -> bool:
    launch = Path(store.state_dir) / "jobs" / row["id"] / "launches" / (row.get("launch_id") or "-")
    return (launch / REMOTE_ATTEMPT).is_file()


def _uncertain_reason(store, row, now) -> str:
    """No result yet: wait while a late result is plausible; ask the operator once it is not, or when nothing could
    ever be collected (no remote record)."""
    if not _has_remote_record(store, row):
        return OPERATOR_CONFIRMATION
    deadline = result_deadline(store, row)
    return RESULT_OVERDUE if deadline is not None and now >= deadline else AWAITING_RESULT


def _run_status(store, outbox, run_id, now) -> dict:
    run = store.get_run(run_id)
    plan = run["plan"]
    entries = {entry.node.id: entry for entry in plan.nodes}
    tracking = outbox.status(run_id)
    with store.jobs.connection() as db:
        jobs = {row["id"]: dict(row) for row in db.execute(
            "SELECT j.id,j.status,j.error,j.attempts,j.active_attempt_id,j.manifest,a.resource_id,a.launch_id,"
            "a.launch_started,EXISTS (SELECT 1 FROM gpu_locks g WHERE g.job_id=j.id) AS locked,o.outputs "
            "FROM jobs j LEFT JOIN job_attempts a ON a.attempt_id=j.active_attempt_id "
            "LEFT JOIN node_outputs o ON o.attempt_id=j.active_attempt_id WHERE j.graph_run_id=?", (run_id,))}
        counted = dict(db.execute(
            "SELECT a.job_id,COUNT(*) FROM job_attempts a JOIN jobs j ON j.id=a.job_id WHERE j.graph_run_id=? AND "
            "NOT EXISTS (SELECT 1 FROM attempt_dispositions d WHERE d.attempt_id=a.attempt_id AND d.counted=0) "
            "GROUP BY a.job_id", (run_id,)).fetchall())
    reserved = store.reserved_budget(run_id)
    approved = plan.max_estimated_cost_usd
    nodes, exhausted = [], False
    for node in run["nodes"]:
        job = jobs.get(node["job_id"])
        operation = json.loads(job["manifest"])["operation"] if job else None
        if (job and job["status"] == "QUEUED" and operation.get("kind") != "select"
                and reserved + OperationResources.model_validate(operation["resources"]).facts.cost(
                    operation["resources"]["timeout_seconds"]) > approved * (1 + 1e-9)):
            exhausted = True
        nodes.append(_node_status(store, plan, entries[node["node_id"]], node, job, operation, counted,
                                  tracking["nodes"].get(node["node_id"], {}), now))
    return {"run_id": run_id, "status": run["status"], "simulation": plan.simulation, "plan_sha256": plan.sha256,
            "approved_cost_usd": approved, "reserved_cost_usd": reserved, "budget_exhausted": exhausted,
            "unpriced_attempts": sum(bool(node["wait_reason"] and node["wait_reason"].startswith(UNPRICED_ATTEMPT))
                                     for node in run["nodes"]),
            "created": run["created"],
            "tracking": {"events": tracking["events"], "graph": tracking["graph"]["tracking_status"],
                         "action": tracking["graph"].get("action")},
            "nodes": nodes}


def _node_status(store, plan, entry, node, job, operation, counted, tracking, now) -> dict:
    status = node["status"]
    uncertain = bool(job and job["locked"] and job["launch_started"] and job["status"] in ("FAILED", "CANCELED"))
    deadline = awaiting = None
    if uncertain:
        row = {"id": job["id"], "launch_id": job["launch_id"], "active_attempt_id": job["active_attempt_id"],
               "operation": operation}
        deadline = result_deadline(store, row)
        awaiting = _has_remote_record(store, row) and (deadline is None or now < deadline)
    wait_reason = (_display(node["wait_reason"]) if node["wait_reason"] and status in UNFINISHED_NODES else None)
    error = node["error"] or (job["error"] if job and job["status"] in ("FAILED", "CANCELED") else None)
    resources = (operation or {}).get("resources") or {}
    output, selection = _outputs_summary(json.loads(job["outputs"]) if job and job["outputs"] else None)
    return {
        "node_id": node["node_id"], "label": entry.node.label, "kind": entry.node.kind, "status": status,
        # Task 9.8: with attempt_id, the IDs an operator release names (graph_release_lock, CLI release-lock).
        "job_id": node["job_id"],
        "job_status": job["status"] if job else None, "attempt_id": job["active_attempt_id"] if job else None,
        "attempts": job["attempts"] if job else 0, "counted_attempts": counted.get(job["id"], 0) if job else 0,
        "max_attempts": plan.graph.retry_policy.max_attempts,
        "resource_id": job["resource_id"] if job else None,
        "destination_profile_id": (resources.get("executor") or {}).get("profile_id"),
        "uncertain": uncertain, "awaiting_result": bool(awaiting), "result_deadline": deadline,
        # An uncertain attempt needs the operator only once no late result is plausible (8.5 review Minor 6).
        "needs_operator": bool(uncertain and not awaiting) or bool(
            wait_reason and wait_reason.startswith(OPERATOR_PREFIXES)),
        "wait_reason": wait_reason, "error": _display(error) if error else None,
        "retry_at": node["retry_not_before"],
        "tracking_status": tracking.get("tracking_status", "not_started"),
        "registration_status": tracking.get("registration_status", "none"),
        "tracking_action": tracking.get("action"),
        "output": output, "selection": selection, "cache_seed": _cache_seed(store, plan, job, operation)}


def _outputs_summary(outputs):
    if not outputs:
        return None, None
    model, evaluation = outputs.get("model"), outputs.get("evaluation")
    receipt = outputs.get("selection") or (model or {}).get("selection")
    selection = None
    if receipt:
        winner = receipt.get("winner")
        selection = winner["artifact_id"] if receipt.get("status") == "selected" and winner else "no_winner"
    if model:
        ref = model["model"]
        return {"artifact_id": ref["artifact_id"], "inventory_sha256": ref["inventory_sha256"]}, selection
    if evaluation:
        record = evaluation["evaluation"]
        return {"evaluation_status": record.get("status"), "metrics": record.get("metrics")}, selection
    return None, selection


def _cache_seed(store, plan, job, operation) -> str | None:
    """``published``/``failed`` from the runner's ``<launch_id>.cache-seed.json``, ``missing`` without it (a crash
    while seeding; D8 never retries it: the next launch on that host uploads the entry). Real model outputs only."""
    if (plan.simulation or not job or job["status"] != "COMPLETED" or not job["launch_id"]
            or (operation or {}).get("kind") not in {"train", "merge", "materialize"}):
        return None
    path = Path(store.state_dir) / "jobs" / job["id"] / "launches" / f"{job['launch_id']}.cache-seed.json"
    try:
        status = json.loads(path.read_bytes()).get("status")
    except (OSError, ValueError):
        return "missing"
    return status if status in {"published", "failed"} else "missing"
