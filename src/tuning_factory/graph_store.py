"""Atomic graph/job ledger. Binding resolver is a trusted controller hook, not API input.

No runtime, provider or tracking side effects occur in DB transactions. The trusted ``LedgerBindingResolver``
reconstructs VerifiedBindings from verified local receipts; without it the store keeps root-only bindings.

Fencing (Task 8): one controller owner lease per state directory, run leases bound to that owner, and the
attempt leases of ``JobStore``. A claim reserves its attempt's maximum cost in the same transaction. GraphStore's
own rows use the injected wall clock; job rows and events keep JobStore's ``time.time()``.

Readiness and outcomes (Task 8.2): a node compiles only when every parent completed and its attempt evidence was
acknowledged (or, for a partial selection, every evaluation parent finished). Failures become FAILED and block the
subtree; each finished attempt gets one immutable disposition, and only a provably finished remote failure (or an
attempt that provably never ran) is retried, within ``max_attempts`` and after its backoff.

Tracking (Task 8.3): ``submit``, ``claim_ready``, ``record_outputs``, ``complete_local_selection``,
``classify_attempt`` and ``finalize_run`` enqueue their tracking events (``tracking_outbox.py``) in their own
transaction. Only rows are written there; the publisher sends them later, outside any ledger transaction.
"""
import json
import math
import os
import re
import time
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .benchmarks import BenchmarkSpec, SelectionResult
from .graph_planner import (
    ApprovalGrant,
    GraphPlan,
    ModelTargetEvidence,
    NodeEvaluationBinding,
    NodeExecutionEvidence,
    NodeModelBinding,
    ProducerEvidence,
    VerifiedBindings,
    _check_plan,
    compile_node,
)
from .graph_schema import SelectNode, graph_edges, node_refs
from .jobs import AttemptLease, JobStore
from .manifest import resolve_operation
from .model_artifacts import ModelArtifactStore
from .operations import LocalSelectSpec, OperationResources, resolve_local_selection
from .registry import Registry, canonical, digest
from .target_inspection import verify_inspected_targets
from .tracking_outbox import (
    EVENTS_INDEX,
    EVENTS_TABLE,
    AttemptFacts,
    attempt_events,
    enqueue_event,
    graph_event,
    node_event,
    summary_event,
)

OWNER_HELD = 'controller already running'
STALE_OWNER = 'stale controller owner lease'
OWNER_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}')
# Additive Task 8 migration: new tables and nullable columns only, so pre-Task-8 rows stay valid unchanged.
MIGRATION_TABLES = (
    '''CREATE TABLE IF NOT EXISTS controller_owner (
      id INTEGER PRIMARY KEY CHECK (id = 1), owner_id TEXT NOT NULL, lease_token TEXT NOT NULL,
      acquired REAL NOT NULL, heartbeat REAL NOT NULL, expires REAL NOT NULL)''',
    '''CREATE TABLE IF NOT EXISTS budget_reservations (
      attempt_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, node_id TEXT NOT NULL, job_id TEXT NOT NULL,
      resource_id TEXT NOT NULL, max_cost_usd REAL NOT NULL,
      status TEXT NOT NULL CHECK (status IN ('RESERVED','VOID')), created REAL NOT NULL)''',
    '''CREATE TABLE IF NOT EXISTS attempt_dispositions (
      attempt_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, node_id TEXT NOT NULL, job_id TEXT NOT NULL,
      disposition TEXT NOT NULL, counted INTEGER NOT NULL, detail TEXT, created REAL NOT NULL)''',
    EVENTS_TABLE,   # Task 8.3: the tracking outbox's events; tracking_outbox stays the per-attempt gate
    EVENTS_INDEX,
)
MIGRATION_COLUMNS = (('controller_leases', 'owner_token', 'TEXT'), ('nodes', 'retry_not_before', 'REAL'),
                     ('nodes', 'wait_reason', 'TEXT'), ('nodes', 'updated', 'REAL'))
RESERVE = ('INSERT INTO budget_reservations (attempt_id,run_id,node_id,job_id,resource_id,max_cost_usd,status,created) '
           'VALUES (?,?,?,?,?,?,?,?)')
DISPOSE = ('INSERT INTO attempt_dispositions (attempt_id,run_id,node_id,job_id,disposition,counted,detail,created) '
           'VALUES (?,?,?,?,?,?,?,?)')
UNPRICED_ATTEMPT = "a pre-Task-8 attempt could not be priced; it reserves the run's whole approved budget"
LEDGER_INTEGRITY = ('ledger integrity check failed; no node was failed, and the run waits until an operator restores '
                    'or removes the record')
TEST_ACKNOWLEDGEMENT = ('acknowledge_tracking is a test hook; attempt evidence is acknowledged only by the tracking '
                        "outbox's receipt")

# -- Task 8.2: readiness, dispositions and safe release ---------------------------------------------
FailureKind = Literal['remote_failed', 'output_invalid', 'refused_before_launch', 'failed_before_submit',
                      'canceled', 'uncertain']
CLASSIFIED = ('remote_failed', 'output_invalid', 'refused_before_launch', 'failed_before_submit', 'canceled')
PRODUCERS = frozenset({'train', 'merge', 'materialize'})
NODE_FINISHED = frozenset({'COMPLETED', 'FAILED', 'BLOCKED', 'CANCELED'})
NODE_STOPPED = frozenset({'FAILED', 'BLOCKED', 'CANCELED'})
REMOTE_ATTEMPT = 'remote-attempt.json'   # the executor writes it before its first remote contact (plan F8)
RELEASED_BEFORE_CONTACT = 'attempt stopped before remote contact; released after takeover'
STALE_HEARTBEAT = 'worker heartbeat expired; reconcile execution before releasing GPU lock'   # JobStore.recover's
DISPOSITION_IMMUTABLE = 'attempt disposition is immutable'
NO_WINNER = 'selection produced no winner'
PARTIAL_REFUSED = 'partial selection is not allowed'
# 8.2 review M-4: an uncounted settlement already written for the attempt answers these failure kinds.
SETTLED_AS = {'released_before_contact': frozenset({'failed_before_submit'}),
              'returned_unlaunched': frozenset({'canceled', 'refused_before_launch'})}


class LedgerIntegrityError(ValueError):
    """Run-wide ledger evidence no longer verifies while a gated ``prepare_ready`` resolves bindings (8.2 review
    M-2): nothing is failed; the controller shows it as the ready nodes' wait reason."""


@dataclass(frozen=True)
class ControllerLease:
    run_id: str
    lease_token: str


@dataclass(frozen=True)
class OwnerLease:
    """The one controller allowed to own run leases in this state directory."""
    owner_id: str
    lease_token: str


@dataclass(frozen=True)
class NodeOutputs:
    """Trusted runtime receipts; producer/completion identities are ledger-derived."""
    model: NodeModelBinding | None = None
    evaluation: NodeEvaluationBinding | None = None
    inspected_targets: tuple[ModelTargetEvidence, ...] = ()
    selection: SelectionResult | None = None   # only a no-winner selection records a bare receipt


@dataclass(frozen=True)
class Readiness:
    """Whether a PENDING node may compile now. ``done``: the node already has a job or is finished."""
    node_id: str
    state: Literal['ready', 'waiting', 'blocked', 'done']
    reason: str | None
    failed_parents: tuple[str, ...] = ()


def _owner_ttl(ttl_seconds):
    if (isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, (int, float))
            or not math.isfinite(ttl_seconds) or ttl_seconds <= 0):
        raise ValueError('controller owner lease TTL must be positive and finite')


def _attempt_cost(operation) -> float:
    """The most one attempt may cost: its pinned facts' price over its timeout. A local selection costs 0."""
    if operation['kind'] == 'select':
        return 0.0
    from pydantic import TypeAdapter

    from .executor_profiles import ResourceObservation
    resources = operation['resources']
    pinned = TypeAdapter(ResourceObservation).validate_python(resources['facts'])
    return float(pinned.cost(resources['timeout_seconds']))


def _unpriced_cost(db, run_id) -> float:
    """The reservation of an attempt the backfill cannot price: its run's whole approved total (blocking further
    GPU claims of that run only), or infinity when even the stored plan is unreadable."""
    try:
        row = db.execute('SELECT plan FROM graph_runs WHERE id=?', (run_id,)).fetchone()
        total = float(json.loads(row['plan'])['max_estimated_cost_usd'])
    except (KeyError, TypeError, ValueError):
        return math.inf
    return total if math.isfinite(total) and total >= 0 else math.inf


def accepted_result(state_dir, job_id, launch_id, attempt_id=None):
    """The attempt runner's persisted, identity-checked result of one launch, or None.

    ``jobs/<job>/launches/<launch_id>.result.json`` is written only after the runner accepted the executor's result
    for that launch (``OperationAttemptRunner._accept``), so it proves the remote execution stopped. It is re-read
    here and must still name the attempt's own persisted launch ID.
    """
    from .worker_protocol import OperationResult
    if not launch_id:
        return None
    path = Path(state_dir) / 'jobs' / job_id / 'launches' / f'{launch_id}.result.json'
    try:
        if path.is_symlink() or not path.is_file():
            return None
        result = OperationResult.model_validate_json(path.read_bytes())
    except (OSError, ValueError):
        return None
    if result.launch_id != launch_id or result.attempt_id not in (None, attempt_id):
        return None
    return result


def _node_facts(db, run_id):
    """node_id -> (status, tracking acknowledged, no-winner selection) of the node's current attempt."""
    facts = {}
    for row in db.execute('SELECT n.node_id,n.status,t.acknowledged,o.outputs FROM nodes n '
                          'LEFT JOIN jobs j ON n.job_id=j.id '
                          'LEFT JOIN tracking_outbox t ON t.attempt_id=j.active_attempt_id '
                          'LEFT JOIN node_outputs o ON o.attempt_id=j.active_attempt_id WHERE n.run_id=?', (run_id,)):
        payload = json.loads(row['outputs']) if row['outputs'] else {}
        facts[row['node_id']] = (row['status'], bool(row['acknowledged']),
                                 'selection' in payload and payload.get('model') is None)
    return facts


def _readiness(plan, facts):
    """Pure readiness of every plan node from its parents' statuses (graph_edges: ports and explicit edges)."""
    edges = graph_edges(plan.graph)
    result = []
    for entry in plan.nodes:
        node = entry.node
        if facts[node.id][0] != 'PENDING':
            result.append(Readiness(node.id, 'done', None))
            continue
        parents = sorted({parent for parent, child in edges if child == node.id})
        evaluations = {ref.node_id for ref in node.evaluations} if isinstance(node, SelectNode) else set()
        partial = isinstance(node, SelectNode) and node.selection.allow_partial
        stopped = tuple(parent for parent in parents if facts[parent][0] in NODE_STOPPED)
        hard = tuple(parent for parent in stopped if not (partial and parent in evaluations))
        if hard:
            other = next((parent for parent in hard if parent not in evaluations), None)
            reason = PARTIAL_REFUSED if other is None else f'parent {other} {facts[other][0].lower()}'
            result.append(Readiness(node.id, 'blocked', reason, hard))
            continue
        losers = tuple(dict.fromkeys(ref.node_id for ref, port in node_refs(node)
                                     if port == 'model' and facts[ref.node_id][2]))
        if losers:   # a model port fed by a selection that recorded no winner
            result.append(Readiness(node.id, 'blocked', NO_WINNER, losers))
            continue
        pending = next((parent for parent in parents if facts[parent][0] not in NODE_FINISHED), None)
        untracked = next((parent for parent in parents if facts[parent][0] == 'COMPLETED' and not facts[parent][1]),
                         None)
        if pending or untracked:
            reason = f'waiting for {pending}' if pending else f'waiting for tracking of {untracked}'
            result.append(Readiness(node.id, 'waiting', reason, stopped))
            continue
        result.append(Readiness(node.id, 'ready', None, stopped))
    return tuple(result)


def _ancestors(plan, node_id) -> frozenset:
    """Every node the given node depends on, transitively (ports and explicit edges)."""
    edges = graph_edges(plan.graph)
    found, frontier = set(), [node_id]
    while frontier:
        current = frontier.pop()
        for parent, child in edges:
            if child == current and parent not in found:
                found.add(parent)
                frontier.append(parent)
    return frozenset(found)


def _failed_evaluations(plan, node, statuses):
    """The finished-but-unsuccessful evaluation parents of the selection a node compiles (itself or a parent)."""
    nodes = {entry.node.id: entry.node for entry in plan.nodes}
    selects = [node] if isinstance(node, SelectNode) else [
        nodes[ref.node_id] for ref, _ in node_refs(node) if isinstance(nodes[ref.node_id], SelectNode)]
    return tuple(sorted({ref.node_id for select in selects for ref in select.evaluations
                         if statuses[ref.node_id] in NODE_STOPPED}))


class GraphStore:
    def __init__(self, path, *, registry=None, artifact_store=None, binding_resolver=None,
                 clock: Callable[[], float] = time.time):
        self.jobs = JobStore(path)
        self.registry = registry or Registry(self.jobs.path.parent / 'registry')
        self.artifacts = artifact_store or ModelArtifactStore(self.jobs.path.parent / 'models')
        self.binding_resolver = binding_resolver
        self.clock = clock
        with self.jobs.connection() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS graph_runs (
                  id TEXT PRIMARY KEY, request_id TEXT UNIQUE NOT NULL, plan TEXT NOT NULL,
                  approval TEXT NOT NULL, status TEXT NOT NULL, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS nodes (
                  run_id TEXT NOT NULL, node_id TEXT NOT NULL, status TEXT NOT NULL,
                  job_id TEXT UNIQUE, manifest TEXT, error TEXT, PRIMARY KEY(run_id,node_id));
                CREATE TABLE IF NOT EXISTS edges (
                  run_id TEXT NOT NULL, parent_id TEXT NOT NULL, child_id TEXT NOT NULL,
                  PRIMARY KEY(run_id,parent_id,child_id));
                CREATE TABLE IF NOT EXISTS node_outputs (
                  run_id TEXT NOT NULL, node_id TEXT NOT NULL, attempt_id TEXT NOT NULL,
                  outputs TEXT NOT NULL, PRIMARY KEY(run_id,node_id,attempt_id));
                CREATE TABLE IF NOT EXISTS controller_leases (
                  run_id TEXT PRIMARY KEY, lease_token TEXT NOT NULL, expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS tracking_outbox (
                  attempt_id TEXT PRIMARY KEY, job_id TEXT NOT NULL, payload TEXT NOT NULL,
                  acknowledged INTEGER NOT NULL DEFAULT 0);
            ''')
            db.execute('BEGIN IMMEDIATE')
            for statement in MIGRATION_TABLES:
                db.execute(statement)
            for table, column, kind in MIGRATION_COLUMNS:
                if column not in {row['name'] for row in db.execute(f'PRAGMA table_info({table})')}:
                    db.execute(f'ALTER TABLE {table} ADD COLUMN {column} {kind}')
            # Every graph attempt made before reservations existed holds its maximum cost: never under-count.
            for row in db.execute('SELECT a.attempt_id,a.resource_id,a.created,j.id,j.graph_run_id,j.graph_node_id,'
                                  'j.manifest FROM job_attempts a JOIN jobs j ON a.job_id=j.id '
                                  'WHERE j.graph_run_id IS NOT NULL AND a.attempt_id NOT IN '
                                  '(SELECT attempt_id FROM budget_reservations)').fetchall():
                try:
                    cost = _attempt_cost(json.loads(row['manifest'])['operation'])
                except (KeyError, TypeError, ValueError):
                    # One unpriceable manifest must not stop the state directory from opening (8.1 review M1).
                    cost = _unpriced_cost(db, row['graph_run_id'])
                    db.execute('UPDATE nodes SET wait_reason=? WHERE run_id=? AND node_id=?',
                               (UNPRICED_ATTEMPT, row['graph_run_id'], row['graph_node_id']))
                db.execute(RESERVE, (row['attempt_id'], row['graph_run_id'], row['graph_node_id'], row['id'],
                                     row['resource_id'], cost, 'RESERVED', row['created']))

    @property
    def state_dir(self) -> Path:
        return self.jobs.path.parent

    def submit(self, plan, request_id, approval):
        plan = _check_plan(plan)
        if not isinstance(approval, ApprovalGrant):
            raise TypeError('explicit typed approval required')
        approval.validate_plan(plan)
        if not request_id or not isinstance(request_id, str):
            raise ValueError('request ID required')
        with self.jobs.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM graph_runs WHERE request_id=?', (request_id,)).fetchone()
            if row:
                if json.loads(row['plan'])['sha256'] != plan.sha256:
                    raise ValueError('request ID reused with different plan')
                run_id = row['id']
            else:
                run_id = uuid.uuid4().hex
                plan_json, approval_json = plan.model_dump_json(), approval.model_dump_json()
                db.execute('INSERT INTO graph_runs VALUES (?,?,?,?,?,?)',
                           (run_id, request_id, plan_json, approval_json, 'RUNNING', self.clock()))
                db.executemany('INSERT INTO nodes (run_id,node_id,status) VALUES (?,?,?)',
                               [(run_id, entry.node.id, 'PENDING') for entry in plan.nodes])
                db.executemany('INSERT INTO edges VALUES (?,?,?)', [(run_id, a, b) for a, b in graph_edges(plan.graph)])
                self._track(db, [graph_event(run_id, plan_json, approval_json)])
        return self.get_run(run_id)

    # -- tracking events (Task 8.3): enqueued in the ledger transaction they describe ------------------

    def _track(self, db, events):
        """Enqueue tracking events in the caller's ledger transaction: both commit, or neither does."""
        now = self.clock()
        for event in events:
            enqueue_event(db, event, now)

    def _attempt_chain(self, db, run, row, attempt_id, status, **evidence):
        """The graph, node, attempt-run, evidence (and registration) events of one finished attempt."""
        created = db.execute('SELECT created FROM job_attempts WHERE attempt_id=?', (attempt_id,)).fetchone()[0]
        manifest = row['manifest']
        facts = AttemptFacts(run_id=run['id'], node_id=row['graph_node_id'], job_id=row['id'], attempt_id=attempt_id,
                             launch_id=row['launch_id'], created=created,
                             manifest=json.loads(manifest) if isinstance(manifest, str) else manifest)
        return attempt_events(self, facts, run['plan'], run['approval'], status=status, **evidence)

    def _summarize(self, db, run, final):
        """The run's ``graph_summary`` event, once (the node statuses of its first final status)."""
        key = f"graph-summary:{run['id']}:{final}"
        if db.execute('SELECT 1 FROM tracking_events WHERE idempotency_key=?', (key,)).fetchone():
            return
        statuses = {row['node_id']: row['status'] for row in db.execute('SELECT node_id,status FROM nodes WHERE run_id=?',
                                                                         (run['id'],))}
        self._track(db, [graph_event(run['id'], run['plan'], run['approval']), summary_event(run['id'], final, statuses)])

    def get_run(self, run_id):
        with self.jobs.connection() as db:
            row = db.execute('SELECT * FROM graph_runs WHERE id=?', (run_id,)).fetchone()
            if row is None:
                raise ValueError('unknown graph run')
            result = dict(row)
            result['plan'] = GraphPlan.model_validate_json(row['plan'])
            result['approval'] = ApprovalGrant.model_validate_json(row['approval'])
            result['nodes'] = [dict(r) for r in db.execute('SELECT * FROM nodes WHERE run_id=? ORDER BY node_id', (run_id,))]
            return result

    # -- controller owner lease (one per state directory) -------------------------------------------

    def acquire_owner(self, owner_id: str, *, ttl_seconds: float = 60) -> OwnerLease:
        if not isinstance(owner_id, str) or not OWNER_ID.fullmatch(owner_id):
            raise ValueError('invalid controller owner ID')
        _owner_ttl(ttl_seconds)
        with self.jobs.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            now = self.clock()
            current = db.execute('SELECT expires FROM controller_owner WHERE id=1').fetchone()
            if current and current['expires'] > now:
                raise ValueError(OWNER_HELD)
            owner = OwnerLease(owner_id, uuid.uuid4().hex)
            db.execute('INSERT OR REPLACE INTO controller_owner (id,owner_id,lease_token,acquired,heartbeat,expires) '
                       'VALUES (1,?,?,?,?,?)', (owner.owner_id, owner.lease_token, now, now, now + ttl_seconds))
            return owner

    def _owner(self, db, owner, *, unexpired=True):
        if not isinstance(owner, OwnerLease):
            raise ValueError('controller owner lease required')
        row = db.execute('SELECT * FROM controller_owner WHERE id=1 AND owner_id=? AND lease_token=?',
                         (owner.owner_id, owner.lease_token)).fetchone()
        if row is None or unexpired and not row['expires'] > self.clock():
            raise ValueError(STALE_OWNER)
        return row

    def validate_owner(self, db, owner: OwnerLease):
        """The unexpired owner row inside the caller's transaction, else ``stale controller owner lease`` (the
        tracking outbox fences its claims with it)."""
        return self._owner(db, owner)

    def renew_owner(self, owner: OwnerLease, *, ttl_seconds: float = 60) -> None:
        _owner_ttl(ttl_seconds)
        with self.jobs.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            self._owner(db, owner)
            now = self.clock()
            db.execute('UPDATE controller_owner SET heartbeat=?,expires=? WHERE id=1', (now, now + ttl_seconds))

    def release_owner(self, owner: OwnerLease) -> None:
        """Expire our own owner lease now, so a successor need not wait for the TTL."""
        with self.jobs.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            self._owner(db, owner, unexpired=False)
            db.execute('UPDATE controller_owner SET expires=0 WHERE id=1')

    def owner_state(self) -> dict | None:
        """Who owns this state directory, for status display; never the lease token."""
        with self.jobs.connection() as db:
            row = db.execute('SELECT owner_id,acquired,heartbeat,expires FROM controller_owner WHERE id=1').fetchone()
        return dict(row) if row else None

    # -- run leases ---------------------------------------------------------------------------------

    def acquire_controller(self, run_id, ttl_seconds=60, *, owner: OwnerLease | None = None):
        """A run lease. With ``owner``, it replaces a lease that expired or that another owner holds (that owner
        lost its owner lease); an unexpired owner-less lease is never taken. Without ``owner``, as before Task 8."""
        if ttl_seconds <= 0:
            raise ValueError('controller lease TTL must be positive')
        with self.jobs.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            if owner is not None:
                self._owner(db, owner)
            if not db.execute('SELECT 1 FROM graph_runs WHERE id=?', (run_id,)).fetchone():
                raise ValueError('unknown graph run')
            now = self.clock()
            current = db.execute('SELECT * FROM controller_leases WHERE run_id=?', (run_id,)).fetchone()
            if current and current['expires'] > now and (
                    owner is None or current['owner_token'] in (None, owner.lease_token)):
                raise ValueError('controller lease already held')
            token = uuid.uuid4().hex
            db.execute('INSERT OR REPLACE INTO controller_leases (run_id,lease_token,expires,owner_token) '
                       'VALUES (?,?,?,?)', (run_id, token, now + ttl_seconds, owner.lease_token if owner else None))
            return ControllerLease(run_id, token)

    def _controller(self, db, lease):
        """The run row under a current run lease. A lease bound to an owner is valid only while that owner lease is
        (8.2 review M3): ``release_owner`` or the owner's expiry fences its run leases at once (D14)."""
        if not isinstance(lease, ControllerLease):
            raise ValueError('controller lease required')
        now = self.clock()
        row = db.execute('SELECT r.* FROM graph_runs r JOIN controller_leases c ON r.id=c.run_id '
                         'LEFT JOIN controller_owner o ON o.id=1 '
                         'WHERE c.run_id=? AND c.lease_token=? AND c.expires>? '
                         'AND (c.owner_token IS NULL OR (o.lease_token=c.owner_token AND o.expires>?))',
                         (lease.run_id, lease.lease_token, now, now)).fetchone()
        if row is None:
            raise ValueError('stale controller lease')
        return row

    def heartbeat_controller(self, lease, ttl_seconds=60):
        if ttl_seconds <= 0:
            raise ValueError('controller lease TTL must be positive')
        with self.jobs.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            self._controller(db, lease)
            db.execute('UPDATE controller_leases SET expires=? WHERE run_id=?', (self.clock() + ttl_seconds, lease.run_id))

    def ledger(self, run_id):
        """Typed records for trusted Task 8 binding reconstruction; no caller-supplied statuses."""
        with self.jobs.connection() as db:
            records = db.execute('SELECT n.node_id,j.*,o.outputs,t.acknowledged FROM nodes n JOIN jobs j ON n.job_id=j.id '
                                 'LEFT JOIN node_outputs o ON o.attempt_id=j.active_attempt_id '
                                 'LEFT JOIN tracking_outbox t ON t.attempt_id=j.active_attempt_id '
                                 'WHERE n.run_id=?', (run_id,)).fetchall()
            plan = self.get_run(run_id)['plan']
            result = []
            for row in records:
                state = {'COMPLETED': 'completed', 'FAILED': 'failed', 'CANCELED': 'cancelled'}.get(row['status'])
                if state is None or row['active_attempt_id'] is None:
                    continue
                evidence = NodeExecutionEvidence(node_id=row['node_id'], plan_sha256=plan.sha256,
                    job_id=row['id'], attempt_id=row['active_attempt_id'], manifest_sha256=json.loads(row['manifest'])['sha256'],
                    status=state, tracking_acknowledged=bool(row['acknowledged']))
                payload = json.loads(row['outputs']) if row['outputs'] else {}
                result.append((evidence, NodeOutputs(
                    model=NodeModelBinding.model_validate(payload['model']) if payload.get('model') else None,
                    evaluation=NodeEvaluationBinding.model_validate(payload['evaluation']) if payload.get('evaluation') else None,
                    inspected_targets=tuple(ModelTargetEvidence.model_validate(v) for v in payload.get('inspected_targets', [])),
                    selection=SelectionResult.model_validate(payload['selection']) if payload.get('selection') else None)))
            return tuple(result)

    def bindings_for(self, run_id: str, node_id: str) -> VerifiedBindings:
        """The trusted resolver hook: the VerifiedBindings that compile and the runner's continuation both use."""
        plan = self.get_run(run_id)['plan']
        entry = next((item for item in plan.nodes if item.node.id == node_id), None)
        if entry is None:
            raise ValueError('unknown graph node')
        if self.binding_resolver:
            resolver = self.binding_resolver
            if isinstance(resolver, LedgerBindingResolver):
                if resolver.store is not self:
                    raise ValueError('the ledger binding resolver belongs to another store')
                try:   # recorded evidence that no longer verifies is a ledger integrity error (8.5 review Minor 1)
                    bindings = resolver(plan, node_id, self.ledger(run_id), run_id=run_id)
                except LedgerIntegrityError:
                    raise
                except ValueError as exc:
                    raise LedgerIntegrityError(f'{LEDGER_INTEGRITY}: {exc}') from exc
            else:
                bindings = resolver(plan, node_id, self.ledger(run_id))
            if not isinstance(bindings, VerifiedBindings):
                raise TypeError('trusted resolver must return VerifiedBindings')
            return bindings
        ledger = self.ledger(run_id)
        # Root-node planning works without a controller. Conditional outputs require
        # the trusted controller to assemble and verify their complete producer lineage.
        return VerifiedBindings(registry=self.registry, artifact_store=self.artifacts,
            destination_profile=entry.allowed_resources[0].executor if entry.allowed_resources else None,
            producers=tuple(item.producer for item in plan.execution_choices.artifacts),
            base_targets=plan.execution_choices.base_targets,
            tracking_destination=plan.execution_choices.tracking_destination,
            completed_nodes=tuple(e for e, _ in ledger if e.status == 'completed'))

    # -- readiness and preparation (Task 8.2) -------------------------------------------------------

    def readiness(self, run_id) -> tuple[Readiness, ...]:
        """A pure read: which PENDING nodes may compile, which wait (and for what) and which are blocked."""
        plan = self.get_run(run_id)['plan']
        with self.jobs.connection() as db:
            facts = _node_facts(db, run_id)
        return _readiness(plan, facts)

    @staticmethod
    def _node_ids(plan, node_ids):
        if isinstance(node_ids, str):
            raise TypeError('node_ids must be an iterable of node IDs')
        requested = set(node_ids)
        if not requested <= {entry.node.id for entry in plan.nodes}:
            raise ValueError('unknown graph node')
        return requested

    def prepare_ready(self, controller_lease, node_ids: Iterable[str] | None = None):
        """Compile PENDING nodes into queued jobs.

        Without ``node_ids`` (the pre-Task-8 behaviour, unchanged) every PENDING node is tried and a compile error
        is recorded on the node, which stays PENDING. With ``node_ids`` only those the ledger finds ``ready`` now
        are compiled (others are skipped untouched), and a compile refusal fails the node and blocks its subtree; a
        run-wide resolver failure raises ``LedgerIntegrityError`` and fails nothing (8.2 review M-2).
        """
        with self.jobs.connection() as db:   # a fenced controller is refused even when nothing is left to prepare
            if node_ids is not None:
                db.execute('BEGIN IMMEDIATE')   # settle jobs canceled while queued first (8.2 review I-2)
                self._settle_canceled(db, self._controller(db, controller_lease))
            else:
                self._controller(db, controller_lease)
        run = self.get_run(controller_lease.run_id)
        gated = None
        if node_ids is not None:
            requested = self._node_ids(run['plan'], node_ids)
            gated = {item.node_id for item in self.readiness(run['id'])
                     if item.state == 'ready' and item.node_id in requested}
        prepared = []
        for entry in run['plan'].nodes:
            # Read immutable files outside the write transaction; recheck ownership on commit.
            node = next(n for n in run['nodes'] if n['node_id'] == entry.node.id)
            if node['job_id']:
                self.materialize_job_evidence(node['job_id'])
                continue
            if node['status'] != 'PENDING':
                continue
            if gated is not None and entry.node.id not in gated:
                continue
            try:
                bindings = self.bindings_for(run['id'], entry.node.id)
            except ValueError as exc:
                if gated is not None:
                    # The resolver re-verifies the node's recorded lineage (its ancestors' producers and persisted
                    # targets) and the active jobs' facts. A record that no longer verifies is not this node's
                    # refusal: fail nothing and let the controller hold it for an operator (8.2 review M-2).
                    if isinstance(exc, LedgerIntegrityError):
                        raise
                    raise LedgerIntegrityError(f'{LEDGER_INTEGRITY}: {exc}') from exc
                self._record_compile_error(controller_lease, run['id'], entry.node.id, exc)
                continue
            try:
                operation = compile_node(run['plan'], entry.node.id, bindings)
                if isinstance(operation, LocalSelectSpec):
                    body = {'schema_version': 2, 'operation': operation.model_dump(mode='json')}
                    manifest = {**body, 'sha256': digest(canonical(body))}
                else:
                    manifest = resolve_operation(operation, self.registry)
            except ValueError as exc:
                if gated is not None:
                    self._refuse_node(controller_lease, entry.node.id, str(exc))
                    continue
                self._record_compile_error(controller_lease, run['id'], entry.node.id, exc)
                continue
            with self.jobs.connection() as db:
                db.execute('BEGIN IMMEDIATE')
                current = self._controller(db, controller_lease)
                if current['status'] != 'RUNNING':
                    return prepared
                node = db.execute('SELECT * FROM nodes WHERE run_id=? AND node_id=?', (run['id'], entry.node.id)).fetchone()
                if node['job_id']:
                    job_id = node['job_id']
                elif gated is not None and node['status'] != 'PENDING':
                    continue   # blocked or canceled since readiness was read
                else:
                    job = self.jobs._submit(db, manifest, 'graph:' + run['id'] + ':' + entry.node.id,
                                            graph_run_id=run['id'], graph_node_id=entry.node.id)
                    job_id = job['id']
                    db.execute("UPDATE jobs SET status='QUEUED' WHERE id=?", (job_id,))
                    db.execute('INSERT INTO events VALUES (?,?,?,?)', (job_id, 'QUEUED', time.time(), 'graph ready'))
                    db.execute("UPDATE nodes SET job_id=?,manifest=?,status='READY',error=NULL WHERE run_id=? AND node_id=?",
                               (job_id, canonical(manifest).decode(), run['id'], entry.node.id))
            prepared.append(job_id)
            self.materialize_job_evidence(job_id)
        return prepared

    def _record_compile_error(self, controller_lease, run_id, node_id, exc):
        """The pre-Task-8 (ungated) path: the error is recorded and the node stays PENDING."""
        with self.jobs.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            self._controller(db, controller_lease)
            db.execute('UPDATE nodes SET error=? WHERE run_id=? AND node_id=? AND job_id IS NULL',
                       (str(exc), run_id, node_id))

    def _refuse_node(self, controller_lease, node_id, error):
        """A ready node whose compilation is refused: FAILED, and its subtree BLOCKED (one transaction)."""
        with self.jobs.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            run = self._controller(db, controller_lease)
            node = db.execute('SELECT status,job_id FROM nodes WHERE run_id=? AND node_id=?',
                              (run['id'], node_id)).fetchone()
            if run['status'] != 'RUNNING' or node['status'] != 'PENDING' or node['job_id']:
                return
            self._fail_node(db, run['id'], node_id, error)
            self._block_descendants(db, run['id'], GraphPlan.model_validate_json(run['plan']), node_id)

    def materialize_job_evidence(self, job_id):
        return self.jobs.materialize_job_evidence(job_id)

    def _clear_unpriced(self, db, run_id, node_id):
        """The node's attempt settled: the backfill's unpriced-attempt notice no longer applies (8.2 review M-6)."""
        db.execute('UPDATE nodes SET wait_reason=NULL WHERE run_id=? AND node_id=? AND wait_reason=?',
                   (run_id, node_id, UNPRICED_ATTEMPT))

    @staticmethod
    def _locked(db, job_id) -> bool:
        return db.execute('SELECT 1 FROM gpu_locks WHERE job_id=? UNION ALL SELECT 1 FROM resource_leases '
                          'WHERE job_id=?', (job_id, job_id)).fetchone() is not None

    def fail_node(self, controller_lease, node_id, error) -> None:
        """Mark a node FAILED: an unprepared one, or one whose attempt is final (lock released). Idempotent."""
        if not isinstance(error, str) or not error:
            raise ValueError('a failure reason is required')
        with self.jobs.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            run = self._controller(db, controller_lease)
            self._fail_node(db, run['id'], node_id, error)

    def _fail_node(self, db, run_id, node_id, error):
        row = db.execute('SELECT n.status,n.job_id,j.status AS job_status FROM nodes n LEFT JOIN jobs j ON n.job_id=j.id '
                         'WHERE n.run_id=? AND n.node_id=?', (run_id, node_id)).fetchone()
        if row is None:
            raise ValueError('unknown graph node')
        if row['status'] == 'FAILED':
            return
        if row['status'] not in {'PENDING', 'READY', 'RUNNING'}:
            raise ValueError('only an unfinished node can fail')
        if row['job_id'] and (row['job_status'] not in {'FAILED', 'CANCELED'} or self._locked(db, row['job_id'])):
            raise ValueError('the node attempt is not final')
        db.execute("UPDATE nodes SET status='FAILED',error=?,retry_not_before=NULL,updated=? WHERE run_id=? AND node_id=?",
                   (error, self.clock(), run_id, node_id))

    def block_descendants(self, controller_lease, node_id) -> tuple[str, ...]:
        """BLOCK every PENDING descendant that readiness finds blocked (a failed/blocked/canceled parent, a refused
        partial selection, or a model port fed by a no-winner selection). Returns the nodes it blocked."""
        with self.jobs.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            run = self._controller(db, controller_lease)
            plan = GraphPlan.model_validate_json(run['plan'])
            if node_id not in {entry.node.id for entry in plan.nodes}:
                raise ValueError('unknown graph node')
            return self._block_descendants(db, run['id'], plan, node_id)

    def _block_descendants(self, db, run_id, plan, node_id):
        edges = graph_edges(plan.graph)
        descendants, frontier = set(), [node_id]
        while frontier:
            current = frontier.pop()
            for parent, child in edges:
                if parent == current and child not in descendants:
                    descendants.add(child)
                    frontier.append(child)
        facts, blocked, changed = _node_facts(db, run_id), [], True
        while changed:   # a fixpoint: blocking one node may block its own children
            changed = False
            for item in _readiness(plan, facts):
                if item.node_id in descendants and item.state == 'blocked':
                    db.execute("UPDATE nodes SET status='BLOCKED',error=?,updated=? WHERE run_id=? AND node_id=? "
                               "AND status='PENDING'", (item.reason, self.clock(), run_id, item.node_id))
                    facts[item.node_id] = ('BLOCKED', *facts[item.node_id][1:])
                    blocked.append(item.node_id)
                    changed = True
        return tuple(blocked)

    def set_wait_reason(self, controller_lease, node_id, reason: str | None) -> None:
        """Why a node is not progressing, for status display (the controller's words; never a credential)."""
        if reason is not None and (not isinstance(reason, str) or not reason or len(reason) > 500):
            raise ValueError('a wait reason is a short non-empty string or None')
        with self.jobs.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            run = self._controller(db, controller_lease)
            if not db.execute('UPDATE nodes SET wait_reason=?,updated=? WHERE run_id=? AND node_id=?',
                              (reason, self.clock(), run['id'], node_id)).rowcount:
                raise ValueError('unknown graph node')

    # -- budget and claims --------------------------------------------------------------------------

    @staticmethod
    def _reserved(db, run_id) -> float:
        return float(db.execute("SELECT COALESCE(SUM(max_cost_usd),0) FROM budget_reservations "
                                "WHERE run_id=? AND status='RESERVED'", (run_id,)).fetchone()[0])

    def reserved_budget(self, run_id) -> float:
        """The run's reserved maximum cost: every attempt's reservation that was not voided."""
        with self.jobs.connection() as db:
            return self._reserved(db, run_id)

    @staticmethod
    def _counted_attempts(db, job_id) -> int:
        """Attempts that count against ``max_attempts``: all of the job's attempts but the uncounted dispositions."""
        return db.execute('SELECT COUNT(*) FROM job_attempts a WHERE a.job_id=? AND NOT EXISTS ('
                          'SELECT 1 FROM attempt_dispositions d WHERE d.attempt_id=a.attempt_id AND d.counted=0)',
                          (job_id,)).fetchone()[0]

    def _dispose(self, db, run_id, row, attempt_id, disposition, counted, detail):
        db.execute(DISPOSE, (attempt_id, run_id, row['graph_node_id'], row['id'], disposition, counted, detail,
                             self.clock()))

    def claim_ready(self, resource_facts, controller_lease, *, job_id=None, executor=None):
        """Claim one queued job of this run: with ``resource_facts`` an operation that fits them, with None only a
        local selection. The attempt's maximum cost is reserved in the same transaction.

        ``job_id`` and ``executor`` (8.5 review Important 1) restrict the claim to that job, or to jobs pinned to that
        public executor profile: the controller opens one job's destination and must never hand it to another job
        whose profile (an alias of the same Pod) was refused or needs another runtime. Nothing is written otherwise.
        """
        from pydantic import TypeAdapter

        from .executor_profiles import ResourceObservation, observation_fits
        if resource_facts is not None:
            resource_facts = TypeAdapter(ResourceObservation).validate_python(resource_facts.model_dump())
        with self.jobs.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            run = self._controller(db, controller_lease)
            if run['status'] != 'RUNNING':
                return None
            plan = GraphPlan.model_validate_json(run['plan'])
            active = db.execute("SELECT COUNT(*) FROM jobs WHERE graph_run_id=? AND status IN ('PROVISIONING','RUNNING','EVALUATING')",
                                (run['id'],)).fetchone()[0]
            if active >= plan.graph.max_concurrent:
                return None
            reserved = self._reserved(db, run['id'])
            ceiling = plan.max_estimated_cost_usd * (1 + 1e-9)   # defence in depth against float summation order
            for row in db.execute("SELECT j.* FROM jobs j JOIN nodes n ON j.id=n.job_id WHERE n.run_id=? "
                                  "AND j.status='QUEUED' AND j.id NOT IN (SELECT job_id FROM gpu_locks) ORDER BY j.priority DESC,j.created", (run['id'],)).fetchall():
                if job_id is not None and row['id'] != job_id:
                    continue
                operation = json.loads(row['manifest'])['operation']
                if (operation['kind'] == 'select') != (resource_facts is None):
                    continue   # a GPU observation never claims the local selection; None claims nothing else
                if (executor is not None
                        and OperationResources.model_validate(operation['resources']).executor != executor):
                    continue
                if operation['kind'] == 'select':
                    resource = 'local:' + run['id']
                else:
                    pinned = TypeAdapter(ResourceObservation).validate_python(operation['resources']['facts'])
                    if not observation_fits(resource_facts, pinned):
                        continue
                    # A simulated attempt never holds, or waits for, the real hardware it names.
                    resource = ('simulation:' if plan.simulation else '') + resource_facts.exclusivity_key
                if self._counted_attempts(db, row['id']) >= plan.graph.retry_policy.max_attempts:
                    continue   # authoritative: an integer count, independent of the budget's floats
                cost = _attempt_cost(operation)
                if reserved + cost > ceiling:
                    continue
                if self.jobs._resource_blocked(db, resource, resource_facts):
                    continue
                job = self.jobs._claim(db, row, resource, resource_facts)
                db.execute(RESERVE, (job['lease'].attempt_id, run['id'], row['graph_node_id'], row['id'], resource,
                                     cost, 'RESERVED', self.clock()))
                db.execute("UPDATE nodes SET status='RUNNING' WHERE run_id=? AND node_id=?", (run['id'], row['graph_node_id']))
                self._track(db, [graph_event(run['id'], run['plan'], run['approval']),   # the node's MLflow run
                                 node_event(run['id'], run['plan'], row['graph_node_id'], row['id'],
                                            json.loads(row['manifest']))])
                return job
        return None

    def return_unlaunched(self, controller_lease, attempt_lease, *, reason: str) -> str:
        """Undo a claim whose launch never began (``begin_launch`` refused or never called), so no remote process
        can exist. The lock is freed, the reservation voided and the attempt not counted. The job queues again
        (node READY), or is canceled with its run or by a job-level cancel. Returns the job's new status."""
        if not isinstance(reason, str) or not reason:
            raise ValueError('a reason is required to return an attempt')
        with self.jobs.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            run = self._controller(db, controller_lease)
            row = self.jobs.validate_lease(db, attempt_lease)
            if row['graph_run_id'] != run['id']:
                raise ValueError('current graph attempt required')
            if row['status'] != 'PROVISIONING' or row['launch_started'] or row['legacy']:
                raise ValueError('only an unlaunched provisioning attempt can be returned')
            return self._return_unlaunched(db, run, row, attempt_lease, reason)

    def _return_unlaunched(self, db, run, row, attempt_lease, reason):
        self.jobs._release(db, attempt_lease)
        self._clear_unpriced(db, run['id'], row['graph_node_id'])
        db.execute("UPDATE budget_reservations SET status='VOID' WHERE attempt_id=?", (attempt_lease.attempt_id,))
        self._dispose(db, run['id'], row, attempt_lease.attempt_id, 'returned_unlaunched', 0, reason)
        now = time.time()
        if run['status'] == 'CANCELED' or row['cancel_requested']:
            # A job-level cancel is never requeued into a real launch (8.1 review I1).
            db.execute("UPDATE jobs SET status='CANCELED',updated=? WHERE id=?", (now, row['id']))
            db.execute('INSERT INTO events VALUES (?,?,?,?)', (row['id'], 'CANCELED', now, reason))
            # The attempt stays the job's evidence, but its actor is fenced.
            db.execute('UPDATE job_attempts SET lease_token=? WHERE attempt_id=?',
                       (uuid.uuid4().hex, attempt_lease.attempt_id))
            db.execute("UPDATE nodes SET status='CANCELED',updated=? WHERE run_id=? AND node_id=?",
                       (self.clock(), run['id'], row['graph_node_id']))
            if run['status'] != 'CANCELED':   # the run goes on without this node's subtree
                self._block_descendants(db, run['id'], GraphPlan.model_validate_json(run['plan']), row['graph_node_id'])
            return 'CANCELED'
        for status in ('RETRYING', 'QUEUED'):
            db.execute('INSERT INTO events VALUES (?,?,?,?)', (row['id'], status, now, reason))
        # Like reset_for_resume: the returned attempt is over, so its lease no longer validates.
        db.execute("UPDATE jobs SET status='QUEUED',active_attempt_id=NULL,updated=? WHERE id=?", (now, row['id']))
        db.execute("UPDATE nodes SET status='READY',updated=? WHERE run_id=? AND node_id=?",
                   (self.clock(), run['id'], row['graph_node_id']))
        return 'QUEUED'

    def takeover_attempt(self, controller_lease, job_id):
        """Reissue only the actor token under the current graph controller lease."""
        with self.jobs.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            self._controller(db, controller_lease)
            attempt = db.execute('SELECT a.* FROM job_attempts a JOIN jobs j ON j.active_attempt_id=a.attempt_id '
                                 'WHERE j.id=? AND j.graph_run_id=?', (job_id, controller_lease.run_id)).fetchone()
            if attempt is None:
                raise ValueError('current graph attempt required')
            lease = AttemptLease(job_id, attempt['attempt_id'], attempt['lease_token'], attempt['resource_id'])
            row = self.jobs.validate_lease(db, lease)
            return self.jobs._takeover(db, lease, row)

    # -- outputs ------------------------------------------------------------------------------------

    def _output_payload(self, job, attempt_id, outputs) -> str:
        """Check typed outputs against the attempt ledger and return the canonical receipt payload."""
        if not job['graph_run_id']:
            raise ValueError('graph output requires graph-owned job')
        operation = job['manifest']['operation']
        kind = operation['kind']
        if outputs.selection is not None and (kind != 'select' or outputs.model is not None
                                              or outputs.evaluation is not None):
            raise ValueError('only a no-winner selection records a bare selection receipt')
        if kind in {'train', 'merge', 'materialize', 'select'} and outputs.model is None and outputs.selection is None:
            raise ValueError('completed operation requires its model output receipt')
        if kind == 'evaluate' and outputs.evaluation is None:
            raise ValueError('completed evaluation requires its evaluation output receipt')
        if outputs.model and kind == 'evaluate':
            raise ValueError('evaluation cannot publish a model output')
        if outputs.selection is not None:
            receipt = resolve_local_selection(LocalSelectSpec.model_validate(operation))
            if receipt.status != 'no_winner' or SelectionResult.model_validate(outputs.selection.model_dump()) != receipt:
                raise ValueError('selection receipt mismatch')
        model = descriptor = None
        if outputs.model:
            model = NodeModelBinding.model_validate(outputs.model.model_dump())
            if model.node_id != job['graph_node_id']:
                raise ValueError('output node mismatch')
            descriptor = self.artifacts.verify(model.model)
            plan = self.get_run(job['graph_run_id'])['plan']
            entry = next(item for item in plan.nodes if item.node.id == job['graph_node_id'])
            if descriptor.root not in entry.permitted_model_roots:
                raise ValueError('output model root differs from planned alternatives')
            if kind != 'select' and descriptor.simulation != plan.simulation:
                raise ValueError('output model simulation differs from plan')
            if kind == 'select':
                receipt = resolve_local_selection(LocalSelectSpec.model_validate(operation))
                if model.selection != receipt or model.model != receipt.winner:
                    raise ValueError('selection receipt mismatch')
            elif (descriptor.producer_job_id, descriptor.producer_attempt_id, descriptor.producer_manifest_sha256) != (
                    job['id'], attempt_id, job['manifest']['sha256']):
                raise ValueError('artifact producer differs from attempt ledger')
        if outputs.evaluation:
            evaluation = NodeEvaluationBinding.model_validate(outputs.evaluation.model_dump())
            if (kind != 'evaluate' or evaluation.node_id != job['graph_node_id']
                    or evaluation.evaluation.producer_attempt_id != attempt_id
                    or evaluation.evaluation.model.model_dump(mode='json') != operation['model']
                    # The same identity compile_node's select path and BenchmarkRevision use (plan F11).
                    or evaluation.evaluation.benchmark_sha256
                    != BenchmarkSpec.model_validate(operation['benchmark']).fingerprint()):
                raise ValueError('evaluation receipt differs from attempt ledger')
            if evaluation.producer is not None or evaluation.tracking_acknowledged:
                raise ValueError('completion/tracking evidence must be derived from ledger')
        targets = []
        if outputs.inspected_targets:
            if kind == 'evaluate' or outputs.evaluation is not None:
                raise ValueError('evaluation cannot publish inspected targets')
            if model is None or kind == 'select':
                raise ValueError("inspected targets belong to the producing attempt's model output")
            # The complete linear inventory of the verified full-model output (coordinator item 4).
            targets = sorted({canonical(verify_inspected_targets(item, descriptor, model.model).model_dump(mode='json'))
                              for item in outputs.inspected_targets})
        body = {'model': outputs.model.model_dump(mode='json') if outputs.model else None,
                'evaluation': outputs.evaluation.model_dump(mode='json') if outputs.evaluation else None,
                'inspected_targets': [json.loads(item) for item in targets]}
        if outputs.selection is not None:   # only then: earlier receipts keep their exact bytes
            body['selection'] = outputs.selection.model_dump(mode='json')
        return canonical(body).decode()

    def record_outputs(self, attempt_lease, outputs):
        if not isinstance(outputs, NodeOutputs):
            raise TypeError('typed runtime NodeOutputs required')
        job = self.jobs.get(attempt_lease.job_id)
        payload = self._output_payload(job, attempt_lease.attempt_id, outputs)
        with self.jobs.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = self.jobs.validate_lease(db, attempt_lease)
            if row['status'] != 'COMPLETED':
                raise ValueError('outputs require completed current attempt')
            existing = db.execute('SELECT outputs FROM node_outputs WHERE attempt_id=?', (attempt_lease.attempt_id,)).fetchone()
            if existing and existing['outputs'] != payload:
                raise ValueError('immutable output receipt mismatch')
            if existing:
                # Recorded once already (its events with it): a rerun never re-hashes the attempt's evidence, which a
                # later worker.log may have changed (8.3 review Minor 1), as complete_local_selection's rerun.
                return
            db.execute('INSERT OR IGNORE INTO node_outputs VALUES (?,?,?,?)',
                       (job['graph_run_id'], job['graph_node_id'], attempt_lease.attempt_id, payload))
            db.execute('INSERT OR IGNORE INTO tracking_outbox (attempt_id,job_id,payload,acknowledged) VALUES (?,?,?,0)',
                       (attempt_lease.attempt_id, job['id'], payload))
            db.execute("UPDATE nodes SET status='COMPLETED' WHERE run_id=? AND node_id=?", (job['graph_run_id'], job['graph_node_id']))
            self._clear_unpriced(db, job['graph_run_id'], job['graph_node_id'])
            run = db.execute('SELECT * FROM graph_runs WHERE id=?', (job['graph_run_id'],)).fetchone()
            self._track(db, self._attempt_chain(db, run, row, attempt_lease.attempt_id, 'COMPLETED',
                                                outputs_json=payload))

    def complete_local_selection(self, controller_lease, claimed) -> NodeOutputs | None:
        """Finish a claimed local selection in one transaction: the receipt, the job's RUNNING -> EVALUATING ->
        COMPLETED steps, the outputs, the tracking row and events, and the lock release. A no-winner receipt blocks
        the model children in the same transaction. A cancel (of the run or the job) wins over the join and records
        nothing; a pause returns the attempt unlaunched. None in both cases.

        A deterministic refusal while verifying the receipt (e.g. a winner artifact that no longer verifies) is
        settled here too, after the cancel check (8.2 review I-3): the job FAILS, the lock is released and the
        attempt gets a counted, terminal ``refused_before_launch`` disposition (a local selection never launches,
        and this is the analogue of the runner's own pre-launch checks). Returning it would loop uncounted."""
        lease = claimed.get('lease') if isinstance(claimed, dict) else None
        if not isinstance(lease, AttemptLease):
            raise ValueError('a claimed attempt lease is required')
        job = self.jobs.get(lease.job_id)
        operation = job['manifest']['operation']
        if not job['graph_run_id'] or operation.get('kind') != 'select':
            raise ValueError('a local selection attempt is required')
        node_id, outputs, payload, refusal = job['graph_node_id'], None, None, None
        try:   # verified before the transaction; a refusal is settled inside it
            receipt = resolve_local_selection(LocalSelectSpec.model_validate(operation))
            outputs = (NodeOutputs(model=NodeModelBinding(node_id=node_id, model=receipt.winner, selection=receipt))
                       if receipt.status == 'selected' else NodeOutputs(selection=receipt))
            payload = self._output_payload(job, lease.attempt_id, outputs)
        except ValueError as exc:
            refusal = f'{type(exc).__name__}: {exc}'
        with self.jobs.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            run = self._controller(db, controller_lease)
            row = self.jobs.validate_lease(db, lease)
            if row['graph_run_id'] != run['id']:
                raise ValueError('current graph attempt required')
            existing = db.execute('SELECT outputs FROM node_outputs WHERE attempt_id=?', (lease.attempt_id,)).fetchone()
            if row['status'] == 'COMPLETED' and existing:
                if refusal is not None:
                    raise ValueError(refusal)
                if existing['outputs'] != payload:
                    raise ValueError('immutable output receipt mismatch')
                return outputs   # recorded once; a rerun returns the same receipt
            if row['status'] != 'PROVISIONING' or row['launch_started'] or row['legacy']:
                raise ValueError('only a claimed local selection can complete')
            now = time.time()
            if run['status'] == 'CANCELED' or row['cancel_requested']:
                detail = 'canceled before the local selection completed'
                db.execute("UPDATE jobs SET status='CANCELED',error=?,updated=? WHERE id=?", (detail, now, row['id']))
                db.execute('INSERT INTO events VALUES (?,?,?,?)', (row['id'], 'CANCELED', now, detail))
                db.execute("UPDATE nodes SET status='CANCELED',updated=? WHERE run_id=? AND node_id=?",
                           (self.clock(), run['id'], node_id))
                self.jobs._release(db, lease)
                db.execute("UPDATE budget_reservations SET status='VOID' WHERE attempt_id=?", (lease.attempt_id,))
                self._dispose(db, run['id'], row, lease.attempt_id, 'canceled', 0, detail)   # it never ran
                if run['status'] != 'CANCELED':
                    self._block_descendants(db, run['id'], GraphPlan.model_validate_json(run['plan']), node_id)
                return None
            if refusal is not None:   # deterministic: counted and terminal, never returned to loop
                db.execute("UPDATE jobs SET status='FAILED',error=?,updated=? WHERE id=?", (refusal, now, row['id']))
                db.execute('INSERT INTO events VALUES (?,?,?,?)', (row['id'], 'FAILED', now, refusal))
                self.jobs._release(db, lease)
                self._dispose(db, run['id'], row, lease.attempt_id, 'refused_before_launch', 1, refusal)
                self._settle_node(db, run, row, 'refused_before_launch', 1, refusal)
                self._track(db, self._attempt_chain(db, run, row, lease.attempt_id, 'FAILED',
                                                    disposition='refused_before_launch', error=refusal))
                return None
            if run['status'] != 'RUNNING':
                self._return_unlaunched(db, run, row, lease, 'graph paused before the local selection')
                return None
            for status in ('RUNNING', 'EVALUATING', 'COMPLETED'):   # as JobStore.transition: one row per status
                db.execute('INSERT INTO events VALUES (?,?,?,?)', (row['id'], status, now, None))
            db.execute("UPDATE jobs SET status='COMPLETED',error=NULL,updated=? WHERE id=?", (now, row['id']))
            db.execute('INSERT INTO node_outputs VALUES (?,?,?,?)', (run['id'], node_id, lease.attempt_id, payload))
            db.execute('INSERT INTO tracking_outbox (attempt_id,job_id,payload,acknowledged) VALUES (?,?,?,0)',
                       (lease.attempt_id, row['id'], payload))
            db.execute("UPDATE nodes SET status='COMPLETED',updated=? WHERE run_id=? AND node_id=?",
                       (self.clock(), run['id'], node_id))
            self.jobs._release(db, lease)
            if outputs.model is None:   # a no-winner receipt blocks the model children now (8.2 review M-3)
                self._block_descendants(db, run['id'], GraphPlan.model_validate_json(run['plan']), node_id)
            self._track(db, self._attempt_chain(db, run, row, lease.attempt_id, 'COMPLETED', outputs_json=payload))
        return outputs

    # Tests only (8.3 review Minor 6): the controller and the outbox never open the gate without evidence.
    test_acknowledgement = False

    def acknowledge_tracking(self, attempt_lease):
        """A test hook that opens the tracking gate without any evidence. Refused unless
        ``GraphStore.test_acknowledgement`` is set (``tests/conftest.py`` sets it); in production only the tracking
        outbox's ``record_receipt`` of an ``attempt_evidence`` event opens the gate."""
        if not type(self).test_acknowledgement:
            raise ValueError(TEST_ACKNOWLEDGEMENT)
        with self.jobs.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            self.jobs.validate_lease(db, attempt_lease)
            changed = db.execute('UPDATE tracking_outbox SET acknowledged=1 WHERE attempt_id=?', (attempt_lease.attempt_id,)).rowcount
            if not changed:
                raise ValueError('tracking outbox receipt is missing')

    def producer_evidence(self, attempt_lease, model):
        """Reconstruct original producer identity; selected aliases retain their origin."""
        job = self.jobs.get(attempt_lease.job_id)
        with self.jobs.connection() as db:
            self.jobs.validate_lease(db, attempt_lease)
            ack = db.execute('SELECT acknowledged FROM tracking_outbox WHERE attempt_id=?', (attempt_lease.attempt_id,)).fetchone()
        descriptor = self.artifacts.verify(model)
        recorded = next((outputs.model for evidence, outputs in self.ledger(job['graph_run_id'])
                         if evidence.attempt_id == attempt_lease.attempt_id), None)
        if recorded is None or recorded.model != model:
            raise ValueError('model is not recorded in the producer attempt ledger')
        if job['status'] != 'COMPLETED' or not ack or not ack['acknowledged']:
            raise ValueError('producer completion/tracking is not ready')
        if (descriptor.producer_job_id, descriptor.producer_attempt_id, descriptor.producer_manifest_sha256) != (
                job['id'], attempt_lease.attempt_id, job['manifest']['sha256']):
            raise ValueError('original artifact producer differs from ledger')
        op = job['manifest']['operation']
        return ProducerEvidence(model=model, job_id=job['id'], attempt_id=attempt_lease.attempt_id,
            manifest_sha256=job['manifest']['sha256'], runtime_profile=op['resources']['runtime']['profile'],
            training_stage=op.get('config', {}).get('training_stage'), status='completed', tracking_acknowledged=True,
            plan_sha256=op['lineage']['plan_sha256'], node_id=job['graph_node_id'])

    # -- dispositions, retries and safe release (Task 8.2) ------------------------------------------

    @staticmethod
    def _current_attempt(db, run_id, job_id):
        row = db.execute('SELECT j.*,a.attempt_id,a.lease_token,a.resource_id,a.launch_id,a.launch_started,a.legacy '
                         'FROM jobs j JOIN job_attempts a ON j.active_attempt_id=a.attempt_id '
                         'WHERE j.id=? AND j.graph_run_id=?', (job_id, run_id)).fetchone()
        if row is None:
            raise ValueError('current graph attempt required')
        return row

    def attempt_disposition(self, job_id) -> dict | None:
        """The disposition of the job's current attempt (attempt_id, disposition, counted, detail), or None."""
        with self.jobs.connection() as db:
            row = db.execute('SELECT d.attempt_id,d.disposition,d.counted,d.detail FROM jobs j '
                             'JOIN attempt_dispositions d ON d.attempt_id=j.active_attempt_id WHERE j.id=?',
                             (job_id,)).fetchone()
        return dict(row) if row else None

    def classify_attempt(self, controller_lease, job_id, kind: FailureKind) -> str:
        """Record how a finished attempt ended (once; immutable) and settle its node. Returns the disposition.

        ``remote_failed`` (an accepted FAILED remote result) is counted and retryable after the backoff, within
        ``max_attempts``; otherwise the node FAILS and its subtree is BLOCKED. ``output_invalid``,
        ``refused_before_launch`` (the runner's own deterministic checks) and ``failed_before_submit`` are counted
        and terminal; ``canceled`` is terminal and cancels the node. ``uncertain`` is reconciled, never classified.

        An attempt must have released its lock, with two provable exceptions released here: a CANCELED attempt
        whose accepted result exists for its own persisted launch ID (a crash between the CANCELED transition and
        ``release_gpu``; 7.5 Minor B), and a FAILED attempt whose launch never began (``launch_started=0``: a stale
        recover of an attempt refused at the launch gate). The latter provably never ran, so it is uncounted, its
        reservation voided and its node retryable at once (D4; 8.1 review M4). Both releases rotate the attempt token
        in the same transaction, so the old actor is fenced at once (8.2 review M-1).

        An attempt the store already settled uncounted (``released_before_contact``, ``returned_unlaunched``) is a
        no-op for its equivalent kind and returns that existing disposition (8.2 review M-4).
        """
        if kind == 'uncertain':
            raise ValueError('an uncertain attempt is reconciled, never classified')
        if kind not in CLASSIFIED:
            raise ValueError('unknown failure kind')
        with self.jobs.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            run = self._controller(db, controller_lease)
            row = self._current_attempt(db, run['id'], job_id)
            existing = db.execute('SELECT disposition FROM attempt_dispositions WHERE attempt_id=?',
                                  (row['attempt_id'],)).fetchone()
            if existing:
                if existing['disposition'] == kind:
                    return kind
                if kind in SETTLED_AS.get(existing['disposition'], ()):
                    return existing['disposition']   # already settled uncounted by the store (8.2 review M-4)
                raise ValueError(DISPOSITION_IMMUTABLE)
            canceled = kind == 'canceled'
            if (row['status'] != ('CANCELED' if canceled else 'FAILED') or row['legacy']
                    or not canceled and bool(row['launch_started']) != (kind != 'refused_before_launch')):
                raise ValueError('failure kind differs from the attempt')
            counted = 1
            if self._locked(db, job_id):
                if kind == 'refused_before_launch':
                    counted = 0   # begin_launch never committed, and cannot: the job is no longer PROVISIONING
                elif not (canceled and accepted_result(self.state_dir, job_id, row['launch_id'], row['attempt_id'])):
                    raise ValueError('attempt still holds its lock; reconcile it first')
                self.jobs._release(db, AttemptLease(job_id, row['attempt_id'], row['lease_token'], row['resource_id']))
                # The released attempt is over: fence its old actor at once (8.2 review M-1).
                db.execute('UPDATE job_attempts SET lease_token=? WHERE attempt_id=?',
                           (uuid.uuid4().hex, row['attempt_id']))
            if not counted:
                db.execute("UPDATE budget_reservations SET status='VOID' WHERE attempt_id=?", (row['attempt_id'],))
            self._dispose(db, run['id'], row, row['attempt_id'], kind, counted, row['error'])
            self._settle_node(db, run, row, kind, counted)
            if counted:   # an attempt that provably never ran gets no MLflow attempt run (8.3 review Minor 10)
                self._track(db, self._attempt_chain(db, run, row, row['attempt_id'],
                                                    'CANCELED' if canceled else 'FAILED', disposition=kind,
                                                    error=row['error']))
            return kind

    def _settle_node(self, db, run, row, kind, counted, error=None):
        """A finished attempt's node: retry later, or CANCELED/FAILED with its subtree blocked."""
        plan = GraphPlan.model_validate_json(run['plan'])
        node_id, now = row['graph_node_id'], self.clock()
        self._clear_unpriced(db, run['id'], node_id)
        if run['status'] == 'CANCELED':   # nothing of a canceled run runs again
            db.execute("UPDATE nodes SET status='CANCELED',retry_not_before=NULL,updated=? WHERE run_id=? AND node_id=? "
                       "AND status!='COMPLETED'", (now, run['id'], node_id))
            return
        retryable = kind == 'remote_failed' or not counted
        if (retryable and not row['cancel_requested']
                and self._counted_attempts(db, row['id']) < plan.graph.retry_policy.max_attempts):
            delay = plan.graph.retry_policy.backoff_seconds if counted else 0   # an attempt that never ran waits not
            db.execute('UPDATE nodes SET retry_not_before=?,updated=? WHERE run_id=? AND node_id=?',
                       (now + delay, now, run['id'], node_id))
            return
        # CANCELED when the attempt was canceled, or a job-level cancel stopped the retry it would have had.
        status = 'CANCELED' if kind == 'canceled' or retryable and row['cancel_requested'] else 'FAILED'
        db.execute('UPDATE nodes SET status=?,error=?,retry_not_before=NULL,updated=? WHERE run_id=? AND node_id=?',
                   (status, error or row['error'] or kind, now, run['id'], node_id))
        self._block_descendants(db, run['id'], plan, node_id)

    def requeue_retry(self, controller_lease, job_id) -> bool:
        """Queue a retry of a finished attempt: a RUNNING run, a FAILED job without a lock or a cancel request, a
        retryable disposition (an accepted remote failure, or an attempt that provably never ran), a due
        ``retry_not_before`` on the injected clock, and counted attempts below ``max_attempts``."""
        with self.jobs.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            run = self._controller(db, controller_lease)
            job = db.execute('SELECT * FROM jobs WHERE id=? AND graph_run_id=?', (job_id, run['id'])).fetchone()
            if job is None:
                raise ValueError('current graph job required')
            node = db.execute('SELECT status,retry_not_before FROM nodes WHERE run_id=? AND node_id=?',
                              (run['id'], job['graph_node_id'])).fetchone()
            disposition = db.execute('SELECT disposition,counted FROM attempt_dispositions WHERE attempt_id=?',
                                     (job['active_attempt_id'],)).fetchone()
            plan = GraphPlan.model_validate_json(run['plan'])
            if (run['status'] != 'RUNNING' or job['status'] != 'FAILED' or job['cancel_requested']
                    or disposition is None or not (disposition['disposition'] == 'remote_failed'
                                                   or not disposition['counted'])
                    or node['status'] != 'RUNNING' or node['retry_not_before'] is None
                    or node['retry_not_before'] > self.clock() or self._locked(db, job_id)
                    or self._counted_attempts(db, job_id) >= plan.graph.retry_policy.max_attempts):
                return False
            now = time.time()
            # The finished attempt is over: like reset_for_resume, its lease no longer validates.
            db.execute("UPDATE jobs SET status='QUEUED',error=NULL,active_attempt_id=NULL,updated=? WHERE id=?",
                       (now, job_id))
            db.execute('INSERT INTO events VALUES (?,?,?,?)', (job_id, 'QUEUED', now, 'graph retry'))
            db.execute("UPDATE nodes SET status='READY',error=NULL,retry_not_before=NULL,updated=? "
                       "WHERE run_id=? AND node_id=?", (self.clock(), run['id'], job['graph_node_id']))
            return True

    def _contacted(self, job_id, launch_id) -> bool:
        """Whether the launch's local remote record exists (or cannot be proven absent)."""
        if not launch_id:
            return True
        launch = self.state_dir / 'jobs' / job_id / 'launches' / launch_id
        try:
            return (launch.is_symlink() or os.path.lexists(launch / REMOTE_ATTEMPT)
                    or os.path.lexists(launch / (REMOTE_ATTEMPT + '.tmp')))
        except OSError:
            return True

    def release_uncontacted(self, controller_lease, job_id) -> bool:
        """Release a provably uncontacted attempt (D9), in one transaction.

        Only a non-legacy job of this run that is still PROVISIONING after ``begin_launch``. Its actor token is
        rotated first (a takeover), so the old actor's ``transition(RUNNING)`` now fails, and the executor writes
        ``remote-attempt.json`` only after that transition (plan F8): checking the attempt's own persisted launch
        directory after the rotation is therefore stable. Without the record the attempt fails, its lock and
        reservation are released and it is uncounted and retryable at once. Otherwise only the rotation remains.
        """
        return self._settle_started(controller_lease, job_id, fail_contacted=False) == 'released'

    def settle_stale_launch(self, controller_lease, job_id) -> Literal['released', 'uncertain', 'unchanged']:
        """The controller's settlement of a stale attempt still PROVISIONING after ``begin_launch``, in one
        transaction after the token rotation (8.5 review Minor 3): ``released`` as ``release_uncontacted``;
        ``uncertain`` when its remote record exists (or cannot be proven absent): FAILED with the lock kept, for
        reconcile; ``unchanged`` when it is no longer such an attempt (its actor passed RUNNING meanwhile), which is
        left alone and untouched."""
        return self._settle_started(controller_lease, job_id, fail_contacted=True)

    def _settle_started(self, controller_lease, job_id, *, fail_contacted):
        with self.jobs.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            run = self._controller(db, controller_lease)
            row = self._current_attempt(db, run['id'], job_id)
            if row['status'] != 'PROVISIONING' or not row['launch_started'] or row['legacy']:
                return 'unchanged'
            db.execute('UPDATE job_attempts SET lease_token=? WHERE attempt_id=?',
                       (uuid.uuid4().hex, row['attempt_id']))
            now = time.time()
            if self._contacted(job_id, row['launch_id']):
                if not fail_contacted:
                    return 'contacted'
                db.execute("UPDATE jobs SET status='FAILED',error=?,updated=? WHERE id=?", (STALE_HEARTBEAT, now, job_id))
                db.execute('INSERT INTO events VALUES (?,?,?,?)', (job_id, 'FAILED', now, STALE_HEARTBEAT))
                return 'uncertain'
            db.execute("UPDATE jobs SET status='FAILED',error=?,updated=? WHERE id=?",
                       (RELEASED_BEFORE_CONTACT, now, job_id))
            db.execute('INSERT INTO events VALUES (?,?,?,?)', (job_id, 'FAILED', now, RELEASED_BEFORE_CONTACT))
            self.jobs._release(db, AttemptLease(job_id, row['attempt_id'], row['lease_token'], row['resource_id']))
            db.execute("UPDATE budget_reservations SET status='VOID' WHERE attempt_id=?", (row['attempt_id'],))
            self._dispose(db, run['id'], row, row['attempt_id'], 'released_before_contact', 0, RELEASED_BEFORE_CONTACT)
            self._settle_node(db, run, row, 'released_before_contact', 0, RELEASED_BEFORE_CONTACT)
            return 'released'

    def recover_stale(self, controller_lease, *, stale_before: float) -> tuple[str, ...]:
        """The run-scoped ``JobStore.recover``: RUNNING/EVALUATING attempts of this run whose heartbeat is older
        than ``stale_before`` (wall clock) become FAILED and keep their lock for reconciliation. PROVISIONING
        attempts are left to ``release_uncontacted`` (started) or a takeover and ``return_unlaunched``
        (unstarted); legacy attempts and other runs are never touched."""
        if (isinstance(stale_before, bool) or not isinstance(stale_before, (int, float))
                or not math.isfinite(stale_before)):
            raise ValueError('stale_before must be a finite wall-clock time')
        with self.jobs.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            run = self._controller(db, controller_lease)
            rows = db.execute("SELECT j.id FROM jobs j JOIN job_attempts a ON j.active_attempt_id=a.attempt_id "
                              "WHERE j.graph_run_id=? AND j.status IN ('RUNNING','EVALUATING') AND a.legacy=0 "
                              "AND j.updated<? ORDER BY j.id", (run['id'], stale_before)).fetchall()
            now = time.time()
            for row in rows:
                db.execute("UPDATE jobs SET status='FAILED',error=?,updated=? WHERE id=?", (STALE_HEARTBEAT, now, row['id']))
                db.execute('INSERT INTO events VALUES (?,?,?,?)', (row['id'], 'FAILED', now, STALE_HEARTBEAT))
            return tuple(row['id'] for row in rows)

    def finalize_run(self, controller_lease) -> str | None:
        """The run's final status once every node is finished and no job of the run holds a lock: COMPLETED when
        every node completed, FAILED when any failed or is blocked, otherwise CANCELED. A CANCELED run stays
        CANCELED. Tracking never holds it back (D5). None while the run is not final."""
        with self.jobs.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            run = self._controller(db, controller_lease)
            if run['status'] in {'COMPLETED', 'FAILED'}:
                self._summarize(db, run, run['status'])
                return run['status']
            self._settle_canceled(db, run)
            statuses = [row['status'] for row in db.execute('SELECT status FROM nodes WHERE run_id=?', (run['id'],))]
            if any(status not in NODE_FINISHED for status in statuses) or db.execute(
                    'SELECT 1 FROM gpu_locks g JOIN jobs j ON g.job_id=j.id WHERE j.graph_run_id=?',
                    (run['id'],)).fetchone():
                return None
            if run['status'] == 'CANCELED':
                self._summarize(db, run, 'CANCELED')
                return 'CANCELED'
            final = ('FAILED' if any(status in {'FAILED', 'BLOCKED'} for status in statuses)
                     else 'CANCELED' if 'CANCELED' in statuses else 'COMPLETED')
            db.execute('UPDATE graph_runs SET status=? WHERE id=?', (final, run['id']))
            self._summarize(db, run, final)
            return final

    def settle_canceled_jobs(self, controller_lease) -> tuple[str, ...]:
        """Nodes whose job was canceled while it had no attempt (``JobStore.cancel`` of a QUEUED job: freshly
        prepared, requeued for a retry, or returned unlaunched) become CANCELED, and their subtree is BLOCKED unless
        the run itself is canceled (8.2 review I-2). Returns those node IDs. ``finalize_run`` and the gated
        ``prepare_ready`` apply it too."""
        with self.jobs.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            return self._settle_canceled(db, self._controller(db, controller_lease))

    def _settle_canceled(self, db, run):
        rows = db.execute("SELECT n.node_id FROM nodes n JOIN jobs j ON n.job_id=j.id WHERE n.run_id=? "
                          "AND n.status IN ('READY','RUNNING') AND j.status='CANCELED' AND j.active_attempt_id IS NULL "
                          "ORDER BY n.node_id", (run['id'],)).fetchall()
        plan = GraphPlan.model_validate_json(run['plan']) if rows else None
        for row in rows:
            db.execute("UPDATE nodes SET status='CANCELED',retry_not_before=NULL,updated=? WHERE run_id=? AND node_id=?",
                       (self.clock(), run['id'], row['node_id']))
            if run['status'] != 'CANCELED':   # as a job-level cancel of a returned attempt (8.1 review I1)
                self._block_descendants(db, run['id'], plan, row['node_id'])
        return tuple(row['node_id'] for row in rows)

    # -- run state ----------------------------------------------------------------------------------

    def _state(self, run_id, status):
        """RUNNING <-> PAUSED, and either -> CANCELED. Repeating the current state is a no-op (cancel re-applies
        its job/node updates). COMPLETED and FAILED are final."""
        with self.jobs.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT status FROM graph_runs WHERE id=?', (run_id,)).fetchone()
            if row is None:
                raise ValueError('unknown graph run')
            current = row['status']
            if status == 'PAUSED' and current not in {'RUNNING', 'PAUSED'}:
                raise ValueError('only a running graph can pause')
            if status == 'RUNNING' and current == 'CANCELED':
                raise ValueError('canceled graph cannot resume')
            if status == 'RUNNING' and current not in {'PAUSED', 'RUNNING'}:
                raise ValueError('only a paused graph can resume')
            if status == 'CANCELED' and current in {'COMPLETED', 'FAILED'}:
                raise ValueError('a finished graph cannot be canceled')
            db.execute('UPDATE graph_runs SET status=? WHERE id=?', (status, run_id))
            if status == 'CANCELED':
                db.execute("UPDATE jobs SET cancel_requested=1 WHERE graph_run_id=? AND status IN ('PROVISIONING','RUNNING','EVALUATING')", (run_id,))
                db.execute("UPDATE jobs SET status='CANCELED' WHERE graph_run_id=? AND status IN ('PENDING','QUEUED','RETRYING')", (run_id,))
                db.execute("UPDATE nodes SET status='CANCELED' WHERE run_id=? AND status IN ('PENDING','READY')", (run_id,))
                # A node waiting out its retry backoff never runs again (Task 8.2).
                db.execute("UPDATE nodes SET status='CANCELED',retry_not_before=NULL WHERE run_id=? AND status='RUNNING' "
                           "AND retry_not_before IS NOT NULL", (run_id,))

    def pause(self, run_id):
        self._state(run_id, 'PAUSED')

    def resume(self, run_id):
        self._state(run_id, 'RUNNING')

    def cancel(self, run_id):
        self._state(run_id, 'CANCELED')


class LedgerBindingResolver:
    """The trusted resolver: VerifiedBindings rebuilt only from this store's ledger, verified artifacts and the plan.

    Install it as ``store.binding_resolver`` on the controller's trusted path (8.5; tests directly). Producer
    evidence comes from ``producer_evidence`` (acknowledged attempts only), evaluations carry their ledger evidence,
    persisted inspected targets are re-verified on every read, and an unprepared node's destination is chosen by D1:
    the approved alternative ordered by (blocked now, this run's unfinished jobs on the same exclusivity key, index).
    """

    def __init__(self, store: GraphStore):
        if not isinstance(store, GraphStore):
            raise TypeError('the ledger binding resolver requires a GraphStore')
        self.store = store

    def __call__(self, plan, node_id, ledger, *, run_id=None) -> VerifiedBindings:
        if run_id is None:
            raise ValueError('the ledger binding resolver requires the run ID (GraphStore.bindings_for passes it)')
        store = self.store
        run = store.get_run(run_id)
        if run['plan'].sha256 != plan.sha256:
            raise ValueError('resolver plan differs from the run')
        entry = next((item for item in plan.nodes if item.node.id == node_id), None)
        if entry is None:
            raise ValueError('unknown graph node')
        kinds = {item.node.id: item.node.kind for item in plan.nodes}
        lineage = _ancestors(plan, node_id)   # only the node's own lineage is re-verified (8.5 review Minor 1)
        outputs, evaluations, completed = [], [], []
        producers = [item.producer for item in plan.execution_choices.artifacts]
        targets = {canonical(item.model_dump(mode='json')): item for item in plan.execution_choices.base_targets}
        for evidence, saved in ledger:
            if evidence.status != 'completed':
                continue
            completed.append(evidence)
            if evidence.node_id not in lineage:
                continue   # another branch: a damaged record there never holds this node
            if saved.model is not None:
                outputs.append(saved.model)
                if kinds[evidence.node_id] in PRODUCERS and evidence.tracking_acknowledged:
                    lease = store.jobs.current_lease(evidence.job_id)
                    producers.append(store.producer_evidence(lease, saved.model.model))
            if saved.evaluation is not None:
                evaluations.append(NodeEvaluationBinding(
                    node_id=evidence.node_id, evaluation=saved.evaluation.evaluation,
                    tracking_acknowledged=evidence.tracking_acknowledged, producer=evidence))
            for item in saved.inspected_targets:   # re-checked against the verified full-model inventory
                verified = verify_inspected_targets(item, store.artifacts.verify(item.base), item.base)
                targets.setdefault(canonical(verified.model_dump(mode='json')), verified)
        statuses = {row['node_id']: row['status'] for row in run['nodes']}
        return VerifiedBindings(
            registry=store.registry, artifact_store=store.artifacts, destination_profile=self._destination(run, entry),
            outputs=tuple(outputs), producers=tuple(producers), evaluations=tuple(evaluations),
            failed_parent_node_ids=_failed_evaluations(plan, entry.node, statuses), base_targets=tuple(targets.values()),
            tracking_destination=plan.execution_choices.tracking_destination, completed_nodes=tuple(completed))

    def _destination(self, run, entry):
        """The pinned executor of a prepared node, else the D1 choice among its approved alternatives."""
        from pydantic import TypeAdapter

        from .executor_profiles import ResourceObservation
        if not entry.allowed_resources:
            return None
        store = self.store
        row = next(item for item in run['nodes'] if item['node_id'] == entry.node.id)
        if row['job_id']:
            resources = store.jobs.get(row['job_id'])['manifest']['operation']['resources']
            return OperationResources.model_validate(resources).executor
        adapter, load = TypeAdapter(ResourceObservation), {}
        prefix = 'simulation:' if run['plan'].simulation else ''
        with store.jobs.connection() as db:
            for job in db.execute("SELECT manifest FROM jobs WHERE graph_run_id=? AND status NOT IN "
                                  "('COMPLETED','FAILED','CANCELED')", (run['id'],)):
                operation = json.loads(job['manifest'])['operation']
                if operation.get('kind') != 'select':
                    key = adapter.validate_python(operation['resources']['facts']).exclusivity_key
                    load[key] = load.get(key, 0) + 1

            def rank(item):
                index, resources = item
                key = resources.facts.exclusivity_key   # aliases of one Pod or server share a key: load and lock
                return store.jobs._resource_blocked(db, prefix + key, resources.facts), load.get(key, 0), index

            return min(enumerate(entry.allowed_resources), key=rank)[1].executor
