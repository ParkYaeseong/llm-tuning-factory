"""Task 8.3: the tracking outbox and MLflow repair (plan §4 "8.3", §6; decisions D5, D6, D7, D10, D11).

Main env only, fakes only. Every MLflow interaction goes through ``FakeMlflowClient`` injected into the real
``MLflowTracker``: it keeps state in dictionaries, has deterministic IDs and no delete method. An autouse fixture
makes ``socket.socket.connect`` raise, refuses a real ``MlflowClient`` and isolates the MLflow credential
variables, so the developer's environment never leaks in. No GPU, SSH, network, download or MLflow write.
"""
import importlib
import json
import re
import socket
from pathlib import Path
from types import SimpleNamespace

import pytest
import test_graph_readiness as readiness
import test_operation_runner as runner_tests
from test_graph_readiness import adapter_output, build, complete, failed, finished, node_row, states, train
from test_operation_runner import Crash

from tuning_factory.graph_planner import TrackingDestination
from tuning_factory.registry import canonical, digest

URI = 'https://mlflow.invalid'
EXPERIMENT = 'llm-tuning-factory'
SECRET_VARIABLES = ('MLFLOW_TRACKING_TOKEN', 'MLFLOW_OIDC_TOKEN_URL', 'MLFLOW_OIDC_CLIENT_ID',
                    'MLFLOW_OIDC_CLIENT_SECRET', 'MLFLOW_TRACKING_URI', 'MLFLOW_TRACKING_USERNAME',
                    'MLFLOW_TRACKING_PASSWORD', 'FACTORY_MLFLOW_ARTIFACT_LOCATION', 'FACTORY_OPERATOR')
TOKEN = 'synthetic-tracking-token-5f1c0d'          # never a real credential
OIDC_SECRET = 'synthetic-oidc-client-secret-9a2e'  # never a real credential


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    """No network, no real MLflow client, no OIDC refresher and none of the developer's MLflow settings."""
    def refuse(*args, **kwargs):
        raise AssertionError('tracking outbox tests must not open a network connection')
    monkeypatch.setattr(socket.socket, 'connect', refuse)
    for key in SECRET_VARIABLES:
        monkeypatch.setenv(key, 'x')   # registers the teardown, so a value set by the code under test cannot leak
        monkeypatch.delenv(key)
    monkeypatch.setattr('mlflow.MlflowClient', lambda *a, **k: pytest.fail('a real MLflow client was created'))
    monkeypatch.setattr('tuning_factory.auth.configure_oidc', lambda: None)


def api():
    # A missing implementation is a test failure, rather than a collection error.
    try:
        module = importlib.import_module('tuning_factory.tracking_outbox')
    except ModuleNotFoundError:
        pytest.fail('tuning_factory.tracking_outbox (Task 8.3) is missing')
    for name in ('TrackingEvent', 'ClaimedEvent', 'OutboxRetryPolicy', 'TrackingOutbox', 'TrackingPublisher',
                 'LocalEvidenceSink'):
        if not hasattr(module, name):
            pytest.fail(f'tracking_outbox.{name} (Task 8.3) is missing')
    return module


def tracking():
    module = importlib.import_module('tuning_factory.tracking')
    for name in ('tracking_settings_sha256', 'TRACKING_UNCERTAIN'):
        if not hasattr(module, name):
            pytest.fail(f'tracking.{name} (Task 8.3) is missing')
    return module


# -- the fake MLflow server ----------------------------------------------------------------------

class Paged(list):
    def __init__(self, items, token=None):
        super().__init__(items)
        self.token = token


class FakeMlflowClient:
    """The client subset MLflowTracker uses. ``fail[op]``: exceptions raised before the side effect, one per call.
    ``commit_then_fail[op]``: one exception raised after the side effect (the lost receipt).
    ``search_unavailable``: the keyed searches raise. There is deliberately no delete method."""
    page_size = 2

    def __init__(self, artifact_root='mlflow-artifacts:/1'):
        self.artifact_root = artifact_root
        self.calls, self.experiments, self.runs, self.models, self.versions = [], {}, {}, [], []
        self.fail, self.commit_then_fail, self.search_unavailable, self.ticks = {}, {}, False, 0

    def _call(self, op, *args, **kwargs):
        self.calls.append((op, args, kwargs))
        if self.fail.get(op):
            raise self.fail[op].pop(0)

    def _done(self, op):
        error = self.commit_then_fail.pop(op, None)
        if error is not None:
            raise error

    def _page(self, items, page_token):
        start = int(page_token or 0)
        end = start + self.page_size
        return Paged(items[start:end], str(end) if end < len(items) else None)

    def get_experiment_by_name(self, name):
        self._call('get_experiment_by_name', name)
        return self.experiments.get(name)

    def create_experiment(self, name, artifact_location=None):
        self._call('create_experiment', name, artifact_location=artifact_location)
        experiment = SimpleNamespace(experiment_id=str(len(self.experiments) + 1), name=name,
                                     artifact_location=artifact_location)
        self.experiments[name] = experiment
        return experiment.experiment_id

    def create_run(self, experiment_id, start_time=None, tags=None, run_name=None):
        self._call('create_run', experiment_id, tags=dict(tags or {}), run_name=run_name)
        self.ticks += 1
        run_id = f'run{len(self.runs) + 1:04d}'
        info = SimpleNamespace(run_id=run_id, experiment_id=experiment_id, run_name=run_name, status='RUNNING',
                               start_time=self.ticks * 1000 if start_time is None else start_time,
                               artifact_uri=f'{self.artifact_root}/{run_id}/artifacts')
        self.runs[run_id] = SimpleNamespace(info=info, data=SimpleNamespace(tags=dict(tags or {}), params={},
                                                                            metrics={}), artifacts={}, points=[])
        self._done('create_run')
        return SimpleNamespace(info=info, data=self.runs[run_id].data)

    def get_run(self, run_id):
        self._call('get_run', run_id)
        return self.runs[run_id]

    def search_runs(self, experiment_ids, filter_string='', run_view_type=1, max_results=1000, order_by=None,
                    page_token=None):
        self._call('search_runs', list(experiment_ids), filter_string=filter_string, page_token=page_token)
        if self.search_unavailable:
            raise ConnectionError('search_runs: MLflow is unavailable')
        key, value = re.fullmatch(r"tags\.`([^`]+)` = '([^']*)'", filter_string).groups()
        found = [run for run in self.runs.values()
                 if run.info.experiment_id in experiment_ids and run.data.tags.get(key) == value]
        return self._page(found, page_token)

    def _param(self, run_id, key, value):
        params = self.runs[run_id].data.params
        if key in params and params[key] != str(value):
            raise RuntimeError('INVALID_PARAMETER_VALUE: changing a logged param')
        params[key] = str(value)

    def log_param(self, run_id, key, value):
        self._call('log_param', run_id, key, value)
        self._param(run_id, key, value)

    def log_batch(self, run_id, metrics=(), params=(), tags=()):
        self._call('log_batch', run_id, metrics=[(m.key, m.value, m.timestamp, m.step) for m in metrics],
                   params=[(p.key, p.value) for p in params])
        for param in params:
            self._param(run_id, param.key, param.value)
        for metric in metrics:
            self.runs[run_id].points.append((metric.key, metric.value, metric.timestamp, metric.step))
            self.runs[run_id].data.metrics[metric.key] = metric.value

    def log_artifact(self, run_id, local_path, artifact_path=None):
        self._call('log_artifact', run_id, local_path, artifact_path=artifact_path)
        path = Path(local_path)
        self.runs[run_id].artifacts[(artifact_path + '/' if artifact_path else '') + path.name] = path.read_bytes()

    def log_artifacts(self, run_id, local_dir, artifact_path=None):
        self._call('log_artifacts', run_id, local_dir, artifact_path=artifact_path)
        for file in sorted(Path(local_dir).rglob('*')):
            if file.is_file():
                name = (artifact_path + '/' if artifact_path else '') + file.relative_to(local_dir).as_posix()
                self.runs[run_id].artifacts[name] = file.read_bytes()

    def set_tag(self, run_id, key, value):
        self._call('set_tag', run_id, key, value)
        self.runs[run_id].data.tags[key] = str(value)

    def set_terminated(self, run_id, status=None, end_time=None):
        self._call('set_terminated', run_id, status=status)
        self.runs[run_id].info.status = status or 'FINISHED'

    def search_registered_models(self, filter_string=None, max_results=100, order_by=None, page_token=None):
        self._call('search_registered_models', filter_string=filter_string)
        name = re.fullmatch(r"name = '([^']*)'", filter_string).group(1)
        return Paged([SimpleNamespace(name=item) for item in self.models if item == name])

    def create_registered_model(self, name, tags=None, description=None):
        self._call('create_registered_model', name)
        if name in self.models:
            raise RuntimeError('RESOURCE_ALREADY_EXISTS')
        self.models.append(name)
        self._done('create_registered_model')

    def search_model_versions(self, filter_string=None, max_results=10000, order_by=None, page_token=None):
        self._call('search_model_versions', filter_string=filter_string, page_token=page_token)
        if self.search_unavailable:
            raise ConnectionError('search_model_versions: MLflow is unavailable')
        name, run_id = re.fullmatch(r"name = '([^']*)' and run_id = '([^']*)'", filter_string).groups()
        return self._page([item for item in self.versions if (item.name, item.run_id) == (name, run_id)], page_token)

    def create_model_version(self, name, source, run_id=None, tags=None, run_link=None, description=None,
                             await_creation_for=300, model_id=None):
        self._call('create_model_version', name, source=source, run_id=run_id, tags=dict(tags or {}))
        version = self.seed_version(name, run_id, tags, source=source)
        self._done('create_model_version')
        return version

    def seed_version(self, name, run_id, tags, *, source=None):   # test helper, not a client method
        version = SimpleNamespace(name=name, version=str(1 + sum(item.name == name for item in self.versions)),
                                  source=source or f'runs:/{run_id}/model', run_id=run_id, tags=dict(tags or {}))
        self.versions.append(version)
        return version


def count(fake, op):
    return sum(call[0] == op for call in fake.calls)


def keyed(fake, key):
    return [run for run in fake.runs.values() if run.data.tags.get('factory.tracking_key') == key]


# -- worlds --------------------------------------------------------------------------------------

def destination(auth_mode='none', *, uri=URI, artifact_location=None):
    """The TrackingDestination a real plan pins for these MLflow settings (D11)."""
    return TrackingDestination(profile_id='mlflow', revision='1', public_settings_sha256=tracking(
    ).tracking_settings_sha256({'tracking_uri': uri, 'experiment': EXPERIMENT,
                                'artifact_location': artifact_location, 'auth_mode': auth_mode}))


def chain(r):
    """a -> b: a LoRA training and a continuation of its adapter."""
    return [train(r, 'a'), train(r, 'b', 'a')]


def single(r):
    return [train(r, 'a')]


def tracked(tmp_path, monkeypatch, nodes=chain, *, simulation=False, auth_mode='none', pinned=None, fake=None,
            owner_ttl=10 ** 7, claim_ttl=600):
    """A submitted plan (``test_graph_readiness.build``) pinned to this MLflow destination, plus the real tracker
    on a fake client, the outbox, the local sink and the publisher, on one FakeClock."""
    outbox_api = api()
    monkeypatch.setattr(readiness, 'TRACKING', pinned or destination(auth_mode))
    w = build(tmp_path, nodes, simulation=simulation)
    w.fake = fake or FakeMlflowClient()
    w.tracker = tracking().MLflowTracker(URI, client=w.fake)
    w.owner = w.store.acquire_owner('controller-a', ttl_seconds=owner_ttl)
    w.outbox = outbox_api.TrackingOutbox(w.store, clock=w.clock, claim_ttl_seconds=claim_ttl)
    w.sink = outbox_api.LocalEvidenceSink(w.store.state_dir)
    w.publisher = outbox_api.TrackingPublisher(w.outbox, w.store, w.tracker, local_sink=w.sink)
    return w


def restarted(w, owner_id='controller-b', *, fake=None, owner_ttl=10 ** 7):
    """A new controller process: the old owner lease and its claims expired, then new instances of everything."""
    outbox_api = api()
    w.clock.advance(10 ** 7 + 3600)
    owner = w.store.acquire_owner(owner_id, ttl_seconds=owner_ttl)
    outbox = outbox_api.TrackingOutbox(w.store, clock=w.clock)
    publisher = outbox_api.TrackingPublisher(outbox, w.store, tracking().MLflowTracker(URI, client=fake or w.fake),
                                             local_sink=outbox_api.LocalEvidenceSink(w.store.state_dir))
    return SimpleNamespace(owner=owner, outbox=outbox, publisher=publisher)


def done(w, node_id, **options):
    """Prepare, claim, complete and record the node's attempt; its tracking is left to the outbox."""
    readiness.prepare(w, node_id)
    return complete(w, node_id, ack=False, **options)


def rows(w, kind=None):
    with w.store.jobs.connection() as db:
        query = 'SELECT * FROM tracking_events' + (' WHERE kind=?' if kind else '') + ' ORDER BY rowid'
        return [dict(row) for row in db.execute(query, (kind,) if kind else ())]


def event(w, key):
    found = [row for row in rows(w) if row['idempotency_key'] == key]
    assert len(found) == 1, (key, [row['idempotency_key'] for row in rows(w)])
    return found[0]


def acknowledged(w, attempt_id):
    with w.store.jobs.connection() as db:
        row = db.execute('SELECT acknowledged FROM tracking_outbox WHERE attempt_id=?', (attempt_id,)).fetchone()
    return None if row is None else bool(row['acknowledged'])


def identity(row):
    return json.loads(row['remote_identity']) if row['remote_identity'] else None


def due(w, key):
    """Advance the fake clock to the event's next attempt."""
    w.clock.advance(max(0.0, event(w, key)['next_attempt_at'] - w.clock()))


def registration_key(claimed, binding):
    return f'register:{binding.model.artifact_id}:{binding.model.inventory_sha256}:{claimed["lease"].attempt_id}'


# -- 1-3. schema, idempotency and the shared transaction ------------------------------------------

def test_outbox_schema_migrates_and_backfills_unacknowledged_attempts(tmp_path, monkeypatch):
    outbox_api = api()
    gs = readiness.api()
    w = tracked(tmp_path, monkeypatch, lambda r: [train(r, 'a'), train(r, 'b', 'a'), train(r, 'c')],
                simulation=True)
    readiness.prepare(w, 'c')
    complete(w, 'c')                                  # a pre-8.3 attempt whose tracking was already acknowledged
    claimed, _ = done(w, 'a')                         # and one that was never acknowledged
    path = w.store.jobs.path
    with w.store.jobs.connection() as db:             # the state directory as pre-8.3 code left it
        db.execute('DROP TABLE tracking_events')
    store = gs.GraphStore(path, clock=w.clock)
    with store.jobs.connection() as db:
        columns = [row['name'] for row in db.execute('PRAGMA table_info(tracking_events)')]
        indexes = {row['name'] for row in db.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        assert db.execute('SELECT COUNT(*) FROM tracking_events').fetchone()[0] == 0
    assert columns == ['event_id', 'idempotency_key', 'kind', 'run_id', 'node_id', 'job_id', 'attempt_id',
                       'depends_on', 'payload', 'status', 'tries', 'next_attempt_at', 'claim_token', 'claim_expires',
                       'intent', 'remote_identity', 'last_error', 'created', 'updated']
    assert 'tracking_events_due' in indexes
    outbox = outbox_api.TrackingOutbox(store, clock=w.clock)
    store.release_owner(w.owner)
    owner = store.acquire_owner('controller-b', ttl_seconds=3600)
    with pytest.raises(ValueError, match='controller owner lease'):
        outbox.backfill(w.owner)                      # a released or replaced owner may not backfill
    attempt = claimed['lease'].attempt_id
    assert outbox.backfill(owner) == 4                # graph, node, attempt and evidence of the unacknowledged one
    assert outbox.backfill(owner) == 0                # once
    assert [(row['kind'], row['idempotency_key']) for row in rows(w)] == [
        ('graph_run', f'graph:{w.run_id}'), ('node_run', f'node:{w.run_id}:a'), ('attempt_run', f'attempt:{attempt}'),
        ('attempt_evidence', f'evidence:{attempt}')]
    assert acknowledged(w, attempt) is False          # the ledger gate stays closed until the evidence is saved
    assert states(w)['b'] == ('waiting', 'waiting for tracking of a')
    evidence = {item.attempt_id: item for item, _ in store.ledger(w.run_id)}[attempt]
    assert evidence.tracking_acknowledged is False
    publisher = outbox_api.TrackingPublisher(outbox, store, None, local_sink=outbox_api.LocalEvidenceSink(
        store.state_dir))
    assert publisher.process(owner) == 4
    assert {row['status'] for row in rows(w)} == {'ACKNOWLEDGED'}
    assert acknowledged(w, attempt) is True
    assert states(w)['b'][0] == 'ready'


def test_enqueue_is_idempotent_by_key_and_refuses_payload_drift(tmp_path, monkeypatch):
    outbox_api = api()
    w = tracked(tmp_path, monkeypatch, single, simulation=True)
    key = f'graph-summary:{w.run_id}:COMPLETED'
    item = outbox_api.TrackingEvent(run_id=w.run_id, kind='graph_summary', idempotency_key=key,
                                    depends_on_key=f'graph:{w.run_id}',
                                    payload={'final_status': 'COMPLETED', 'nodes': {'a': 'COMPLETED'}})
    assert item.event_id == digest(canonical({'kind': 'graph_summary', 'key': key}))
    with w.store.jobs.connection() as db:
        db.execute('BEGIN IMMEDIATE')
        assert w.outbox.enqueue(item, db) == item.event_id
        assert w.outbox.enqueue(item, db) == item.event_id   # the same key and payload: nothing new
    assert len([row for row in rows(w) if row['idempotency_key'] == key]) == 1
    drift = item.model_copy(update={'payload': {'final_status': 'COMPLETED', 'nodes': {'a': 'FAILED'}}})
    with pytest.raises(ValueError, match='tracking idempotency key reused with different payload'):
        with w.store.jobs.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            w.outbox.enqueue(drift, db)
    orphan = item.model_copy(update={'idempotency_key': f'graph-summary:{w.run_id}:FAILED',
                                     'depends_on_key': 'graph:missing'})
    with pytest.raises(ValueError, match='tracking parent event is missing'):
        with w.store.jobs.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            w.outbox.enqueue(orphan, db)
    with pytest.raises(ValueError):
        outbox_api.TrackingEvent(run_id=w.run_id, kind='graph_run', idempotency_key="graph:x' or '1'='1",
                                 payload={})
    assert json.loads(event(w, key)['payload']) == item.payload


def test_enqueue_shares_the_record_outputs_transaction(tmp_path, monkeypatch):
    api()
    gs = readiness.api()
    w = tracked(tmp_path, monkeypatch, single)
    readiness.prepare(w, 'a')
    claimed = finished(w, 'a')
    outputs = gs.NodeOutputs(model=adapter_output(w, claimed))
    attempt = claimed['lease'].attempt_id
    original = gs.GraphStore._track

    def crashing(self, db, events):
        original(self, db, events)
        raise Crash('after the enqueue, before the commit')

    monkeypatch.setattr(gs.GraphStore, '_track', crashing)
    with pytest.raises(Crash):
        w.store.record_outputs(claimed['lease'], outputs)
    monkeypatch.setattr(gs.GraphStore, '_track', original)
    with w.store.jobs.connection() as db:
        assert db.execute('SELECT COUNT(*) FROM node_outputs').fetchone()[0] == 0
        assert db.execute('SELECT COUNT(*) FROM tracking_outbox').fetchone()[0] == 0
    assert not [row for row in rows(w) if row['attempt_id'] == attempt]
    assert node_row(w, 'a')['status'] == 'RUNNING'
    w.store.record_outputs(claimed['lease'], outputs)
    w.store.record_outputs(claimed['lease'], outputs)   # idempotent: each event once
    keys = [row['idempotency_key'] for row in rows(w)]
    assert keys == [f'graph:{w.run_id}', f'node:{w.run_id}:a', f'attempt:{attempt}', f'evidence:{attempt}',
                    registration_key(claimed, outputs.model)]
    with w.store.jobs.connection() as db:
        assert db.execute('SELECT COUNT(*) FROM node_outputs').fetchone()[0] == 1


# -- 4-5. hierarchy, lineage and the gate ---------------------------------------------------------

def test_hierarchy_graph_node_attempt_runs_with_lineage_artifacts(tmp_path, monkeypatch):
    outbox_api = api()
    monkeypatch.setattr(runner_tests, 'TRACKING', destination())
    monkeypatch.setenv('FACTORY_OPERATOR', 'test-operator')
    w = runner_tests.world(tmp_path, monkeypatch, ('train', 'eval'))
    assert w.claimed['graph_node_id'] == 'train'
    trained = runner_tests.run(w)
    assert trained.status == 'COMPLETED', trained.error
    w.store.record_outputs(w.lease, trained.outputs)
    second = w.store.claim_ready(w.facts, w.ctl)
    evaluated = runner_tests.run(w, second)
    assert evaluated.status == 'COMPLETED', evaluated.error
    w.store.record_outputs(second['lease'], evaluated.outputs)
    run_id, train_attempt, eval_attempt = w.run['id'], w.lease.attempt_id, second['lease'].attempt_id

    fake = FakeMlflowClient()
    outbox = outbox_api.TrackingOutbox(w.store)
    publisher = outbox_api.TrackingPublisher(outbox, w.store, tracking().MLflowTracker(URI, client=fake),
                                             local_sink=outbox_api.LocalEvidenceSink(w.state))
    owner = w.store.acquire_owner('controller-a')
    assert publisher.process(owner, limit=64) == 8   # graph, 2 nodes, 2 attempts, 2 evidence and 1 registration
    (graph,) = keyed(fake, f'graph:{run_id}')
    nodes = {node: keyed(fake, f'node:{run_id}:{node}')[0] for node in ('train', 'eval')}
    attempts = {'train': keyed(fake, f'attempt:{train_attempt}')[0], 'eval': keyed(fake, f'attempt:{eval_attempt}')[0]}
    assert len(fake.runs) == 5
    for node, node_run in nodes.items():
        assert node_run.data.tags['mlflow.parentRunId'] == graph.info.run_id
        assert attempts[node].data.tags['mlflow.parentRunId'] == node_run.info.run_id
    with w.store.jobs.connection() as db:
        plan_text, approval_text = db.execute('SELECT plan,approval FROM graph_runs WHERE id=?', (run_id,)).fetchone()
    assert graph.artifacts == {'lineage/plan.json': plan_text.encode(), 'lineage/approval.json': approval_text.encode()}

    job = w.store.jobs.get(w.job_id)
    launch = w.state / 'jobs' / w.job_id / 'launches' / w.claimed['launch_id']
    artifacts = attempts['train'].artifacts
    expected = {'manifest.json', 'command.json', 'model-inputs.json', 'result.json', 'remote-result.json',
                'remote-artifacts.json', 'descriptor.json', 'inventory.json', 'node-outputs.json'}
    assert expected <= {name.removeprefix('lineage/') for name in artifacts if name.startswith('lineage/')}
    assert artifacts['lineage/manifest.json'] == (w.state / 'jobs' / w.job_id / 'manifest.json').read_bytes()
    assert artifacts['lineage/command.json'] == (launch / 'command.json').read_bytes()
    assert artifacts['lineage/result.json'] == (launch.parent / f'{launch.name}.result.json').read_bytes()
    model = trained.outputs.model.model
    assert digest(artifacts['lineage/descriptor.json']) == model.artifact_id
    assert digest(artifacts['lineage/inventory.json']) == model.inventory_sha256
    assert not any(name.endswith(('.yaml', '.jsonl')) or 'data' in name.split('/')[-1] for name in artifacts
                   if name.startswith('lineage/'))   # dataset bytes are never uploaded
    record = evaluated.outputs.evaluation.evaluation
    assert json.loads(attempts['eval'].artifacts['lineage/evaluation.json']) == record.model_dump(mode='json')
    assert attempts['eval'].data.metrics == dict(record.metrics)
    created = {row['attempt_id']: row['created'] for row in readiness.table(w, 'job_attempts')}
    assert {point[2:] for point in attempts['eval'].points} == {(int(created[eval_attempt] * 1000), 0)}
    tags = attempts['train'].data.tags
    assert {key: tags[key] for key in ('factory.graph_run_id', 'factory.node_id', 'factory.job_id',
                                       'factory.attempt_id', 'factory.launch_id', 'factory.plan_sha256',
                                       'factory.manifest_sha256', 'factory.simulation', 'factory.workflow_id',
                                       'factory.operator', 'factory.tracking_key')} == {
        'factory.graph_run_id': run_id, 'factory.node_id': 'train', 'factory.job_id': w.job_id,
        'factory.attempt_id': train_attempt, 'factory.launch_id': w.claimed['launch_id'],
        'factory.plan_sha256': w.plan.sha256, 'factory.manifest_sha256': job['manifest']['sha256'],
        'factory.simulation': 'false', 'factory.workflow_id': 'project.1', 'factory.operator': 'test-operator',
        'factory.tracking_key': f'attempt:{train_attempt}'}
    assert attempts['train'].data.params['factory.manifest_sha256'] == job['manifest']['sha256']
    assert {run.info.status for run in attempts.values()} == {'FINISHED'}
    assert acknowledged(w, train_attempt) and acknowledged(w, eval_attempt)

    # D6: the real trained adapter is registered once; the evaluation registers nothing.
    (version,) = fake.versions
    key = f'register:{model.artifact_id}:{model.inventory_sha256}:{train_attempt}'
    assert (version.name, version.run_id, version.source) == (
        'factory-project.1-train', attempts['train'].info.run_id, f'runs:/{attempts["train"].info.run_id}/model')
    assert {name: version.tags[name] for name in ('factory.registration_key', 'quality_qualified', 'approval',
                                                  'factory.producer_attempt_id', 'factory.artifact_id')} == {
        'factory.registration_key': key, 'quality_qualified': 'false', 'approval': 'pending',
        'factory.producer_attempt_id': train_attempt, 'factory.artifact_id': model.artifact_id}
    files = w.store.artifacts.path(model)
    assert {name.removeprefix('model/') for name in artifacts if name.startswith('model/')} == {
        file.relative_to(files).as_posix() for file in files.rglob('*') if file.is_file()}

    # The summary terminates the node runs and the graph run with the final statuses.
    assert {run.info.status for run in [graph, *nodes.values()]} == {'RUNNING'}
    assert w.store.finalize_run(w.ctl) == 'COMPLETED'
    assert publisher.process(owner) == 1
    assert {run.info.status for run in [graph, *nodes.values()]} == {'FINISHED'}
    assert json.loads(graph.artifacts['lineage/summary.json']) == {
        'final_status': 'COMPLETED', 'nodes': {'eval': 'COMPLETED', 'train': 'COMPLETED'}}
    assert {row['status'] for row in rows(w)} == {'ACKNOWLEDGED'}
    assert not [call for call in fake.calls if 'delete' in call[0]]


def test_dependants_wait_for_evidence_ack_while_the_gpu_lock_is_free(tmp_path, monkeypatch):
    w = tracked(tmp_path, monkeypatch)
    claimed, _ = done(w, 'a')
    assert not w.store.jobs.gpu_locked(claimed['lease'].resource_id)   # released at remote completion
    assert states(w)['b'] == ('waiting', 'waiting for tracking of a')
    assert w.store.prepare_ready(w.ctl, node_ids=['b']) == []
    status = w.outbox.status(w.run_id)
    assert node_row(w, 'a')['status'] == 'COMPLETED'
    assert status['nodes']['a']['tracking_status'] == 'pending'       # execution and tracking are separate
    assert status['nodes']['b']['tracking_status'] == 'not_started'
    assert status['events']['PENDING'] == 5
    w.publisher.process(w.owner)
    assert states(w)['b'][0] == 'ready'
    status = w.outbox.status(w.run_id)
    assert (status['nodes']['a']['tracking_status'], status['nodes']['a']['registration_status']) == (
        'saved', 'registered')
    assert status['events'] == {'PENDING': 0, 'ACKNOWLEDGED': 5, 'FAILED': 0, 'UNCERTAIN': 0}


# -- 6-10. adoption after a lost receipt, uncertainty and duplicates -----------------------------

def test_lost_run_receipt_adopts_by_stable_tag(tmp_path, monkeypatch):
    w = tracked(tmp_path, monkeypatch, single, owner_ttl=60)
    done(w, 'a')
    w.fake.commit_then_fail['create_run'] = Crash('the create_run response was lost')
    with pytest.raises(Crash):
        w.publisher.process(w.owner)
    (committed,) = w.fake.runs.values()
    assert json.loads(event(w, f'graph:{w.run_id}')['intent'])['op'] == 'create_run'
    after = restarted(w)
    after.publisher.process(after.owner)
    graph = event(w, f'graph:{w.run_id}')
    assert graph['status'] == 'ACKNOWLEDGED'
    assert identity(graph) == {'run_id': committed.info.run_id, 'adopted': True, 'duplicates': []}
    assert len(keyed(w.fake, f'graph:{w.run_id}')) == 1
    assert [call[2]['tags']['factory.tracking_key'] for call in w.fake.calls if call[0] == 'create_run'].count(
        f'graph:{w.run_id}') == 1
    assert {row['status'] for row in rows(w)} == {'ACKNOWLEDGED'}


def test_lost_registration_receipt_adopts_existing_version(tmp_path, monkeypatch):
    w = tracked(tmp_path, monkeypatch, single, owner_ttl=60)
    claimed, outputs = done(w, 'a')
    key = registration_key(claimed, outputs.model)
    w.fake.commit_then_fail['create_model_version'] = Crash('the create_model_version response was lost')
    with pytest.raises(Crash):
        w.publisher.process(w.owner)
    assert len(w.fake.versions) == 1 and event(w, key)['status'] == 'PENDING'
    after = restarted(w)
    after.publisher.process(after.owner)
    row = event(w, key)
    assert row['status'] == 'ACKNOWLEDGED'
    assert identity(row) | {'run_id': None} == {'name': 'factory-project.1-a', 'version': '1', 'run_id': None,
                                                'adopted': True, 'duplicates': []}
    assert len(w.fake.versions) == 1 and count(w.fake, 'create_model_version') == 1


def test_uncertain_search_never_creates(tmp_path, monkeypatch):
    w = tracked(tmp_path, monkeypatch, single)
    claimed, outputs = done(w, 'a')
    key = registration_key(claimed, outputs.model)
    w.fake.commit_then_fail['create_model_version'] = ConnectionError('the response was lost')
    w.publisher.process(w.owner)
    row = event(w, key)
    assert (row['status'], row['tries'], json.loads(row['intent'])['op']) == ('PENDING', 1, 'create_model_version')
    assert tracking().TRACKING_UNCERTAIN in row['last_error']
    w.fake.search_unavailable = True
    for _ in range(20):
        if event(w, key)['status'] != 'PENDING':
            break
        due(w, key)
        w.publisher.process(w.owner)
    row = event(w, key)
    assert (row['status'], row['tries']) == ('UNCERTAIN', 9)
    assert count(w.fake, 'create_model_version') == 1                 # a failed search never leads to a create
    assert count(w.fake, 'search_model_versions') == 8
    w.outbox.rearm(row['event_id'])
    row = event(w, key)
    assert (row['status'], row['tries'], row['intent']) == ('PENDING', 0, event(w, key)['intent'])
    assert row['intent'] is not None                                   # the operator's re-arm searches first again
    w.publisher.process(w.owner)
    assert event(w, key)['status'] == 'PENDING' and count(w.fake, 'create_model_version') == 1
    w.fake.search_unavailable = False
    due(w, key)
    w.publisher.process(w.owner)
    assert event(w, key)['status'] == 'ACKNOWLEDGED' and identity(event(w, key))['adopted'] is True
    assert count(w.fake, 'create_model_version') == 1 and len(w.fake.versions) == 1
    with pytest.raises(ValueError, match='only a failed or uncertain tracking event can be re-armed'):
        w.outbox.rearm(row['event_id'])


def test_definitive_empty_search_after_a_failed_create_creates_once(tmp_path, monkeypatch):
    w = tracked(tmp_path, monkeypatch, single)
    claimed, outputs = done(w, 'a')
    key = registration_key(claimed, outputs.model)
    w.fake.fail['create_model_version'] = [ConnectionError('refused before the server committed')]
    w.publisher.process(w.owner)
    assert event(w, key)['status'] == 'PENDING' and not w.fake.versions
    due(w, key)
    w.publisher.process(w.owner)
    row = event(w, key)
    assert row['status'] == 'ACKNOWLEDGED' and identity(row)['adopted'] is False
    assert len(w.fake.versions) == 1 and count(w.fake, 'create_model_version') == 2
    assert count(w.fake, 'search_model_versions') == 1                # the authoritative empty search came first


def test_duplicate_versions_adopt_lowest_and_are_never_deleted(tmp_path, monkeypatch):
    w = tracked(tmp_path, monkeypatch, single)
    claimed, outputs = done(w, 'a')
    key = registration_key(claimed, outputs.model)
    w.fake.fail['create_model_version'] = [ConnectionError('refused')]
    w.publisher.process(w.owner)
    run_id = identity(event(w, f'evidence:{claimed["lease"].attempt_id}'))['run_id']
    name = 'factory-project.1-a'
    w.fake.seed_version(name, run_id, {'factory.registration_key': 'register:another'})   # not ours
    for _ in range(2):   # two copies an eventually consistent registry might show (more than a page)
        w.fake.seed_version(name, run_id, {'factory.registration_key': key})
    due(w, key)
    w.publisher.process(w.owner)
    row = event(w, key)
    assert identity(row) == {'name': name, 'version': '2', 'run_id': run_id, 'adopted': True, 'duplicates': ['3']}
    assert len(w.fake.versions) == 3 and count(w.fake, 'create_model_version') == 1
    assert not [name for name in dir(w.fake) if 'delete' in name]
    assert not [call for call in w.fake.calls if 'delete' in call[0]]
    # Runs are adopted the same way: the earliest of several, the others listed.
    for _ in range(3):
        w.fake.create_run('1', tags={'factory.tracking_key': 'graph:duplicate'})
    found = w.tracker.find_keyed_runs('graph:duplicate')
    assert [item['run_id'] for item in found] == sorted(item['run_id'] for item in found) and len(found) == 3


# -- 11-13. bounded retries, no execution side effects, fencing -----------------------------------

def test_bounded_exponential_backoff(tmp_path, monkeypatch):
    w = tracked(tmp_path, monkeypatch, single)
    done(w, 'a')
    key = f'graph:{w.run_id}'
    w.fake.fail['log_artifact'] = [RuntimeError('artifact upload failed')] * 50
    w.publisher.process(w.owner)
    for expected in (30, 60, 120, 240, 480, 960, 1920, 3600):
        row = event(w, key)
        assert row['status'] == 'PENDING'
        assert row['next_attempt_at'] - w.clock() == pytest.approx(expected)
        w.clock.advance(expected - 1)
        assert w.outbox.claim_pending(w.owner) == ()   # nothing before next_attempt_at
        w.clock.advance(1)
        w.publisher.process(w.owner)
    row = event(w, key)
    assert (row['status'], row['tries']) == ('FAILED', 9)
    assert 'artifact upload failed' in row['last_error']
    assert len(keyed(w.fake, key)) == 1                # every retry adopted the one graph run
    assert w.outbox.status(w.run_id)['graph']['tracking_status'] == 'failed'


def test_tracking_retries_never_touch_execution(tmp_path, monkeypatch):
    w = tracked(tmp_path, monkeypatch, single)
    claimed, _ = done(w, 'a')
    tables = ('jobs', 'job_attempts', 'gpu_locks', 'resource_leases', 'events', 'budget_reservations',
              'attempt_dispositions', 'node_outputs', 'nodes', 'graph_runs')
    before = {name: readiness.table(w, name) for name in tables}
    w.fake.fail['log_artifact'] = [RuntimeError('upload failed')] * 3
    for _ in range(4):
        w.publisher.process(w.owner)
        w.clock.advance(3600)
    assert {row['status'] for row in rows(w)} == {'ACKNOWLEDGED'}
    assert {name: readiness.table(w, name) for name in tables} == before
    assert len(readiness.table(w, 'job_attempts')) == 1 and acknowledged(w, claimed['lease'].attempt_id)


def test_stale_tracking_claims_are_fenced(tmp_path, monkeypatch):
    outbox_api = api()
    w = tracked(tmp_path, monkeypatch, single, owner_ttl=60, claim_ttl=120)
    (first,) = w.outbox.claim_pending(w.owner)
    assert first.event.kind == 'graph_run' and first.intent is None and first.parent_identity is None
    w.clock.advance(61)                                     # owner A expired; its claim has not
    owner = w.store.acquire_owner('controller-b', ttl_seconds=3600)
    with pytest.raises(ValueError, match='stale controller owner lease'):
        w.outbox.claim_pending(w.owner)
    assert w.outbox.claim_pending(owner) == ()
    w.clock.advance(60)
    (second,) = w.outbox.claim_pending(owner)
    assert second.event_id == first.event_id and second.claim_token != first.claim_token
    for stale in (lambda: w.outbox.record_receipt(first.event_id, {'run_id': 'r'}, claim_token=first.claim_token),
                  lambda: w.outbox.record_intent(first.event_id, {'op': 'create_run'}, claim_token=first.claim_token),
                  lambda: w.outbox.retry(first.event_id, 'late', claim_token=first.claim_token)):
        with pytest.raises(ValueError, match='stale tracking claim'):
            stale()
    w.outbox.record_receipt(second.event_id, {'run_id': 'r'}, claim_token=second.claim_token)
    assert event(w, f'graph:{w.run_id}')['status'] == 'ACKNOWLEDGED'
    with pytest.raises(ValueError, match='stale tracking claim'):
        w.outbox.record_receipt(second.event_id, {'run_id': 'r'}, claim_token=second.claim_token)
    assert isinstance(second, outbox_api.ClaimedEvent)


# -- 14-18. HTTPS, the pinned destination, simulations, secrets and changed evidence --------------

def test_https_rules_apply_to_created_and_adopted_runs(tmp_path, monkeypatch):
    from tuning_factory.auth import INSECURE_TRACKING
    monkeypatch.setenv('MLFLOW_TRACKING_TOKEN', TOKEN)
    uploads = ('log_artifact', 'log_artifacts', 'log_batch', 'log_param')

    # A created run whose server-assigned artifact root is plain HTTP: terminated FAILED before any upload.
    w = tracked(tmp_path / 'created', monkeypatch, single, auth_mode='token',
                fake=FakeMlflowClient(artifact_root='http://artifacts.invalid/1'))
    done(w, 'a')
    w.publisher.process(w.owner)
    row = event(w, f'graph:{w.run_id}')
    assert row['status'] == 'FAILED' and INSECURE_TRACKING in row['last_error'] and row['tries'] == 1
    (run,) = w.fake.runs.values()
    assert run.info.status == 'FAILED' and not run.artifacts
    assert not [call for call in w.fake.calls if call[0] in uploads]

    # An adopted run (its create response was lost) is refused the same way.
    w = tracked(tmp_path / 'adopted', monkeypatch, single, auth_mode='token',
                fake=FakeMlflowClient(artifact_root='http://artifacts.invalid/1'))
    done(w, 'a')
    w.fake.commit_then_fail['create_run'] = ConnectionError('the response was lost')
    w.publisher.process(w.owner)
    due(w, f'graph:{w.run_id}')
    w.publisher.process(w.owner)
    row = event(w, f'graph:{w.run_id}')
    assert row['status'] == 'FAILED' and INSECURE_TRACKING in row['last_error']
    assert count(w.fake, 'create_run') == 1 and count(w.fake, 'search_runs') == 1
    assert [run.info.status for run in w.fake.runs.values()] == ['FAILED']
    assert not [call for call in w.fake.calls if call[0] in uploads]

    # The constructor refusals still hold.
    with pytest.raises(ValueError, match='HTTPS'):
        tracking().MLflowTracker('http://mlflow.invalid', client=FakeMlflowClient())
    insecure = FakeMlflowClient()
    insecure.create_experiment(EXPERIMENT, artifact_location='http://artifacts.invalid/root')
    with pytest.raises(ValueError, match='HTTPS'):
        tracking().MLflowTracker(URI, client=insecure)
    monkeypatch.delenv('MLFLOW_TRACKING_TOKEN')
    tracking().MLflowTracker('http://127.0.0.1:5000', client=FakeMlflowClient())   # no credentials: allowed


def test_tracking_destination_must_match_the_pinned_plan(tmp_path, monkeypatch):
    outbox_api, tracking_api = api(), tracking()
    other = TrackingDestination(profile_id='mlflow', revision='1', public_settings_sha256='2' * 64)
    w = tracked(tmp_path, monkeypatch, single, pinned=other)
    done(w, 'a')
    w.fake.calls.clear()
    w.publisher.process(w.owner)
    message = ('tracking destination differs from the approved plan; restart the controller with the approved '
               'MLflow settings')
    graph = event(w, f'graph:{w.run_id}')
    assert (graph['status'], graph['tries'], graph['last_error']) == ('PENDING', 0, message)
    assert {row['status'] for row in rows(w)} == {'PENDING'} and {row['tries'] for row in rows(w)} == {0}
    assert w.fake.calls == []
    assert w.outbox.status(w.run_id)['nodes']['a']['tracking_status'] == 'pending'
    # A lazy tracker never even constructs its client to find out.
    lazy = outbox_api.TrackingPublisher(w.outbox, w.store, tracking_api.LazyMLflowTracker(URI),
                                        local_sink=w.sink)
    w.clock.advance(3600)
    lazy.process(w.owner)
    assert event(w, f'graph:{w.run_id}')['tries'] == 0
    # The settings hash covers the URI, experiment, artifact location and auth mode (D11), never a secret.
    public = w.tracker.public_settings()
    assert public == {'tracking_uri': URI, 'experiment': EXPERIMENT, 'artifact_location': None, 'auth_mode': 'none'}
    assert tracking_api.tracking_settings_sha256(public) == destination().public_settings_sha256
    for changed in ({'tracking_uri': 'https://other.invalid'}, {'experiment': 'other'},
                    {'artifact_location': 'mlflow-artifacts:/9'}, {'auth_mode': 'token'}):
        assert tracking_api.tracking_settings_sha256(public | changed) != tracking_api.tracking_settings_sha256(public)
    for refused in ({'auth_mode': 'basic'}, {'tracking_uri': 'https://user:pw@mlflow.invalid'}):
        with pytest.raises(ValueError):
            tracking_api.tracking_settings_sha256(public | refused)
    with pytest.raises(ValueError):
        tracking_api.tracking_settings_sha256(public | {'token': TOKEN})


def test_simulation_runs_use_the_local_sink_only(tmp_path, monkeypatch):
    w = tracked(tmp_path, monkeypatch, simulation=True)
    claimed, _ = done(w, 'a')
    w.fake.calls.clear()
    assert w.publisher.process(w.owner) == 4                 # a simulated model is never registered
    assert w.fake.calls == []
    assert {row['status'] for row in rows(w)} == {'ACKNOWLEDGED'}
    receipts = sorted((w.store.state_dir / 'tracking' / w.run_id).glob('*.json'))
    assert sorted(path.stem for path in receipts) == sorted(row['event_id'] for row in rows(w))
    for row in rows(w):
        receipt = identity(row)
        assert receipt['sink'] == 'local' and receipt['receipt'] == f'tracking/{w.run_id}/{row["event_id"]}.json'
        data = (w.store.state_dir / receipt['receipt']).read_bytes()
        assert digest(data) == receipt['receipt_sha256'] and json.loads(data)['idempotency_key'] == row[
            'idempotency_key']
    assert acknowledged(w, claimed['lease'].attempt_id) and states(w)['b'][0] == 'ready'


def test_no_secrets_in_events_receipts_or_tags(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv('MLFLOW_TRACKING_TOKEN', TOKEN)
    monkeypatch.setenv('MLFLOW_OIDC_TOKEN_URL', 'https://idp.invalid/token')
    monkeypatch.setenv('MLFLOW_OIDC_CLIENT_ID', 'factory-client')
    monkeypatch.setenv('MLFLOW_OIDC_CLIENT_SECRET', OIDC_SECRET)
    w = tracked(tmp_path, monkeypatch, auth_mode='oidc')
    assert w.tracker.public_settings()['auth_mode'] == 'oidc'
    claimed, _ = done(w, 'a')
    failure = f'401 Unauthorized: Authorization: Bearer {TOKEN}; client_secret={OIDC_SECRET}'
    w.fake.fail['log_artifact'] = [ConnectionError(failure)]
    w.fake.fail['search_runs'] = [ConnectionError(failure)]
    w.fake.commit_then_fail['create_run'] = ConnectionError(failure)
    snapshots, errors = [], []

    def publish(times):
        for _ in range(times):
            w.publisher.process(w.owner)
            with w.store.jobs.connection() as db:   # the stored errors exist only until a retry succeeds
                snapshots.append('\n'.join(db.iterdump()))
            snapshots.append(json.dumps(w.outbox.status(w.run_id)))
            errors.extend(row['last_error'] for row in rows(w) if row['last_error'])
            w.clock.advance(3600)

    publish(6)
    w.ctl = w.store.acquire_controller(w.run_id, ttl_seconds=3600)   # the fake clock outran the run lease
    readiness.prepare(w, 'b')
    failed(w, 'b')   # a failed attempt's evidence too
    publish(3)
    assert {row['status'] for row in rows(w)} == {'ACKNOWLEDGED'}
    assert len(errors) >= 3 and all('[redacted]' in error for error in errors)   # the failures were recorded
    files = b''.join(path.read_bytes() for path in w.store.state_dir.rglob('*') if path.is_file()
                     and path.suffix in {'.json', '.txt', '.log', ''})
    recorded = repr(w.fake.calls) + repr({run_id: (run.data, run.artifacts) for run_id, run in w.fake.runs.items()})
    recorded += repr([vars(item) for item in w.fake.versions])
    shown = '\n'.join(snapshots) + json.dumps(w.tracker.public_settings())
    for secret in (TOKEN, OIDC_SECRET):
        assert secret not in shown and secret.encode() not in files
        assert secret not in recorded and secret not in caplog.text
    assert identity(event(w, f'evidence:{claimed["lease"].attempt_id}'))['run_id']


def test_local_evidence_change_after_enqueue_is_refused(tmp_path, monkeypatch):
    w = tracked(tmp_path, monkeypatch)
    claimed, outputs = done(w, 'a')
    attempt = claimed['lease'].attempt_id
    manifest = w.store.state_dir / 'jobs' / claimed['id'] / 'manifest.json'
    manifest.write_bytes(manifest.read_bytes().replace(b'"train"', b'"tampered"', 1))
    w.publisher.process(w.owner)
    row = event(w, f'evidence:{attempt}')
    assert row['status'] == 'FAILED' and 'local evidence changed after enqueue' in row['last_error']
    run = keyed(w.fake, f'attempt:{attempt}')[0]
    assert run.artifacts == {} and run.info.status == 'RUNNING'
    assert not [call for call in w.fake.calls if call[0] in {'log_artifact', 'log_artifacts', 'log_batch',
                                                              'get_run'} and call[1][0] == run.info.run_id]
    assert event(w, registration_key(claimed, outputs.model))['status'] == 'PENDING' and not w.fake.versions
    assert acknowledged(w, attempt) is False and states(w)['b'][0] == 'waiting'


# -- 19-20. legacy v1 registration (D10) ----------------------------------------------------------

def v1_factory(tmp_path, fake):
    from tuning_factory.execution import LocalExecutor
    from tuning_factory.runner import Factory

    class Transport(LocalExecutor):
        remote = True

        def run(self, backend, directory, timeout, canceled, checkpoint=None, *, launch_id=None):
            output = directory / 'output'
            output.mkdir(exist_ok=True)
            (output / 'adapter_config.json').write_text('{}')
            (output / 'adapter_model.safetensors').write_bytes(b'transport fixture only')

    factory = Factory(tmp_path, tracker=tracking().MLflowTracker(URI, client=fake))
    factory.registry.register_dataset('dummy', '1', 'examples/dummy.json', export_allowed=True)
    return factory, Transport


def v1_work(factory, transport):
    from tuning_factory.config import load_config
    from tuning_factory.training import LlamaFactoryBackend
    job = factory.submit(load_config('configs/ai/paper_extraction.yaml'), simulation=False)
    return job, lambda: factory.work(executor=transport(), backend=LlamaFactoryBackend(), approved_transfer=True)


def test_v1_register_candidate_adopts_after_a_lost_receipt(tmp_path):
    from tuning_factory.runner import Factory
    fake = FakeMlflowClient()
    factory, transport = v1_factory(tmp_path, fake)
    job, work = v1_work(factory, transport)
    fake.commit_then_fail['create_model_version'] = Crash('the create_model_version response was lost')
    with pytest.raises(Crash):
        work()
    assert len(fake.versions) == 1 and factory.store.get(job['id'])['status'] == 'EVALUATING'
    lease = factory.store.current_lease(job['id'])
    assert factory.store.recover(stale_after_seconds=-1) == 1

    class Collector:
        def collect_result(self, directory):
            return {'status': 'COMPLETED'}

    restarted_factory = Factory(tmp_path, tracker=tracking().MLflowTracker(URI, client=fake))
    restored = restarted_factory.reconcile(job['id'], Collector())
    assert restored['status'] == 'COMPLETED' and restored['attempts'] == 1
    (version,) = fake.versions
    assert count(fake, 'create_model_version') == 1
    manifest_sha = factory.store.get(job['id'])['manifest']['sha256']
    assert version.tags['factory.registration_key'] == f'v1:{manifest_sha}:{lease.attempt_id}'   # attempt included
    assert (version.name, version.tags['approval'], version.run_id) == (
        'factory-paper_extraction', 'pending', restored['run_id'])


def test_v1_tracking_failure_semantics_unchanged(tmp_path):
    for case in ('create', 'search'):
        fake = FakeMlflowClient()
        factory, transport = v1_factory(tmp_path / case, fake)
        job, work = v1_work(factory, transport)
        if case == 'create':
            fake.fail['create_model_version'] = [RuntimeError('registry unavailable')]
        else:
            fake.search_unavailable = True
        result = work()
        assert result['status'] == 'FAILED'
        assert result['error'] == 'MLflow persistence failed; training artifacts retained; see tracking-error.txt'
        error = (factory.directory(job['id']) / 'tracking-error.txt').read_text(encoding='utf-8')
        assert ('registry unavailable' if case == 'create' else 'unavailable') in error
        assert not fake.versions and count(fake, 'create_model_version') == (1 if case == 'create' else 0)
        assert not factory.store.gpu_locked('mock-cpu')
