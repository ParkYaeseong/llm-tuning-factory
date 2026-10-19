from concurrent.futures import ThreadPoolExecutor

import pytest

from tuning_factory.jobs import JobStore


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
