import json
import socket
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace

import pytest

from tuning_factory.jobs import JobStore


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """The graph ledger never opens a connection: every test here runs on local SQLite and files only."""
    def refuse(*args, **kwargs):
        raise AssertionError('graph ledger tests must not open a network connection')
    monkeypatch.setattr(socket.socket, 'connect', refuse)


class FakeClock:
    """Wall-clock compatible: it starts at time.time(), because attempt heartbeats are written in wall time."""
    def __init__(self, start=None):
        self.now = time.time() if start is None else start

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def queued(store, key, count=1, priority=0):
    job = store.submit({'sha256': key, 'simulation': False,
                        'config': {'resource_profile': {'gpu_count': count}}}, key, priority)
    store.transition(job['id'], 'QUEUED')
    return job


def test_fit_before_priority_and_fenced_resume(tmp_path):
    store = JobStore(tmp_path / 'jobs.db')
    queued(store, 'large', 4, 100)
    small = queued(store, 'small', 1)
    claimed = store.claim('existing_runpod:pod1', max_gpus=1)
    assert claimed is not None and claimed['id'] == small['id']
    lease = claimed['lease']
    store.transition(lease, 'FAILED')
    store.release_gpu(lease, execution_stopped=True)
    store.reset_for_resume(small['id'])
    current = store.claim('existing_runpod:pod1', max_gpus=1)
    for mutate in (lambda: store.heartbeat(lease), lambda: store.transition(lease, 'FAILED'),
                   lambda: store.release_gpu(lease, execution_stopped=True),
                   lambda: store.set_run_id(lease, 'stale'),
                   lambda: store.reconcile_result(lease, 'COMPLETED')):
        with pytest.raises(ValueError, match='lease'):
            mutate()
    assert store.gpu_locked('existing_runpod:pod1')
    assert current['launch_id'] != claimed['launch_id']


def test_actor_takeover_keeps_launch_and_rejects_old_owner(tmp_path):
    store = JobStore(tmp_path / 'jobs.db')
    queued(store, 'one')
    first = store.claim('existing_runpod:pod1')
    store.begin_launch(first['lease'])
    current = store.takeover(first['lease'])
    assert current['launch_id'] == first['launch_id']
    assert current['lease'].attempt_id == first['lease'].attempt_id
    with pytest.raises(ValueError, match='lease'):
        store.heartbeat(first['lease'])
    with pytest.raises(ValueError, match='launch'):
        store.begin_launch(current['lease'])


def test_graph_atomic_submit_prepare_claim_and_crash_repair(tmp_path, monkeypatch):
    from test_graph_planner import fixture

    from tuning_factory.graph_planner import ApprovalGrant, plan_graph
    from tuning_factory.graph_store import GraphStore

    registry, project, choices, facts = fixture(tmp_path)
    plan = plan_graph(project, choices, (facts,))
    store = GraphStore(tmp_path / 'jobs.db', registry=registry)
    with ThreadPoolExecutor(max_workers=4) as pool:
        runs = list(pool.map(lambda _: store.submit(plan, 'same', ApprovalGrant.for_plan(plan)), range(4)))
    assert len({run['id'] for run in runs}) == 1
    lease = store.acquire_controller(runs[0]['id'])
    def crash(*args):
        raise OSError('simulated evidence crash')
    monkeypatch.setattr(store, 'materialize_job_evidence', crash)
    with pytest.raises(OSError):
        store.prepare_ready(lease)
    jobs = store.jobs.list()
    assert len(jobs) == 1 and jobs[0]['graph_run_id'] == runs[0]['id']
    monkeypatch.undo()
    store.materialize_job_evidence(jobs[0]['id'])
    assert (tmp_path / 'jobs' / jobs[0]['id'] / 'manifest.json').is_file()
    assert store.jobs.claim('legacy-worker') is None
    with ThreadPoolExecutor(max_workers=4) as pool:
        claims = list(pool.map(lambda _: store.claim_ready(facts, lease), range(4)))
    assert sum(item is not None for item in claims) == 1
    assert len(store.jobs.list()) == 1


def test_unknown_legacy_lock_blocks_canonical_pod(tmp_path):
    import sqlite3
    path = tmp_path / 'jobs.db'
    with sqlite3.connect(path) as db:
        db.executescript('''CREATE TABLE jobs (id TEXT PRIMARY KEY, request_key TEXT UNIQUE NOT NULL,
          manifest TEXT NOT NULL, status TEXT NOT NULL, priority INTEGER NOT NULL,
          attempts INTEGER NOT NULL DEFAULT 0, cancel_requested INTEGER NOT NULL DEFAULT 0,
          error TEXT, run_id TEXT, created REAL NOT NULL, updated REAL NOT NULL);
          CREATE TABLE gpu_locks(gpu TEXT PRIMARY KEY, job_id TEXT UNIQUE NOT NULL);
          INSERT INTO jobs VALUES ('old','old','{"sha256":"old"}','RUNNING',0,1,0,NULL,'run-old',1,1);
          INSERT INTO gpu_locks VALUES ('unknown-profile','old');''')
    store = JobStore(path)
    queued(store, 'new')
    assert store.claim('existing_runpod:pod1', max_concurrent=4) is None
    assert store.get('old')['run_id'] == 'run-old'
    assert store.get('old')['status'] == 'FAILED'
    assert store.gpu_locked('unknown-profile')


def graph_fixture(tmp_path):
    from test_graph_planner import fixture

    from tuning_factory.graph_planner import ApprovalGrant, plan_graph
    from tuning_factory.graph_store import GraphStore
    registry, project, choices, facts = fixture(tmp_path)
    plan = plan_graph(project, choices, (facts,))
    store = GraphStore(tmp_path / 'jobs.db', registry=registry)
    run = store.submit(plan, 'run', ApprovalGrant.for_plan(plan))
    return store, run, facts


def test_controller_expiry_pause_cancel_and_takeover(tmp_path):
    store, run, facts = graph_fixture(tmp_path)
    old = store.acquire_controller(run['id'])
    store.pause(run['id'])
    assert store.prepare_ready(old) == []
    assert store.claim_ready(facts, old) is None
    store.resume(run['id'])
    store.prepare_ready(old)
    claimed = store.claim_ready(facts, old)
    store.jobs.begin_launch(claimed['lease'])
    with store.jobs.connection() as db:
        db.execute('UPDATE controller_leases SET expires=0')
    new = store.acquire_controller(run['id'])
    with pytest.raises(ValueError, match='lease'):
        store.claim_ready(facts, old)
    adopted = store.takeover_attempt(new, claimed['id'])
    assert adopted['launch_id'] == claimed['launch_id']
    with pytest.raises(ValueError, match='lease'):
        store.jobs.heartbeat(claimed['lease'])
    store.cancel(run['id'])
    assert store.jobs.get(claimed['id'])['cancel_requested']
    assert store.jobs.gpu_locked(claimed['lease'].resource_id)
    assert store.claim_ready(facts, new) is None


def test_run_claimed_uses_persisted_job_and_never_reclaims(tmp_path, monkeypatch):
    from test_runner_cli import setup_factory

    from tuning_factory.config import load_config
    from tuning_factory.execution import LocalExecutor
    factory = setup_factory(tmp_path)
    job = factory.submit(load_config('configs/ai/paper_extraction.yaml'))
    claimed = factory.store.claim('mock-cpu')
    def forbidden(*args, **kwargs):
        raise AssertionError('must not claim twice')
    monkeypatch.setattr(factory.store, 'claim', forbidden)
    observed = []
    class Transport(LocalExecutor):
        def run(self, backend, directory, timeout, canceled, checkpoint=None, *, launch_id=None):
            observed.append(launch_id)
            return super().run(backend, directory, timeout, canceled, checkpoint, launch_id=launch_id)
    result = factory.run_claimed(claimed, Transport(), None, False)
    assert result['id'] == job['id'] and result['status'] == 'COMPLETED'
    assert result['attempts'] == 1 and observed == [claimed['launch_id']]
    with pytest.raises(ValueError, match='launch'):
        factory.run_claimed(claimed, Transport(), None, False)


def test_active_mutations_need_lease_even_same_status(tmp_path):
    store = JobStore(tmp_path / 'jobs.db')
    job = queued(store, 'one')
    claimed = store.claim('pod')
    for mutate in (lambda: store.transition(job['id'], 'PROVISIONING'),
                   lambda: store.heartbeat(job['id']),
                   lambda: store.set_run_id(job['id'], 'forged'),
                   lambda: store.release_gpu(job['id'], execution_stopped=True)):
        with pytest.raises(ValueError, match='lease'):
            mutate()
    with pytest.raises(ValueError, match='termination'):
        store.release_gpu(claimed['lease'], execution_stopped=True)


def test_outputs_are_typed_fenced_and_require_operation_receipt(tmp_path):
    from tuning_factory.graph_store import NodeOutputs
    store, run, facts = graph_fixture(tmp_path)
    ctl = store.acquire_controller(run['id'])
    store.prepare_ready(ctl)
    claimed = store.claim_ready(facts, ctl)
    with pytest.raises(TypeError, match='typed'):
        store.record_outputs(claimed['lease'], {'tracking_acknowledged': True})
    with pytest.raises(ValueError, match='model'):
        store.record_outputs(claimed['lease'], NodeOutputs())


def test_retry_queue_cannot_be_claimed_while_previous_lock_is_held(tmp_path):
    store = JobStore(tmp_path / 'jobs.db')
    queued(store, 'one')
    first = store.claim('pod1')
    store.transition(first['lease'], 'RETRYING')
    store.transition(first['lease'], 'QUEUED')
    assert store.claim('pod2') is None
    store.release_gpu(first['lease'], execution_stopped=True)
    assert store.claim('pod2')


def test_graph_run_claimed_does_not_double_claim_or_launch_v1(tmp_path, monkeypatch):
    from tuning_factory.runner import Factory
    store, run, facts = graph_fixture(tmp_path)
    ctl = store.acquire_controller(run['id'])
    store.prepare_ready(ctl)
    claimed = store.claim_ready(facts, ctl)
    factory = Factory(tmp_path)
    def forbidden(*args, **kwargs):
        pytest.fail('already claimed graph job cannot be claimed again')
    monkeypatch.setattr(factory.store, 'claim', forbidden)
    with pytest.raises(NotImplementedError, match='operation worker'):
        factory.run_claimed(claimed)
    with factory.store.connection() as db:
        row = factory.store._validate_lease(db, claimed['lease'])
        assert row['launch_started'] == 0
    assert factory.store.get(claimed['id'])['attempts'] == 1


def test_cli_release_requires_exact_current_attempt(tmp_path):
    from typer.testing import CliRunner

    from tuning_factory.cli import app
    store = JobStore(tmp_path / 'jobs.db')
    job = queued(store, 'one')
    claimed = store.claim('existing_runpod:pod1')
    store.transition(claimed['lease'], 'FAILED')
    args = ['--state-dir', str(tmp_path), 'release-lock', job['id']]
    cli = CliRunner()
    assert cli.invoke(app, [*args, 'stale', '--confirm-execution-stopped']).exit_code != 0
    assert store.gpu_locked('existing_runpod:pod1')
    assert cli.invoke(app, [*args, claimed['lease'].attempt_id]).exit_code != 0
    result = cli.invoke(app, [*args, claimed['lease'].attempt_id, '--confirm-execution-stopped'])
    assert result.exit_code == 0, result.output
    assert not store.gpu_locked('existing_runpod:pod1')


def test_durable_typed_model_and_tracking_ledger(tmp_path):
    from test_graph_planner import fixture, revise
    from test_model_artifacts import fixture_model

    from tuning_factory.graph_planner import ApprovalGrant, ModelContext, NodeModelBinding, plan_graph
    from tuning_factory.graph_store import GraphStore, NodeOutputs
    directory, descriptor = fixture_model(tmp_path, adapter=True)
    root = descriptor.root.model_copy(update={'repo_id': 'Qwen/Qwen2.5-0.5B-Instruct'})
    descriptor = descriptor.model_copy(update={'root': root, 'effective_base': root})
    import json
    adapter_path = directory / 'adapter_config.json'
    adapter = json.loads(adapter_path.read_text())
    adapter['base_model_name_or_path'] = root.repo_id
    adapter_path.write_text(json.dumps(adapter))
    registry, project, choices, facts = fixture(tmp_path)
    graph = project.spec.graph.copy()
    graph['nodes'] = [dict(graph['nodes'][0])]
    graph['nodes'][0]['model_input'] = {'policy': {'base': descriptor.root.model_dump(mode='json')},
                                      'initialization': 'new_adapter'}
    project = revise(project, graph=graph)
    choices = choices.model_copy(update={'models': (ModelContext(root=descriptor.root),)})
    plan = plan_graph(project, choices, (facts,))
    store = GraphStore(tmp_path / 'jobs.db', registry=registry)
    run = store.submit(plan, 'receipt-run', ApprovalGrant.for_plan(plan))
    ctl = store.acquire_controller(run['id'])
    store.prepare_ready(ctl)
    job = store.claim_ready(facts, ctl)
    lease = job['lease']
    descriptor = descriptor.model_copy(update={'producer_job_id': job['id'], 'producer_attempt_id': lease.attempt_id,
        'producer_manifest_sha256': job['manifest']['sha256'], 'simulation': True})
    ref = store.artifacts.freeze(directory, descriptor)
    output = NodeOutputs(model=NodeModelBinding(node_id='train.1', model=ref))
    with pytest.raises(ValueError, match='completed'):
        store.record_outputs(lease, output)
    store.jobs.transition(lease, 'RUNNING')
    store.jobs.transition(lease, 'EVALUATING')
    store.jobs.transition(lease, 'COMPLETED')
    store.record_outputs(lease, output)
    with pytest.raises(ValueError, match='tracking'):
        store.producer_evidence(lease, ref)
    store.acknowledge_tracking(lease)
    reopened = GraphStore(tmp_path / 'jobs.db', registry=registry)
    evidence, saved = reopened.ledger(run['id'])[0]
    assert evidence.tracking_acknowledged and evidence.attempt_id == lease.attempt_id
    assert saved.model == output.model
    producer = reopened.producer_evidence(lease, ref)
    assert producer.job_id == job['id'] and producer.node_id == 'train.1'
    new = store.takeover_attempt(ctl, job['id'])
    with pytest.raises(ValueError, match='lease'):
        reopened.record_outputs(lease, output)
    reopened.record_outputs(new['lease'], output)


# -- Task 8.1: ledger foundations ------------------------------------------------------------------

TRACKING = {'profile_id': 'mlflow', 'revision': '1', 'public_settings_sha256': '2' * 64}


def ledger_plan(tmp_path, pods=('pod1',), *, max_attempts=1, max_concurrent=1, simulation=True):
    """Independent train nodes ``train.<n>``, one per Pod, at $8/h for 1800 s ($4 per attempt)."""
    from test_graph_planner import fixture, revise

    from tuning_factory.graph_planner import NodeResources, TrackingDestination, plan_graph
    tmp_path.mkdir(parents=True, exist_ok=True)
    registry, project, choices, facts = fixture(tmp_path)
    template = dict(project.spec.graph['nodes'][0])
    executors, observations, resources, nodes = [], [], [], []
    for index, pod in enumerate(pods, 1):
        executors.append(choices.executors[0].model_copy(update={
            'profile_id': f'p{index}', 'pod_id': pod, 'transport_settings_sha256': '1' * 64}))
        observations.append(facts.model_copy(update={'pod_id': pod}))
        nodes.append(dict(template, id=f'train.{index}', executor_profile_ids=[f'p{index}']))
        resources.append(NodeResources(node_id=f'train.{index}', timeout_seconds=1800))
    graph = dict(project.spec.graph, nodes=nodes, max_concurrent=max_concurrent,
                 retry_policy={'max_attempts': max_attempts})
    choices = choices.model_copy(update={'executors': tuple(executors), 'node_resources': tuple(resources),
                                         'tracking_destination': TrackingDestination(**TRACKING)})
    plan = plan_graph(revise(project, graph), choices, tuple(observations), simulation=simulation)
    return registry, plan, tuple(observations)


def ledger_world(tmp_path, pods=('pod1',), *, clock=None, runs=1, **options):
    from types import SimpleNamespace

    from tuning_factory.graph_planner import ApprovalGrant
    from tuning_factory.graph_store import GraphStore
    registry, plan, facts = ledger_plan(tmp_path, pods, **options)
    extra = {} if clock is None else {'clock': clock}
    store = GraphStore(tmp_path / 'jobs.db', registry=registry, **extra)
    submitted = [store.submit(plan, f'run-{index}', ApprovalGrant.for_plan(plan)) for index in range(runs)]
    return SimpleNamespace(store=store, plan=plan, facts=facts, runs=submitted, run_id=submitted[0]['id'])


def reservations(store, run_id=None):
    with store.jobs.connection() as db:
        rows = db.execute('SELECT * FROM budget_reservations' + (' WHERE run_id=?' if run_id else '')
                          + ' ORDER BY created,attempt_id', (run_id,) if run_id else ()).fetchall()
    return [dict(row) for row in rows]


def dispositions(store):
    with store.jobs.connection() as db:
        return [dict(row) for row in db.execute('SELECT * FROM attempt_dispositions ORDER BY rowid')]


def node_status(store, run_id, node_id):
    return next(node['status'] for node in store.get_run(run_id)['nodes'] if node['node_id'] == node_id)


def events(store, job_id):
    with store.jobs.connection() as db:
        return [row['status'] for row in db.execute('SELECT status FROM events WHERE job_id=? ORDER BY rowid',
                                                    (job_id,))]


def test_local_selection_lock_never_blocks_gpu_claims(tmp_path):
    from test_gpu_servers import observation
    store = JobStore(tmp_path / 'jobs.db')
    queued(store, 'select')
    assert store.claim('local:run1', max_concurrent=4)['lease'].resource_id == 'local:run1'
    server = observation()
    assert not store.gpu_locked('existing_runpod:pod1')
    assert not store.resource_blocked(server)
    assert not store.foreign_provider_lock('existing_runpod:pod1')
    assert not store.foreign_provider_lock(server.exclusivity_key)
    queued(store, 'train')
    claimed = store.claim('existing_runpod:pod1', max_concurrent=4)
    assert claimed and claimed['lease'].resource_id == 'existing_runpod:pod1'
    assert store.gpu_locked('local:run1')   # a second selection of the same run still waits for the first


def test_simulation_namespace_is_isolated_from_hardware_locks(tmp_path):
    from test_gpu_servers import observation
    server = observation()
    store = JobStore(tmp_path / 'jobs.db')
    for key in ('real', 'simulated', 'second-simulated', 'ssh-simulated'):
        queued(store, key)
    assert store.claim('existing_runpod:pod1', max_concurrent=8)
    assert not store.gpu_locked('simulation:existing_runpod:pod1')
    assert store.claim('simulation:existing_runpod:pod1', max_concurrent=8)
    assert store.claim('simulation:existing_runpod:pod1', max_concurrent=8) is None   # simulations share keys
    assert store.claim('simulation:' + server.exclusivity_key, max_concurrent=8)
    other = JobStore(tmp_path / 'other.db')
    queued(other, 'simulated')
    queued(other, 'real')
    assert other.claim('simulation:' + server.exclusivity_key, max_concurrent=8)
    assert not other.resource_blocked(server)   # a simulated lock on the server never blocks the real one
    assert not other.gpu_locked('existing_runpod:pod1')
    assert not other.foreign_provider_lock('existing_runpod:pod1')
    assert not other.foreign_provider_lock(server.exclusivity_key)
    assert other.claim('existing_runpod:pod1', max_concurrent=8)


def test_unknown_and_cross_provider_locks_still_quarantine(tmp_path):
    from test_gpu_servers import observation
    server = observation()
    for held, blocks_pod, blocks_server in (('legacy-unknown:old', True, True), ('unknown-profile', True, True),
                                            ('existing_runpod:pod1', True, True),
                                            ('existing_runpod:pod2', False, True),
                                            (server.exclusivity_key, True, True), ('mock-cpu', False, False)):
        store = JobStore(tmp_path / (held.replace(':', '_') + '.db'))
        queued(store, 'held')
        assert store.claim(held, max_concurrent=4)
        assert store.gpu_locked('existing_runpod:pod1') is blocks_pod, held
        assert store.resource_blocked(server) is blocks_server, held
        foreign_pod = held not in {'existing_runpod:pod1', 'existing_runpod:pod2', 'mock-cpu'}
        assert store.foreign_provider_lock('existing_runpod:pod1') is foreign_pod, held
        assert store.foreign_provider_lock(server.exclusivity_key) is (
            held not in {server.exclusivity_key, 'mock-cpu'}), held


def test_public_lease_api_matches_private(tmp_path):
    store = JobStore(tmp_path / 'jobs.db')
    queued(store, 'one')
    claimed = store.claim('existing_runpod:pod1')
    lease = claimed['lease']
    assert JobStore._validate_lease is JobStore.validate_lease
    with store.connection() as db:
        public, private = dict(store.validate_lease(db, lease)), dict(store._validate_lease(db, lease))
    assert public == private == store.validated_attempt(lease)
    assert (public['id'], public['launch_id'], public['launch_started'], public['resource_id']) == (
        claimed['id'], claimed['launch_id'], 0, 'existing_runpod:pod1')
    stale = replace(lease, lease_token='0' * 32)
    with pytest.raises(ValueError, match='stale attempt lease'):
        store.validated_attempt(stale)
    with store.connection() as db, pytest.raises(ValueError, match='stale attempt lease'):
        store.validate_lease(db, stale)
    with pytest.raises(ValueError, match='current attempt lease required'):
        store.validated_attempt(claimed['id'])


def test_holds_lock_is_a_public_fenced_query(tmp_path):
    store = JobStore(tmp_path / 'jobs.db')
    queued(store, 'one')
    lease = store.claim('existing_runpod:pod1')['lease']
    assert store.holds_lock(lease) is True
    with pytest.raises(ValueError, match='stale attempt lease'):
        store.holds_lock(replace(lease, lease_token='0' * 32))
    store.transition(lease, 'FAILED')
    assert store.holds_lock(lease) is True   # a failed attempt keeps its lock until execution provably stopped
    store.release_gpu(lease, execution_stopped=True)
    assert store.holds_lock(lease) is False


HEAD_DDL = '''
    CREATE TABLE IF NOT EXISTS jobs (
      id TEXT PRIMARY KEY, request_key TEXT UNIQUE NOT NULL, manifest TEXT NOT NULL,
      status TEXT NOT NULL, priority INTEGER NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
      cancel_requested INTEGER NOT NULL DEFAULT 0, error TEXT, run_id TEXT,
      created REAL NOT NULL, updated REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS gpu_locks (gpu TEXT PRIMARY KEY, job_id TEXT UNIQUE NOT NULL);
    CREATE TABLE IF NOT EXISTS events (
      job_id TEXT NOT NULL, status TEXT NOT NULL, timestamp REAL NOT NULL, detail TEXT);
    CREATE TABLE IF NOT EXISTS job_attempts (
      attempt_id TEXT PRIMARY KEY, job_id TEXT NOT NULL, lease_token TEXT NOT NULL,
      resource_id TEXT NOT NULL, launch_id TEXT UNIQUE, launch_started INTEGER NOT NULL DEFAULT 0,
      legacy INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS resource_leases (
      resource_id TEXT PRIMARY KEY, job_id TEXT UNIQUE NOT NULL, attempt_id TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS job_destinations (
      job_id TEXT PRIMARY KEY, receipt TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS resource_observations (
      attempt_id TEXT PRIMARY KEY, facts TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS preparation_reservations (
      reservation_id TEXT PRIMARY KEY, owner_token TEXT NOT NULL,
      resource_id TEXT UNIQUE NOT NULL, profile TEXT NOT NULL, facts TEXT NOT NULL,
      status TEXT NOT NULL, created REAL NOT NULL);
    ALTER TABLE jobs ADD COLUMN graph_run_id TEXT;
    ALTER TABLE jobs ADD COLUMN graph_node_id TEXT;
    ALTER TABLE jobs ADD COLUMN active_attempt_id TEXT;
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
'''   # jobs.py:43-71 and graph_store.py:53-71 at 088a07d, verbatim (the ALTERs are JobStore's own migration)
HEAD_COLUMNS = {
    'jobs': '*', 'gpu_locks': '*', 'events': '*', 'job_attempts': '*', 'resource_leases': '*',
    'job_destinations': '*', 'resource_observations': '*', 'preparation_reservations': '*', 'graph_runs': '*',
    'nodes': 'run_id,node_id,status,job_id,manifest,error', 'edges': '*', 'node_outputs': '*',
    'controller_leases': 'run_id,lease_token,expires', 'tracking_outbox': '*'}


def head_rows(path):
    with closing(sqlite3.connect(path)) as db:
        return {table: db.execute(f'SELECT {columns} FROM {table} ORDER BY rowid').fetchall()
                for table, columns in HEAD_COLUMNS.items()}


def dump(path):
    with closing(sqlite3.connect(path)) as db:
        return list(db.iterdump())


def test_pre_task8_state_db_migrates_additively(tmp_path):
    from tuning_factory.graph_planner import ApprovalGrant, NodeExecutionEvidence, NodeModelBinding
    from tuning_factory.graph_store import ControllerLease, GraphStore, NodeOutputs
    from tuning_factory.jobs import AttemptLease
    from tuning_factory.model_artifacts import ModelArtifactRef
    from tuning_factory.registry import canonical, digest
    registry, plan, (first, second) = ledger_plan(tmp_path, ('pod1', 'pod2'), max_concurrent=2, simulation=False)

    def manifest(node_id, facts):
        body = {'schema_version': 2, 'operation': {'kind': 'train', 'id': node_id, 'resources': {
            'facts': facts.model_dump(mode='json'), 'timeout_seconds': 1800}}}
        return canonical({**body, 'sha256': digest(canonical(body))}).decode()

    m1, m2 = manifest('train.1', first), manifest('train.2', second)
    binding = NodeModelBinding(node_id='train.1', model=ModelArtifactRef(artifact_id='a' * 64,
                                                                         inventory_sha256='b' * 64))
    payload = canonical({'model': binding.model_dump(mode='json'), 'evaluation': None,
                         'inspected_targets': []}).decode()
    path = tmp_path / 'state' / 'jobs.db'
    path.parent.mkdir()
    expires = time.time() + 3600
    with closing(sqlite3.connect(path)) as db, db:
        db.executescript(HEAD_DDL)
        db.execute('INSERT INTO graph_runs VALUES (?,?,?,?,?,?)', ('run1', 'request', plan.model_dump_json(),
                   ApprovalGrant.for_plan(plan).model_dump_json(), 'RUNNING', 1.0))
        db.executemany('INSERT INTO nodes VALUES (?,?,?,?,?,?)', [
            ('run1', 'train.1', 'COMPLETED', 'job1', m1, None), ('run1', 'train.2', 'RUNNING', 'job2', m2, None)])
        db.executemany('INSERT INTO jobs (id,request_key,manifest,status,priority,attempts,cancel_requested,error,'
                       'run_id,created,updated,graph_run_id,graph_node_id,active_attempt_id) '
                       'VALUES (?,?,?,?,0,1,0,NULL,NULL,1,2,?,?,?)', [
                           ('job1', 'graph:run1:train.1', m1, 'COMPLETED', 'run1', 'train.1', 'att1'),
                           ('job2', 'graph:run1:train.2', m2, 'PROVISIONING', 'run1', 'train.2', 'att2')])
        db.executemany('INSERT INTO job_attempts VALUES (?,?,?,?,?,?,0,?)', [
            ('att1', 'job1', 'token1', 'existing_runpod:pod1', 'launch1', 1, 1.0),
            ('att2', 'job2', 'token2', 'existing_runpod:pod2', 'launch2', 0, 2.0)])
        db.execute("INSERT INTO gpu_locks VALUES ('existing_runpod:pod2','job2')")
        db.execute("INSERT INTO resource_leases VALUES ('existing_runpod:pod2','job2','att2')")
        db.executemany('INSERT INTO events VALUES (?,?,?,?)', [
            ('job1', status, 1.0, None) for status in ('PENDING', 'QUEUED', 'PROVISIONING', 'RUNNING',
                                                       'EVALUATING', 'COMPLETED')])
        db.execute('INSERT INTO node_outputs VALUES (?,?,?,?)', ('run1', 'train.1', 'att1', payload))
        db.execute('INSERT INTO tracking_outbox VALUES (?,?,?,0)', ('att1', 'job1', payload))
        db.execute('INSERT INTO controller_leases VALUES (?,?,?)', ('run1', 'old-token', expires))
    before = head_rows(path)

    store = GraphStore(path, registry=registry)
    with closing(sqlite3.connect(path)) as db:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        columns = {table: {row[1] for row in db.execute(f'PRAGMA table_info({table})')}
                   for table in ('controller_leases', 'nodes')}
    assert {'controller_owner', 'budget_reservations', 'attempt_dispositions'} <= tables
    assert 'owner_token' in columns['controller_leases']
    assert {'retry_not_before', 'wait_reason', 'updated'} <= columns['nodes']
    assert head_rows(path) == before   # existing rows are valid unchanged

    expected = ((NodeExecutionEvidence(node_id='train.1', plan_sha256=plan.sha256, job_id='job1', attempt_id='att1',
                                       manifest_sha256=json.loads(m1)['sha256'], status='completed',
                                       tracking_acknowledged=False), NodeOutputs(model=binding)),)

    def serialized(ledger):
        return canonical([[evidence.model_dump(mode='json'), {
            'model': outputs.model.model_dump(mode='json') if outputs.model else None,
            'evaluation': outputs.evaluation.model_dump(mode='json') if outputs.evaluation else None,
            'inspected_targets': [item.model_dump(mode='json') for item in outputs.inspected_targets]}]
            for evidence, outputs in ledger])
    assert serialized(store.ledger('run1')) == serialized(expected)   # what the pre-Task-8 code computed

    migrated = dump(path)
    reopened = GraphStore(path, registry=registry)
    assert dump(path) == migrated   # a second open changes nothing
    assert serialized(reopened.ledger('run1')) == serialized(expected)

    assert [(row['attempt_id'], row['run_id'], row['node_id'], row['job_id'], row['resource_id'],
             row['max_cost_usd'], row['status']) for row in reservations(store)] == [
        ('att1', 'run1', 'train.1', 'job1', 'existing_runpod:pod1', 4.0, 'RESERVED'),
        ('att2', 'run1', 'train.2', 'job2', 'existing_runpod:pod2', 4.0, 'RESERVED')]
    assert store.reserved_budget('run1') == 8.0
    assert store.jobs.holds_lock(AttemptLease('job2', 'att2', 'token2', 'existing_runpod:pod2'))
    store.heartbeat_controller(ControllerLease('run1', 'old-token'))   # the NULL-owner run lease still validates
    owner = store.acquire_owner('controller-a')
    with pytest.raises(ValueError, match='controller lease already held'):
        store.acquire_controller('run1', owner=owner)   # and is never stolen while it is unexpired


def test_owner_lease_excludes_a_second_controller_and_expires(tmp_path):
    from tuning_factory.graph_store import GraphStore
    clock = FakeClock()
    store = GraphStore(tmp_path / 'jobs.db', clock=clock)
    assert store.owner_state() is None
    acquired = clock.now
    first = store.acquire_owner('controller-a.host:1', ttl_seconds=30)
    with pytest.raises(ValueError, match='controller already running'):
        store.acquire_owner('controller-b', ttl_seconds=30)
    with pytest.raises(ValueError, match='controller already running'):
        GraphStore(tmp_path / 'jobs.db', clock=clock).acquire_owner('controller-a.host:1')
    clock.advance(20)
    store.renew_owner(first, ttl_seconds=30)
    state = store.owner_state()
    assert state == {'owner_id': 'controller-a.host:1', 'acquired': acquired, 'heartbeat': clock.now,
                     'expires': clock.now + 30}
    assert first.lease_token not in json.dumps(state)
    clock.advance(31)
    with pytest.raises(ValueError, match='stale controller owner lease'):
        store.renew_owner(first)   # a lapsed owner must acquire again
    second = store.acquire_owner('controller-b', ttl_seconds=30)
    for call in (lambda: store.renew_owner(first), lambda: store.release_owner(first)):
        with pytest.raises(ValueError, match='stale controller owner lease'):
            call()
    assert store.owner_state()['owner_id'] == 'controller-b'
    store.release_owner(second)
    third = store.acquire_owner('controller-c')
    assert store.owner_state()['owner_id'] == 'controller-c' and third.lease_token != second.lease_token
    for owner_id in ('', '-starts-with-dash', 'has space', 'trailing\n', 'x' * 129, None):
        with pytest.raises(ValueError, match='owner'):
            GraphStore(tmp_path / 'other.db', clock=clock).acquire_owner(owner_id)
    for ttl in (0, -1, float('inf'), float('nan')):
        with pytest.raises(ValueError, match='TTL'):
            GraphStore(tmp_path / 'other.db', clock=clock).acquire_owner('controller-d', ttl_seconds=ttl)


def test_owner_takes_over_run_leases_of_an_expired_owner(tmp_path):
    clock = FakeClock()
    w = ledger_world(tmp_path, clock=clock, runs=2)
    store, facts = w.store, w.facts[0]
    run, other = w.runs[0]['id'], w.runs[1]['id']
    a = store.acquire_owner('controller-a', ttl_seconds=30)
    ctl_a = store.acquire_controller(run, ttl_seconds=300, owner=a)
    legacy = store.acquire_controller(other, ttl_seconds=300)   # the pre-Task-8 API: no owner
    clock.advance(31)
    with pytest.raises(ValueError, match='stale controller owner lease'):
        store.acquire_controller(run, owner=a)
    with pytest.raises(ValueError, match='controller lease already held'):
        store.acquire_controller(run)   # an owner-less caller never steals an owner's unexpired run lease
    b = store.acquire_owner('controller-b', ttl_seconds=600)
    ctl_b = store.acquire_controller(run, ttl_seconds=300, owner=b)   # A's run lease is unexpired but A is fenced
    with pytest.raises(ValueError, match='controller lease already held'):
        store.acquire_controller(run, owner=b)
    store.prepare_ready(ctl_b)
    claimed = store.claim_ready(facts, ctl_b)
    for call in (lambda: store.prepare_ready(ctl_a), lambda: store.claim_ready(facts, ctl_a),
                 lambda: store.takeover_attempt(ctl_a, claimed['id'])):
        with pytest.raises(ValueError, match='stale controller lease'):
            call()
    assert store.takeover_attempt(ctl_b, claimed['id'])['launch_id'] == claimed['launch_id']
    with pytest.raises(ValueError, match='controller lease already held'):
        store.acquire_controller(other, owner=b)   # a NULL-owner unexpired lease is never stolen
    store.heartbeat_controller(legacy)
    clock.advance(301)
    store.renew_owner(b)
    store.acquire_controller(other, owner=b)
    with pytest.raises(ValueError, match='stale controller lease'):
        store.heartbeat_controller(legacy)


def test_run_state_machine(tmp_path):
    w = ledger_world(tmp_path, runs=4)
    store = w.store
    first, paused, completed, failed = (run['id'] for run in w.runs)

    def status(run_id):
        return store.get_run(run_id)['status']

    store.pause(first)
    store.pause(first)   # idempotent
    assert status(first) == 'PAUSED'
    store.resume(first)
    store.resume(first)   # idempotent
    assert status(first) == 'RUNNING'
    store.cancel(first)
    store.cancel(first)   # idempotent
    assert status(first) == 'CANCELED'
    with pytest.raises(ValueError, match='only a running graph can pause'):
        store.pause(first)
    with pytest.raises(ValueError, match='canceled graph cannot resume'):
        store.resume(first)
    assert status(first) == 'CANCELED'
    assert {node['status'] for node in store.get_run(first)['nodes']} == {'CANCELED'}
    store.pause(paused)
    store.cancel(paused)
    assert status(paused) == 'CANCELED'
    with store.jobs.connection() as db:   # 8.2 writes the finished statuses
        db.execute("UPDATE graph_runs SET status='COMPLETED' WHERE id=?", (completed,))
        db.execute("UPDATE graph_runs SET status='FAILED' WHERE id=?", (failed,))
    for run_id, final in ((completed, 'COMPLETED'), (failed, 'FAILED')):
        for call, message in ((store.pause, 'only a running graph can pause'),
                              (store.resume, 'only a paused graph can resume'),
                              (store.cancel, 'a finished graph cannot be canceled')):
            with pytest.raises(ValueError, match=message):
                call(run_id)
            assert status(run_id) == final
        assert {node['status'] for node in store.get_run(run_id)['nodes']} == {'PENDING'}
    with pytest.raises(ValueError, match='unknown graph run'):
        store.pause('missing')


def test_begin_launch_refuses_when_graph_paused_or_canceled(tmp_path):
    from tuning_factory.jobs import GRAPH_LAUNCH_REFUSED
    assert GRAPH_LAUNCH_REFUSED == 'graph run is not running; launch refused'
    w = ledger_world(tmp_path, ('pod1', 'pod2'), runs=2)
    store = w.store
    run, other = w.runs[0]['id'], w.runs[1]['id']
    ctl = store.acquire_controller(run)
    store.prepare_ready(ctl)
    claimed = store.claim_ready(w.facts[0], ctl)
    store.pause(run)
    with pytest.raises(ValueError, match='graph run is not running; launch refused'):
        store.jobs.begin_launch(claimed['lease'])
    assert store.jobs.validated_attempt(claimed['lease'])['launch_started'] == 0
    store.resume(run)
    assert store.jobs.begin_launch(claimed['lease']) == claimed['launch_id']
    ctl_other = store.acquire_controller(other)
    store.prepare_ready(ctl_other)
    canceled = store.claim_ready(w.facts[1], ctl_other)
    store.cancel(other)
    with pytest.raises(ValueError, match='graph run is not running; launch refused'):
        store.jobs.begin_launch(canceled['lease'])
    assert store.jobs.validated_attempt(canceled['lease'])['launch_started'] == 0
    queued(store.jobs, 'legacy')   # a legacy job's launch never consults graph runs
    legacy = store.jobs.claim('existing_runpod:pod9', max_concurrent=8)
    assert store.jobs.begin_launch(legacy['lease']) == legacy['launch_id']


def test_return_unlaunched_requeues_without_counting(tmp_path):
    w = ledger_world(tmp_path, runs=2)
    store, facts = w.store, w.facts[0]
    run, other = w.runs[0]['id'], w.runs[1]['id']
    ctl = store.acquire_controller(run)
    store.prepare_ready(ctl)
    claimed = store.claim_ready(facts, ctl)
    lease = claimed['lease']
    assert store.return_unlaunched(ctl, lease, reason='paused before launch') == 'QUEUED'
    job = store.jobs.get(claimed['id'])
    assert (job['status'], job['active_attempt_id']) == ('QUEUED', None)
    assert events(store, claimed['id'])[-3:] == ['PROVISIONING', 'RETRYING', 'QUEUED']
    assert node_status(store, run, 'train.1') == 'READY'
    assert not store.jobs.gpu_locked(lease.resource_id)
    assert [(row['attempt_id'], row['status']) for row in reservations(store)] == [(lease.attempt_id, 'VOID')]
    assert store.reserved_budget(run) == 0
    assert [(row['attempt_id'], row['job_id'], row['node_id'], row['disposition'], row['counted'], row['detail'])
            for row in dispositions(store)] == [(lease.attempt_id, claimed['id'], 'train.1', 'returned_unlaunched',
                                                 0, 'paused before launch')]
    with pytest.raises(ValueError, match='stale attempt lease'):
        store.jobs.validated_attempt(lease)   # the returned attempt is over: its actor is fenced
    again = store.claim_ready(facts, ctl)   # max_attempts=1, but the returned attempt was not counted
    assert again and again['lease'].attempt_id != lease.attempt_id
    store.pause(run)
    assert store.return_unlaunched(ctl, again['lease'], reason='paused') == 'QUEUED'
    assert node_status(store, run, 'train.1') == 'READY'
    store.resume(run)
    third = store.claim_ready(facts, ctl)
    store.cancel(run)
    assert store.return_unlaunched(ctl, third['lease'], reason='canceled before launch') == 'CANCELED'
    assert store.jobs.get(third['id'])['status'] == 'CANCELED'
    assert events(store, third['id'])[-1] == 'CANCELED'
    assert node_status(store, run, 'train.1') == 'CANCELED'
    assert not store.jobs.gpu_locked(third['lease'].resource_id)
    with pytest.raises(ValueError, match='stale attempt lease'):
        store.jobs.validated_attempt(third['lease'])
    assert {row['status'] for row in reservations(store, run)} == {'VOID'}
    assert [row['counted'] for row in dispositions(store)] == [0, 0, 0]
    ctl_other = store.acquire_controller(other)
    store.prepare_ready(ctl_other)
    started = store.claim_ready(facts, ctl_other)
    with pytest.raises(ValueError, match='current graph attempt required'):
        store.return_unlaunched(ctl, started['lease'], reason='wrong run')   # another run's attempt
    store.jobs.begin_launch(started['lease'])
    with pytest.raises(ValueError, match='unlaunched'):
        store.return_unlaunched(ctl_other, started['lease'], reason='too late')
    assert store.jobs.holds_lock(started['lease']) and len(dispositions(store)) == 3


def test_claim_ready_separates_local_selection_from_gpu_claims(tmp_path):
    from test_graph_planner import evaluation_fixture, revise

    from tuning_factory.benchmarks import EvaluationRecord
    from tuning_factory.graph_planner import (
        ApprovalGrant,
        NodeEvaluationBinding,
        NodeExecutionEvidence,
        TrackingDestination,
        VerifiedBindings,
        plan_graph,
    )
    from tuning_factory.graph_store import GraphStore
    registry, project, choices, facts, artifacts, ref, producer, benchmark = evaluation_fixture(tmp_path)
    executor = choices.executors[0].model_copy(update={'transport_settings_sha256': '1' * 64})
    choices = choices.model_copy(update={'executors': (executor,),
                                         'tracking_destination': TrackingDestination(**TRACKING)})
    project = revise(project, dict(project.spec.graph, max_concurrent=2))
    plan = plan_graph(project, choices, (facts,), simulation=False)
    store = GraphStore(tmp_path / 'jobs.db', registry=registry, artifact_store=artifacts)

    def resolver(plan, node_id, ledger):
        """A trusted Task 8-style resolver that supplies a completed evaluation, so the selection compiles."""
        record = EvaluationRecord(model=ref, benchmark_sha256=benchmark.fingerprint(), metrics={'accuracy': 1.0},
                                  status='completed', synthetic=False, simulation=False,
                                  producer_attempt_id='a' * 32)
        evidence = NodeExecutionEvidence(node_id='eval', plan_sha256=plan.sha256, job_id='j' * 32,
                                         attempt_id='a' * 32, manifest_sha256='d' * 64, status='completed',
                                         tracking_acknowledged=True)
        return VerifiedBindings(registry=registry, artifact_store=artifacts, destination_profile=executor,
                                producers=(producer,), tracking_destination=plan.execution_choices.tracking_destination,
                                evaluations=(NodeEvaluationBinding(node_id='eval', evaluation=record,
                                                                   tracking_acknowledged=True, producer=evidence),))

    store.binding_resolver = resolver
    for request in ('gpu-first', 'select-first'):
        run = store.submit(plan, request, ApprovalGrant.for_plan(plan))
        ctl = store.acquire_controller(run['id'])
        assert len(store.prepare_ready(ctl)) == 2
        with store.jobs.connection() as db:   # the selection outranks the evaluation
            db.execute("UPDATE jobs SET priority=10 WHERE graph_run_id=? AND graph_node_id='select'", (run['id'],))
        if request == 'gpu-first':
            gpu = store.claim_ready(facts, ctl)   # a GPU observation never hands back the local selection
            assert gpu and gpu['manifest']['operation']['kind'] == 'evaluate'
            local = store.claim_ready(None, ctl)
        else:
            local = store.claim_ready(None, ctl)
            gpu = store.claim_ready(facts, ctl)   # a held local selection lock never blocks a GPU claim
            assert gpu and gpu['manifest']['operation']['kind'] == 'evaluate'
        assert local['manifest']['operation']['kind'] == 'select'
        assert local['lease'].resource_id == 'local:' + run['id']
        assert gpu['lease'].resource_id == facts.exclusivity_key
        assert store.claim_ready(None, ctl) is None and store.claim_ready(facts, ctl) is None
        assert sorted(row['max_cost_usd'] for row in reservations(store, run['id'])) == [0.0, 2.0]
        for claimed in (gpu, local):
            assert store.jobs.abandon_unlaunched(claimed['lease'], 'the test frees the locks')


def test_claim_reserves_budget_atomically_and_refuses_over_budget(tmp_path):
    pods = ('pod1', 'pod2', 'pod3', 'pod4')
    w = ledger_world(tmp_path / 'budget', pods, max_concurrent=4)
    store, run = w.store, w.run_id
    assert w.plan.max_estimated_cost_usd == 16
    ctl = store.acquire_controller(run)
    store.prepare_ready(ctl)
    with store.jobs.connection() as db:   # two attempts' worth is already reserved in this run
        db.execute("INSERT INTO budget_reservations VALUES ('phantom',?,'train.1','phantom','simulation:none',8.0,"
                   "'RESERVED',0)", (run,))
    with ThreadPoolExecutor(max_workers=4) as pool:
        claims = list(pool.map(lambda facts: store.claim_ready(facts, ctl), w.facts))
    claimed = [item for item in claims if item is not None]
    assert len(claimed) == 2 and store.reserved_budget(run) == 16
    rows = {row['attempt_id']: row for row in reservations(store, run)}
    for item in claimed:   # one reservation per attempt, inserted with its claim
        row = rows[item['lease'].attempt_id]
        assert (row['job_id'], row['node_id'], row['resource_id'], row['max_cost_usd'], row['status']) == (
            item['id'], item['graph_node_id'], item['lease'].resource_id, 4.0, 'RESERVED')
    with store.jobs.connection() as db:
        db.execute("UPDATE budget_reservations SET status='VOID' WHERE attempt_id='phantom'")
    with ThreadPoolExecutor(max_workers=4) as pool:
        claims = list(pool.map(lambda facts: store.claim_ready(facts, ctl), w.facts))
    assert sum(item is not None for item in claims) == 2 and store.reserved_budget(run) == 16
    assert len(reservations(store, run)) == 5

    w = ledger_world(tmp_path / 'attempts', max_attempts=1)
    store, run, facts = w.store, w.run_id, w.facts[0]
    ctl = store.acquire_controller(run)
    store.prepare_ready(ctl)
    first = store.claim_ready(facts, ctl)
    store.return_unlaunched(ctl, first['lease'], reason='uncounted')
    with ThreadPoolExecutor(max_workers=4) as pool:
        claims = list(pool.map(lambda _: store.claim_ready(facts, ctl), range(4)))
    (second,) = [item for item in claims if item is not None]
    assert store.jobs.abandon_unlaunched(second['lease'], 'refused before launch')   # counted, terminal
    with pytest.raises(ValueError, match='graph-owned'):
        store.jobs.reset_for_resume(second['id'])   # 11.5 R1 m7: never requeued outside the controller
    with store.jobs.connection() as db:   # a requeue by any other path still cannot exceed max_attempts
        db.execute("UPDATE jobs SET status='QUEUED',active_attempt_id=NULL WHERE id=?", (second['id'],))
        db.execute("UPDATE nodes SET status='READY' WHERE job_id=?", (second['id'],))
        # isolate the attempt rule from the budget rule
        db.execute("UPDATE budget_reservations SET status='VOID' WHERE attempt_id=?", (second['lease'].attempt_id,))
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(lambda _: store.claim_ready(facts, ctl), range(4))) == [None] * 4
    assert store.reserved_budget(run) == 0 and store.jobs.get(second['id'])['status'] == 'QUEUED'


def test_simulation_plan_claims_in_simulation_namespace(tmp_path):
    w = ledger_world(tmp_path)
    store = w.store
    ctl = store.acquire_controller(w.run_id)
    store.prepare_ready(ctl)
    claimed = store.claim_ready(w.facts[0], ctl)
    assert claimed['lease'].resource_id == 'simulation:existing_runpod:pod1'
    assert reservations(store)[0]['resource_id'] == 'simulation:existing_runpod:pod1'
    queued(store.jobs, 'legacy')
    legacy = store.jobs.claim('existing_runpod:pod1', max_concurrent=4)   # the real Pod is not held
    assert legacy and legacy['lease'].resource_id == 'existing_runpod:pod1'


# -- 8.1 review fixes (folded into Task 8.2) --------------------------------------------------------

def test_return_unlaunched_honours_a_job_level_cancel(tmp_path):
    """I1: a job cancel recorded on a PROVISIONING graph job must never be requeued into a real launch."""
    from tuning_factory.jobs import GRAPH_LAUNCH_REFUSED
    w = ledger_world(tmp_path)
    store, facts, run = w.store, w.facts[0], w.run_id
    ctl = store.acquire_controller(run)
    store.prepare_ready(ctl)
    claimed = store.claim_ready(facts, ctl)
    lease = claimed['lease']
    store.jobs.cancel(claimed['id'])   # the CLI and desktop cancel accept any job ID
    store.pause(run)
    with pytest.raises(ValueError, match=GRAPH_LAUNCH_REFUSED):
        store.jobs.begin_launch(lease)
    assert store.return_unlaunched(ctl, lease, reason='paused before launch') == 'CANCELED'
    assert store.jobs.get(claimed['id'])['status'] == 'CANCELED' and events(store, claimed['id'])[-1] == 'CANCELED'
    assert node_status(store, run, 'train.1') == 'CANCELED'
    assert not store.jobs.gpu_locked(lease.resource_id) and store.reserved_budget(run) == 0
    assert [(row['disposition'], row['counted']) for row in dispositions(store)] == [('returned_unlaunched', 0)]
    with pytest.raises(ValueError, match='stale attempt lease'):
        store.jobs.validated_attempt(lease)
    store.resume(run)
    assert store.claim_ready(facts, ctl) is None   # never reclaimed, so the engine never runs


def test_begin_launch_refuses_a_canceled_graph_job(tmp_path):
    """I1: the point of no return also honours a job-level cancel of a graph job; legacy jobs are unchanged."""
    from tuning_factory.jobs import GRAPH_CANCEL_REFUSED
    assert GRAPH_CANCEL_REFUSED == 'graph job cancel requested; launch refused'
    w = ledger_world(tmp_path)
    store = w.store
    ctl = store.acquire_controller(w.run_id)
    store.prepare_ready(ctl)
    claimed = store.claim_ready(w.facts[0], ctl)
    store.jobs.cancel(claimed['id'])
    with pytest.raises(ValueError, match=GRAPH_CANCEL_REFUSED):
        store.jobs.begin_launch(claimed['lease'])
    assert store.jobs.validated_attempt(claimed['lease'])['launch_started'] == 0
    assert store.return_unlaunched(ctl, claimed['lease'], reason='job canceled before launch') == 'CANCELED'
    queued(store.jobs, 'legacy')
    legacy = store.jobs.claim('existing_runpod:pod9', max_concurrent=8)
    store.jobs.cancel(legacy['id'])   # a legacy job keeps its pre-Task-8 launch semantics
    assert store.jobs.begin_launch(legacy['lease']) == legacy['launch_id']


@pytest.mark.parametrize('malformed', ['no_resources', 'invalid_facts'])
def test_migration_reserves_an_unpriceable_attempt_without_failing_the_store(tmp_path, malformed):
    """M1: one manifest the backfill cannot price must not stop the state directory from opening; it reserves its
    run's whole approved total (blocking only that run's further GPU claims) and names the reason."""
    from tuning_factory.graph_planner import ApprovalGrant
    from tuning_factory.graph_store import UNPRICED_ATTEMPT, GraphStore
    from tuning_factory.registry import canonical, digest
    registry, plan, (first, second) = ledger_plan(tmp_path, ('pod1', 'pod2'), max_concurrent=2, simulation=False)
    resources = {'facts': first.model_dump(mode='json'), 'timeout_seconds': 1800}
    if malformed == 'invalid_facts':
        resources['facts']['gpu_count'] = 0
    operation = {'kind': 'train', 'id': 'train.1'} | ({} if malformed == 'no_resources' else {'resources': resources})
    good = {'schema_version': 2, 'operation': {'kind': 'train', 'id': 'train.2', 'resources': {
        'facts': second.model_dump(mode='json'), 'timeout_seconds': 1800}}}
    manifests = [canonical({**body, 'sha256': digest(canonical(body))}).decode()
                 for body in ({'schema_version': 2, 'operation': operation}, good)]
    path = tmp_path / 'state' / 'jobs.db'
    path.parent.mkdir()
    approval = ApprovalGrant.for_plan(plan).model_dump_json()
    with closing(sqlite3.connect(path)) as db, db:
        db.executescript(HEAD_DDL)
        for run, request in (('run1', 'request-1'), ('run2', 'request-2')):
            db.execute('INSERT INTO graph_runs VALUES (?,?,?,?,?,?)',
                       (run, request, plan.model_dump_json(), approval, 'RUNNING', 1.0))
        db.executemany('INSERT INTO nodes VALUES (?,?,?,?,?,?)', [
            ('run1', 'train.1', 'RUNNING', 'job1', manifests[0], None), ('run1', 'train.2', 'PENDING', None, None, None),
            ('run2', 'train.2', 'RUNNING', 'job2', manifests[1], None), ('run2', 'train.1', 'PENDING', None, None, None)])
        db.executemany('INSERT INTO jobs (id,request_key,manifest,status,priority,attempts,cancel_requested,error,'
                       'run_id,created,updated,graph_run_id,graph_node_id,active_attempt_id) '
                       'VALUES (?,?,?,?,0,1,0,NULL,NULL,1,2,?,?,?)', [
                           ('job1', 'graph:run1:train.1', manifests[0], 'FAILED', 'run1', 'train.1', 'att1'),
                           ('job2', 'graph:run2:train.2', manifests[1], 'FAILED', 'run2', 'train.2', 'att2')])
        db.executemany('INSERT INTO job_attempts VALUES (?,?,?,?,?,?,0,?)', [
            ('att1', 'job1', 'token1', 'existing_runpod:pod1', 'launch1', 1, 1.0),
            ('att2', 'job2', 'token2', 'existing_runpod:pod2', 'launch2', 1, 2.0)])
    store = GraphStore(path, registry=registry)   # opens: the migration does not roll back
    assert {row['attempt_id']: row['max_cost_usd'] for row in reservations(store)} == {
        'att1': plan.max_estimated_cost_usd, 'att2': 4.0}
    assert node_status(store, 'run1', 'train.1') == 'RUNNING'
    reason = next(node['wait_reason'] for node in store.get_run('run1')['nodes'] if node['node_id'] == 'train.1')
    assert reason == UNPRICED_ATTEMPT
    assert all(node['wait_reason'] is None for node in store.get_run('run2')['nodes'])
    GraphStore(path, registry=registry)   # a second open changes nothing and still succeeds
    assert len(reservations(store)) == 2
    ctl = store.acquire_controller('run1')
    store.prepare_ready(ctl)
    assert store.claim_ready(second, ctl) is None   # run1's budget is held whole: no further GPU claim
    store.pause('run1')
    store.cancel('run1')   # the run can still be paused and canceled
    assert store.get_run('run1')['status'] == 'CANCELED'
    ctl2 = store.acquire_controller('run2')
    store.prepare_ready(ctl2)
    assert store.claim_ready(first, ctl2) is not None   # other runs are unaffected


def unpriced_db(tmp_path, plan_text=None):
    """A pre-Task-8 state directory with one FAILED graph attempt whose manifest cannot be priced."""
    from tuning_factory.graph_planner import ApprovalGrant
    from tuning_factory.registry import canonical, digest
    registry, plan, _ = ledger_plan(tmp_path, simulation=False)
    body = {'schema_version': 2, 'operation': {'kind': 'train', 'id': 'train.1'}}
    manifest = canonical({**body, 'sha256': digest(canonical(body))}).decode()
    path = tmp_path / 'state' / 'jobs.db'
    path.parent.mkdir()
    with closing(sqlite3.connect(path)) as db, db:
        db.executescript(HEAD_DDL)
        db.execute('INSERT INTO graph_runs VALUES (?,?,?,?,?,?)', ('run1', 'request-1', plan_text or plan.model_dump_json(),
                   ApprovalGrant.for_plan(plan).model_dump_json(), 'RUNNING', 1.0))
        db.execute('INSERT INTO nodes VALUES (?,?,?,?,?,?)', ('run1', 'train.1', 'RUNNING', 'job1', manifest, None))
        db.execute('INSERT INTO jobs (id,request_key,manifest,status,priority,attempts,cancel_requested,error,run_id,'
                   'created,updated,graph_run_id,graph_node_id,active_attempt_id) '
                   "VALUES ('job1','graph:run1:train.1',?,'FAILED',0,1,0,'lost',NULL,1,2,'run1','train.1','att1')",
                   (manifest,))
        db.execute("INSERT INTO job_attempts VALUES ('att1','job1','token1','existing_runpod:pod1','launch1',1,0,1.0)")
    return registry, path


def test_migration_reserves_infinity_when_even_the_plan_is_unreadable(tmp_path):
    """8.2 review M-6: the M1 backfill's last resort. The store still opens; that run can claim nothing more."""
    import math

    from tuning_factory.graph_store import UNPRICED_ATTEMPT, GraphStore
    registry, path = unpriced_db(tmp_path, plan_text='{"not": "a plan"}')
    store = GraphStore(path, registry=registry)
    (reservation,) = reservations(store)
    assert math.isinf(reservation['max_cost_usd']) and math.isinf(store.reserved_budget('run1'))
    with store.jobs.connection() as db:
        assert db.execute('SELECT wait_reason FROM nodes').fetchone()[0] == UNPRICED_ATTEMPT
    GraphStore(path, registry=registry)   # opens again, unchanged
    assert len(reservations(store)) == 1


def test_settling_an_unpriced_attempt_clears_its_wait_reason(tmp_path):
    """8.2 review M-6: the unpriced-attempt notice is about that attempt; it goes once the attempt settles."""
    from tuning_factory.graph_store import UNPRICED_ATTEMPT, GraphStore
    registry, path = unpriced_db(tmp_path)
    store = GraphStore(path, registry=registry)

    def reason():
        return next(node['wait_reason'] for node in store.get_run('run1')['nodes'])

    assert reason() == UNPRICED_ATTEMPT
    ctl = store.acquire_controller('run1')
    assert store.classify_attempt(ctl, 'job1', 'failed_before_submit') == 'failed_before_submit'
    assert reason() is None and node_status(store, 'run1', 'train.1') == 'FAILED'


def test_tracking_outbox_rows_are_inserted_by_column_name():
    """8.2 review M-5: positional inserts would break as soon as tracking_outbox gains a column."""
    import inspect

    from tuning_factory import graph_store
    source = inspect.getsource(graph_store)
    assert 'INTO tracking_outbox VALUES' not in source
    assert source.count('INTO tracking_outbox (attempt_id,job_id,payload,acknowledged)') == 2


# -- Task 11.5 review fixes (R1 I-1, m1, m7) ---------------------------------------------------------

def uncertain(store, claimed):
    """The claimed attempt launched and lost its result: FAILED, with its lock kept for reconcile."""
    store.jobs.begin_launch(claimed['lease'])
    store.jobs.transition(claimed['lease'], 'RUNNING')
    store.jobs.transition(claimed['lease'], 'FAILED', 'remote execution uncertain; reconcile the same launch')
    assert store.jobs.gpu_locked(claimed['lease'].resource_id)


@pytest.mark.parametrize('scope', ['run', 'job'])
def test_a_cancel_flags_an_uncertain_attempt_that_keeps_its_lock(tmp_path, scope):
    """R1 I-1: a run cancel flagged only active jobs and ``JobStore.cancel`` ignored a FAILED one, so reconcile of an
    uncertain attempt never learned of the cancel. A FAILED attempt holding its lock is flagged; a final one is not."""
    w = ledger_world(tmp_path, ('pod1', 'pod2'), max_concurrent=2)
    store, run = w.store, w.run_id
    ctl = store.acquire_controller(run)
    store.prepare_ready(ctl)
    held, final = (store.claim_ready(facts, ctl) for facts in w.facts)
    uncertain(store, held)
    uncertain(store, final)
    store.jobs.release_gpu(final['lease'], execution_stopped=True)   # released by the operator: final
    assert store.classify_attempt(ctl, final['id'], 'failed_before_submit') == 'failed_before_submit'
    assert node_status(store, run, 'train.2') == 'FAILED'   # a finished node (re-review M3 flags unfinished ones)
    if scope == 'run':
        store.cancel(run)
    else:
        for claimed in (held, final):
            store.jobs.cancel(claimed['id'])
    assert [store.jobs.get(claimed['id'])['cancel_requested'] for claimed in (held, final)] == [1, 0]
    assert [store.jobs.get(claimed['id'])['status'] for claimed in (held, final)] == ['FAILED', 'FAILED']


def test_an_uncertain_attempt_counts_toward_max_concurrent(tmp_path):
    """R1 m1: an attempt that keeps its lock may still run on its GPU, so it holds one of the run's max_concurrent
    slots until reconcile or the operator releases it."""
    w = ledger_world(tmp_path, ('pod1', 'pod2'), max_concurrent=1)
    store, run = w.store, w.run_id
    ctl = store.acquire_controller(run)
    store.prepare_ready(ctl)
    first = store.claim_ready(w.facts[0], ctl)
    assert store.concurrent_attempts(run) == 1 and store.claim_ready(w.facts[1], ctl) is None
    uncertain(store, first)
    assert store.concurrent_attempts(run) == 1 and store.claim_ready(w.facts[1], ctl) is None
    store.jobs.reconcile_result(first['lease'], 'FAILED', 'late remote failure')   # the result arrived
    assert store.concurrent_attempts(run) == 0
    assert store.claim_ready(w.facts[1], ctl)['graph_node_id'] == 'train.2'


def test_a_graph_job_is_never_requeued_outside_the_controller(tmp_path):
    """R1 m7: ``reset_for_resume`` refuses a graph-owned job (only a KeyError in ``Factory.resume`` stopped it), and
    ``claim_ready`` claims only the job of a READY node."""
    w = ledger_world(tmp_path, max_attempts=3)
    store, run = w.store, w.run_id
    ctl = store.acquire_controller(run)
    store.prepare_ready(ctl)
    claimed = store.claim_ready(w.facts[0], ctl)
    assert store.jobs.abandon_unlaunched(claimed['lease'], 'refused before launch')
    with pytest.raises(ValueError, match='graph-owned.*change an input and plan again'):
        store.jobs.reset_for_resume(claimed['id'])   # as graph_store.PLAN_SUBMITTED: no graph retry action exists
    from tuning_factory.runner import Factory
    with pytest.raises(ValueError, match='graph-owned'):   # `factory resume <job>`, before the v1 manifest is read
        Factory(tmp_path).resume(claimed['id'])
    assert store.jobs.get(claimed['id'])['status'] == 'FAILED'
    with store.jobs.connection() as db:   # a requeue by any other path: the job QUEUED while its node is not READY
        db.execute("UPDATE jobs SET status='QUEUED',active_attempt_id=NULL WHERE id=?", (claimed['id'],))
    assert node_status(store, run, 'train.1') == 'RUNNING'
    assert store.claim_ready(w.facts[0], ctl) is None
    with store.jobs.connection() as db:
        db.execute("UPDATE nodes SET status='READY' WHERE run_id=? AND node_id='train.1'", (run,))
    assert store.claim_ready(w.facts[0], ctl)['id'] == claimed['id']   # only the node status held it back
    legacy = queued(store.jobs, 'legacy')
    store.jobs.cancel(legacy['id'])
    store.jobs.reset_for_resume(legacy['id'])   # a v1 job still resumes
    assert store.jobs.get(legacy['id'])['status'] == 'QUEUED'


def test_an_invalid_completed_request_is_refused_even_after_a_cancel(tmp_path):
    """Final re-review nit: COMPLETED is checked against TRANSITIONS before a cancel rewrites it to CANCELED, so a
    RUNNING attempt (COMPLETED only follows EVALUATING) is still refused, not silently CANCELED."""
    w = ledger_world(tmp_path)
    store = w.store
    ctl = store.acquire_controller(w.run_id)
    store.prepare_ready(ctl)
    claimed = store.claim_ready(w.facts[0], ctl)
    store.jobs.begin_launch(claimed['lease'])
    store.jobs.transition(claimed['lease'], 'RUNNING')
    store.jobs.cancel(claimed['id'])
    with pytest.raises(ValueError, match='invalid state transition to COMPLETED'):
        store.jobs.transition(claimed['lease'], 'COMPLETED')
    assert store.jobs.get(claimed['id'])['status'] == 'RUNNING'
    store.jobs.transition(claimed['lease'], 'EVALUATING')
    assert store.jobs.transition(claimed['lease'], 'COMPLETED') == 'CANCELED'   # the valid request: CANCELED
