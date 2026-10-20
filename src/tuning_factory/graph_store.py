"""Atomic graph/job ledger. Binding resolver is a trusted controller hook, not API input.

No runtime, provider or tracking side effects occur in DB transactions. Task 8 may
inject a resolver that reconstructs VerifiedBindings from verified local receipts.

Fencing (Task 8): one controller owner lease per state directory, run leases bound to that owner, and the
attempt leases of ``JobStore``. A claim reserves its attempt's maximum cost in the same transaction. GraphStore's
own rows use the injected wall clock; job rows and events keep JobStore's ``time.time()``.
"""
import json
import math
import re
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass

from .benchmarks import BenchmarkSpec
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
from .graph_schema import graph_edges
from .jobs import AttemptLease, JobStore
from .manifest import resolve_operation
from .model_artifacts import ModelArtifactStore
from .operations import LocalSelectSpec, resolve_local_selection
from .registry import Registry, canonical, digest

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
)
MIGRATION_COLUMNS = (('controller_leases', 'owner_token', 'TEXT'), ('nodes', 'retry_not_before', 'REAL'),
                     ('nodes', 'wait_reason', 'TEXT'), ('nodes', 'updated', 'REAL'))


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
                db.execute("INSERT OR IGNORE INTO budget_reservations VALUES (?,?,?,?,?,?,'RESERVED',?)",
                           (row['attempt_id'], row['graph_run_id'], row['graph_node_id'], row['id'],
                            row['resource_id'], _attempt_cost(json.loads(row['manifest'])['operation']),
                            row['created']))

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
                db.execute('INSERT INTO graph_runs VALUES (?,?,?,?,?,?)',
                           (run_id, request_id, plan.model_dump_json(), approval.model_dump_json(), 'RUNNING', self.clock()))
                db.executemany('INSERT INTO nodes (run_id,node_id,status) VALUES (?,?,?)',
                               [(run_id, entry.node.id, 'PENDING') for entry in plan.nodes])
                db.executemany('INSERT INTO edges VALUES (?,?,?)', [(run_id, a, b) for a, b in graph_edges(plan.graph)])
        return self.get_run(run_id)

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
        if not isinstance(lease, ControllerLease):
            raise ValueError('controller lease required')
        row = db.execute('SELECT r.* FROM graph_runs r JOIN controller_leases c ON r.id=c.run_id '
                         'WHERE c.run_id=? AND c.lease_token=? AND c.expires>?',
                         (lease.run_id, lease.lease_token, self.clock())).fetchone()
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
                    inspected_targets=tuple(ModelTargetEvidence.model_validate(v) for v in payload.get('inspected_targets', [])))))
            return tuple(result)

    def bindings_for(self, run_id: str, node_id: str) -> VerifiedBindings:
        """The trusted resolver hook: the VerifiedBindings that compile and the runner's continuation both use."""
        plan = self.get_run(run_id)['plan']
        entry = next((item for item in plan.nodes if item.node.id == node_id), None)
        if entry is None:
            raise ValueError('unknown graph node')
        ledger = self.ledger(run_id)
        if self.binding_resolver:
            bindings = self.binding_resolver(plan, node_id, ledger)
            if not isinstance(bindings, VerifiedBindings):
                raise TypeError('trusted resolver must return VerifiedBindings')
            return bindings
        # Root-node planning works without a controller. Conditional outputs require
        # the trusted controller to assemble and verify their complete producer lineage.
        return VerifiedBindings(registry=self.registry, artifact_store=self.artifacts,
            destination_profile=entry.allowed_resources[0].executor if entry.allowed_resources else None,
            producers=tuple(item.producer for item in plan.execution_choices.artifacts),
            base_targets=plan.execution_choices.base_targets,
            tracking_destination=plan.execution_choices.tracking_destination,
            completed_nodes=tuple(e for e, _ in ledger if e.status == 'completed'))

    def prepare_ready(self, controller_lease):
        with self.jobs.connection() as db:   # a fenced controller is refused even when nothing is left to prepare
            self._controller(db, controller_lease)
        run = self.get_run(controller_lease.run_id)
        prepared = []
        for entry in run['plan'].nodes:
            # Read immutable files outside the write transaction; recheck ownership on commit.
            node = next(n for n in run['nodes'] if n['node_id'] == entry.node.id)
            if node['job_id']:
                self.materialize_job_evidence(node['job_id'])
                continue
            if node['status'] != 'PENDING':
                continue
            try:
                operation = compile_node(run['plan'], entry.node.id, self.bindings_for(run['id'], entry.node.id))
                if isinstance(operation, LocalSelectSpec):
                    body = {'schema_version': 2, 'operation': operation.model_dump(mode='json')}
                    manifest = {**body, 'sha256': digest(canonical(body))}
                else:
                    manifest = resolve_operation(operation, self.registry)
            except ValueError as exc:
                with self.jobs.connection() as db:
                    db.execute('BEGIN IMMEDIATE')
                    self._controller(db, controller_lease)
                    db.execute('UPDATE nodes SET error=? WHERE run_id=? AND node_id=? AND job_id IS NULL',
                               (str(exc), run['id'], entry.node.id))
                continue
            with self.jobs.connection() as db:
                db.execute('BEGIN IMMEDIATE')
                current = self._controller(db, controller_lease)
                if current['status'] != 'RUNNING':
                    return prepared
                node = db.execute('SELECT * FROM nodes WHERE run_id=? AND node_id=?', (run['id'], entry.node.id)).fetchone()
                if node['job_id']:
                    job_id = node['job_id']
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

    def materialize_job_evidence(self, job_id):
        return self.jobs.materialize_job_evidence(job_id)

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

    def claim_ready(self, resource_facts, controller_lease):
        """Claim one queued job of this run: with ``resource_facts`` an operation that fits them, with None only a
        local selection. The attempt's maximum cost is reserved in the same transaction."""
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
                operation = json.loads(row['manifest'])['operation']
                if (operation['kind'] == 'select') != (resource_facts is None):
                    continue   # a GPU observation never claims the local selection; None claims nothing else
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
                db.execute("INSERT INTO budget_reservations VALUES (?,?,?,?,?,?,'RESERVED',?)",
                           (job['lease'].attempt_id, run['id'], row['graph_node_id'], row['id'], resource, cost,
                            self.clock()))
                db.execute("UPDATE nodes SET status='RUNNING' WHERE run_id=? AND node_id=?", (run['id'], row['graph_node_id']))
                return job
        return None

    def return_unlaunched(self, controller_lease, attempt_lease, *, reason: str) -> str:
        """Undo a claim whose launch never began (``begin_launch`` refused or never called), so no remote process
        can exist. The lock is freed, the reservation voided and the attempt not counted. The job queues again
        (node READY), or is canceled with its run. Returns the job's new status."""
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
            self.jobs._release(db, attempt_lease)
            db.execute("UPDATE budget_reservations SET status='VOID' WHERE attempt_id=?", (attempt_lease.attempt_id,))
            db.execute("INSERT INTO attempt_dispositions VALUES (?,?,?,?,'returned_unlaunched',0,?,?)",
                       (attempt_lease.attempt_id, run['id'], row['graph_node_id'], row['id'], reason, self.clock()))
            now = time.time()
            if run['status'] == 'CANCELED':
                db.execute("UPDATE jobs SET status='CANCELED',updated=? WHERE id=?", (now, row['id']))
                db.execute('INSERT INTO events VALUES (?,?,?,?)', (row['id'], 'CANCELED', now, reason))
                # The attempt stays the job's evidence, but its actor is fenced.
                db.execute('UPDATE job_attempts SET lease_token=? WHERE attempt_id=?',
                           (uuid.uuid4().hex, attempt_lease.attempt_id))
                db.execute("UPDATE nodes SET status='CANCELED' WHERE run_id=? AND node_id=?",
                           (run['id'], row['graph_node_id']))
                return 'CANCELED'
            for status in ('RETRYING', 'QUEUED'):
                db.execute('INSERT INTO events VALUES (?,?,?,?)', (row['id'], status, now, reason))
            # Like reset_for_resume: the returned attempt is over, so its lease no longer validates.
            db.execute("UPDATE jobs SET status='QUEUED',active_attempt_id=NULL,updated=? WHERE id=?", (now, row['id']))
            db.execute("UPDATE nodes SET status='READY' WHERE run_id=? AND node_id=?", (run['id'], row['graph_node_id']))
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

    def record_outputs(self, attempt_lease, outputs):
        if not isinstance(outputs, NodeOutputs):
            raise TypeError('typed runtime NodeOutputs required')
        job = self.jobs.get(attempt_lease.job_id)
        if not job['graph_run_id']:
            raise ValueError('graph output requires graph-owned job')
        operation = job['manifest']['operation']
        if operation['kind'] in {'train', 'merge', 'materialize', 'select'} and outputs.model is None:
            raise ValueError('completed operation requires its model output receipt')
        if operation['kind'] == 'evaluate' and outputs.evaluation is None:
            raise ValueError('completed evaluation requires its evaluation output receipt')
        if outputs.model and operation['kind'] == 'evaluate':
            raise ValueError('evaluation cannot publish a model output')
        if outputs.model:
            model = NodeModelBinding.model_validate(outputs.model.model_dump())
            if model.node_id != job['graph_node_id']:
                raise ValueError('output node mismatch')
            descriptor = self.artifacts.verify(model.model)
            plan = self.get_run(job['graph_run_id'])['plan']
            entry = next(item for item in plan.nodes if item.node.id == job['graph_node_id'])
            if descriptor.root not in entry.permitted_model_roots:
                raise ValueError('output model root differs from planned alternatives')
            if operation['kind'] != 'select' and descriptor.simulation != plan.simulation:
                raise ValueError('output model simulation differs from plan')
            if operation['kind'] == 'select':
                receipt = resolve_local_selection(LocalSelectSpec.model_validate(operation))
                if model.selection != receipt or model.model != receipt.winner:
                    raise ValueError('selection receipt mismatch')
            elif (descriptor.producer_job_id, descriptor.producer_attempt_id, descriptor.producer_manifest_sha256) != (
                    job['id'], attempt_lease.attempt_id, job['manifest']['sha256']):
                raise ValueError('artifact producer differs from attempt ledger')
        if outputs.evaluation:
            evaluation = NodeEvaluationBinding.model_validate(outputs.evaluation.model_dump())
            if (operation['kind'] != 'evaluate' or evaluation.node_id != job['graph_node_id']
                    or evaluation.evaluation.producer_attempt_id != attempt_lease.attempt_id
                    or evaluation.evaluation.model.model_dump(mode='json') != operation['model']
                    # The same identity compile_node's select path and BenchmarkRevision use (plan F11).
                    or evaluation.evaluation.benchmark_sha256
                    != BenchmarkSpec.model_validate(operation['benchmark']).fingerprint()):
                raise ValueError('evaluation receipt differs from attempt ledger')
            if evaluation.producer is not None or evaluation.tracking_acknowledged:
                raise ValueError('completion/tracking evidence must be derived from ledger')
        if outputs.inspected_targets:
            # Task 7 must use a separate inspected-receipt verifier before persisting
            # prerequisite inventory. Training completion alone cannot prove it.
            raise ValueError('inspection receipt verifier required; use trusted binding resolver')
        payload = canonical({'model': outputs.model.model_dump(mode='json') if outputs.model else None,
                             'evaluation': outputs.evaluation.model_dump(mode='json') if outputs.evaluation else None,
                             'inspected_targets': []}).decode()
        with self.jobs.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = self.jobs.validate_lease(db, attempt_lease)
            if row['status'] != 'COMPLETED':
                raise ValueError('outputs require completed current attempt')
            existing = db.execute('SELECT outputs FROM node_outputs WHERE attempt_id=?', (attempt_lease.attempt_id,)).fetchone()
            if existing and existing['outputs'] != payload:
                raise ValueError('immutable output receipt mismatch')
            db.execute('INSERT OR IGNORE INTO node_outputs VALUES (?,?,?,?)',
                       (job['graph_run_id'], job['graph_node_id'], attempt_lease.attempt_id, payload))
            db.execute('INSERT OR IGNORE INTO tracking_outbox VALUES (?,?,?,0)',
                       (attempt_lease.attempt_id, job['id'], payload))
            db.execute("UPDATE nodes SET status='COMPLETED' WHERE run_id=? AND node_id=?", (job['graph_run_id'], job['graph_node_id']))

    def acknowledge_tracking(self, attempt_lease):
        """Trusted tracking adapter calls only after durable tracking receipt succeeds."""
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

    def pause(self, run_id):
        self._state(run_id, 'PAUSED')

    def resume(self, run_id):
        self._state(run_id, 'RUNNING')

    def cancel(self, run_id):
        self._state(run_id, 'CANCELED')
