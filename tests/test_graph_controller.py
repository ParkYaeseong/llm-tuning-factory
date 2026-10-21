"""Task 8.5: the GraphController and end-to-end recovery (plan §4 "8.5"; decisions D1-D14 as accepted).

Main env only, deterministic fakes only:
- ``FakeClock`` (``test_graph_store``): wall-clock compatible, advanced explicitly.
- ``FakeProvider``: approved destinations by profile ID; it records ``resolve``/``recover`` calls and can be scripted to
  refuse or to observe another price. Its executors only count calls.
- ``ScriptedDispatcher``: the ``OperationDispatcher`` protocol on the real ``JobStore`` attempt API (``begin_launch``,
  ``transition``, ``release_gpu``, ``reconcile_result``). Outputs are frozen through the real artifact store under the
  attempt's producer identity. Per node it can complete, fail remotely, stay uncertain until ``deliver``, run a hook
  while running or while seeding, or ``Crash`` at a named step. It counts training calls and launches per node.
- ``ManualPool``: ``submit`` stores the dispatch callable; ``run``/``run_all`` execute it in the test thread, so two
  attempts can be in flight at once without sleeps.
- ``FakeMlflowClient`` (8.3) behind the real ``MLflowTracker``.

An autouse fixture makes ``socket.socket.connect`` raise, refuses a real MLflow client and isolates the MLflow
settings. No GPU, SSH, network, download or MLflow write.
"""
import concurrent.futures
import importlib
import json
import os
import socket
import threading
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
import test_graph_readiness as readiness
import test_model_inputs as inputs
import test_tracking_outbox as outbox_tests
from test_graph_readiness import (
    build,
    diamond,
    evaluate,
    evaluation_output,
    materialize,
    pods,
    produced,
    select,
    ssh_destinations,
    statuses,
    train,
)
from test_operation_runner import Crash

from tuning_factory.executor_profiles import PROFILE_CHANGED, ResolvedDestination
from tuning_factory.graph_planner import ApprovalGrant, NodeModelBinding
from tuning_factory.jobs import GRAPH_CANCEL_REFUSED, GRAPH_LAUNCH_REFUSED
from tuning_factory.model_artifacts import ModelArtifactRef
from tuning_factory.registry import canonical, digest
from tuning_factory.worker_protocol import OperationResult

SECRET = 'synthetic-controller-secret-7c41e9'   # never a real credential


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    """No network, no real MLflow client or OIDC refresher, none of the developer's MLflow settings, and no test-only
    tracking acknowledgement: only the outbox's receipt may open the gate here (8.3 review Minor 6)."""
    connects = []

    def refuse(*args, **kwargs):
        connects.append(args)
        raise AssertionError('graph controller tests must not open a network connection')

    monkeypatch.setattr(socket.socket, 'connect', refuse)
    for key in outbox_tests.SECRET_VARIABLES:
        monkeypatch.setenv(key, 'x')
        monkeypatch.delenv(key)
    for key in ('MLFLOW_HTTP_REQUEST_MAX_RETRIES', 'MLFLOW_HTTP_REQUEST_TIMEOUT'):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr('mlflow.MlflowClient', lambda *a, **k: pytest.fail('a real MLflow client was created'))
    monkeypatch.setattr('tuning_factory.auth.configure_oidc', lambda: None)
    monkeypatch.setattr('tuning_factory.graph_store.GraphStore.test_acknowledgement', False)
    return connects


def api():
    # A missing implementation is a test failure, rather than a collection error.
    try:
        module = importlib.import_module('tuning_factory.graph_controller')
    except ModuleNotFoundError:
        pytest.fail('tuning_factory.graph_controller (Task 8.5) is missing')
    for name in ('GraphController', 'ControllerSettings', 'DispatchRouter', 'TickReport', 'ThreadDispatchPool',
                 'graph_status'):
        if not hasattr(module, name):
            pytest.fail(f'graph_controller.{name} (Task 8.5) is missing')
    return module


# -- fakes ---------------------------------------------------------------------------------------

class ManualPool:
    """Dispatch callables wait here until the test runs them; a ``Crash`` escapes and the future stays pending."""

    def __init__(self):
        self.pending = {}

    def submit(self, key, fn):
        future = concurrent.futures.Future()
        self.pending[key] = (fn, future)
        return future

    def run(self, key):
        fn, future = self.pending.pop(key)
        future.set_running_or_notify_cancel()
        try:
            result = fn()
        except Exception as exc:
            future.set_exception(exc)
        else:
            future.set_result(result)
        return future

    def run_all(self):
        while self.pending:
            self.run(next(iter(self.pending)))


class FakeExecutor:
    remote = True

    def __init__(self, profile, facts, *, recovery=False):
        ssh = profile.provider == 'ssh_server'
        self.profile_id, self.recovery_only = profile.profile_id, recovery
        self.pod = None if ssh else {'id': profile.pod_id}
        self.server_profile, self.server_facts = (profile, facts) if ssh else (None, None)
        self.model_cache_root = '/workspace/factory-desktop/model-cache'
        self.calls = []

    def publish_cached_artifact(self, transfer, *, source_remote_dir, launch_id):
        self.calls.append('publish_cached_artifact')


class FakeProvider:
    """``ExecutorProvider`` over approved destinations: ``refuse[profile_id]`` raises, ``price[profile_id]`` observes
    another live price."""

    secret = None   # a configured credential this provider knows, as SettingsExecutorProvider.redact

    def __init__(self, destinations, clock):
        self.destinations = {profile.profile_id: (profile, facts) for profile, facts in destinations}
        self.clock = clock
        self.resolves, self.recovers, self.executors = [], [], []
        self.refuse, self.price = {}, {}

    def redact(self, text):
        return text.replace(self.secret, '[redacted]') if self.secret else text

    def _executor(self, profile_id, **options):
        profile, facts = self.destinations[profile_id]
        executor = FakeExecutor(profile, facts, **options)
        self.executors.append(executor)
        return executor

    @contextmanager
    def resolve(self, profile_id, approved_revision, *, pinned, runtime_profile=None, model_ops=False):
        self.resolves.append(profile_id)
        if profile_id in self.refuse:
            raise self.refuse[profile_id]
        profile, facts = self.destinations[profile_id]
        assert pinned == profile and approved_revision == profile.revision
        update = {'observed_at': datetime.fromtimestamp(self.clock(), tz=timezone.utc)}
        if profile_id in self.price:
            update['total_hourly_cost_usd'] = self.price[profile_id]
        yield ResolvedDestination(profile=profile, observation=facts.model_copy(update=update),
                                  executor=self._executor(profile_id))

    @contextmanager
    def recover(self, profile_id, approved_revision, *, pinned, pinned_facts, manifest_sha256, runtime_profile=None):
        self.recovers.append(profile_id)
        yield self._executor(profile_id, recovery=True)


def outcome(job_id, attempt_id, status, *, uncertain=False, outputs=None, targets=(), error=None):
    # The light module's outcome type: the controller uses outcomes structurally, never by type.
    from tuning_factory.simulated_dispatch import OperationOutcome
    return OperationOutcome(job_id, attempt_id, status, uncertain, outputs, tuple(targets), error)


class ScriptedDispatcher:
    """``OperationDispatcher`` on the real JobStore attempt API; ``failure_kind`` is the real runner's, read from the
    same records (``<launch_id>.result.json`` and ``remote-attempt.json``) this dispatcher writes."""

    def __init__(self, w):
        from tuning_factory.attempt_runner import OperationAttemptRunner
        self.w, self.store, self.jobs = w, w.store, w.store.jobs
        self.runner = OperationAttemptRunner(w.store, root=w.store.state_dir)
        self.script, self.accuracy, self.crash = {}, {}, {}
        self.during, self.seed, self.final_race = {}, {}, set()
        self.training_calls, self.launches, self.produced = Counter(), Counter(), Counter()
        self.calls, self.outputs, self.delivered = [], {}, {}

    def _step(self, node, step):
        if self.crash.get(node) == step:
            del self.crash[node]
            raise Crash(f'crash at {step} of {node}')

    def _launch(self, job_id, launch_id):
        return self.store.state_dir / 'jobs' / job_id / 'launches' / launch_id

    def _result(self, job, lease, launch_id, status, error=None):
        manifest = job['manifest']
        error_filter = getattr(self.jobs, 'error_filter', None)
        if error and error_filter is not None:   # as the runner's _accept
            error = error_filter(error)
        result = OperationResult(status=status, launch_id=launch_id, attempt_id=lease.attempt_id,
                                 manifest_sha256=manifest['sha256'],
                                 operation_sha256=digest(canonical(manifest['operation'])),
                                 kind=manifest['operation']['kind'], engine_version='scripted', error=error)
        path = self._launch(job['id'], launch_id).parent / f'{launch_id}.result.json'
        path.write_bytes(canonical(result.model_dump(mode='json')))

    def run_claimed(self, claimed, executor, *, approval):
        lease = claimed['lease']
        job = self.jobs.get(lease.job_id)
        node = job['graph_node_id']
        self.calls.append(('run_claimed', node))
        assert isinstance(approval, ApprovalGrant)
        self._step(node, 'before_launch')
        launch_id = self.jobs.begin_launch(lease)   # the D12 gate: GRAPH_LAUNCH_REFUSED / GRAPH_CANCEL_REFUSED
        self._step(node, 'after_begin_launch')
        self.jobs.transition(lease, 'RUNNING')
        self._step(node, 'before_record')
        launch = self._launch(job['id'], launch_id)
        launch.mkdir(parents=True)
        (launch / 'remote-attempt.json').write_text(json.dumps({'launch_id': launch_id}), encoding='utf-8')
        self.launches[node] += 1
        actions = self.script.get(node) or []
        action = actions.pop(0) if actions else 'complete'
        if job['manifest']['operation']['kind'] == 'train':
            self.training_calls[node] += 1
        self._step(node, 'running')
        if node in self.during:
            self.during.pop(node)()
        if action == 'uncertain':
            error = 'remote execution uncertain; reconcile the same launch'
            self.jobs.transition(lease, 'FAILED', error)   # the lock is kept, as the runner's ExecutionUncertain
            return outcome(job['id'], lease.attempt_id, 'FAILED', uncertain=True, error=error)
        if action == 'fail_remote':
            error = f'remote operation failed for {node} with {SECRET} at /workspace/factory-desktop/jobs/x'
            self._result(job, lease, launch_id, 'FAILED', error)
            self.jobs.transition(lease, 'FAILED', error)
            self.jobs.release_gpu(lease, execution_stopped=True)
            return outcome(job['id'], lease.attempt_id, 'FAILED', error=error)
        self._result(job, lease, launch_id, 'COMPLETED')
        if self.jobs.get(job['id'])['cancel_requested']:   # the runner's semantics: a late completion is CANCELED
            self.jobs.transition(lease, 'CANCELED', 'cancellation requested')
            self.jobs.release_gpu(lease, execution_stopped=True)
            return outcome(job['id'], lease.attempt_id, 'CANCELED', error='cancellation requested')
        self.jobs.transition(lease, 'EVALUATING')
        outputs, targets = self._produce(job, lease)
        if self.jobs.transition(lease, 'COMPLETED') == 'CANCELED':   # a cancel during the freeze (re-review M1)
            self.jobs.release_gpu(lease, execution_stopped=True)
            return outcome(job['id'], lease.attempt_id, 'CANCELED', error='cancellation requested')
        self._step(node, 'before_release')
        self.jobs.release_gpu(lease, execution_stopped=True)
        if node in self.seed:   # the runner seeds the remote cache after release_gpu (D8)
            self.seed.pop(node)()
        self._step(node, 'seeding')
        if outputs.model is not None and executor is not None:
            executor.publish_cached_artifact(None, source_remote_dir='/remote', launch_id=launch_id)
            record = launch.parent / f'{launch_id}.cache-seed.json'
            record.write_bytes(canonical({'status': 'published', 'error': None}))
        return outcome(job['id'], lease.attempt_id, 'COMPLETED', outputs=outputs, targets=targets)

    def deliver(self, node_id, status='COMPLETED'):
        self.delivered[node_id] = status

    def reconcile(self, job_id, executor):
        job = self.jobs.get(job_id)
        node = job['graph_node_id']
        self.calls.append(('reconcile', node))
        lease = self.jobs.current_lease(job_id)
        if node in self.final_race:   # released concurrently, between the controller's read and this call
            self.final_race.discard(node)
            self.jobs.release_gpu(lease, execution_stopped=True)
        if not self.jobs.holds_lock(lease):
            raise ValueError('attempt is final: its lock is released, so reconcile never reopens it; use outputs_for '
                             "to rebuild a completed attempt's outputs")
        if job['status'] == 'COMPLETED':
            result = self.outputs_for(job_id)
            self.jobs.release_gpu(lease, execution_stopped=True)
            return result
        attempt = self.jobs.validated_attempt(lease)
        launch = self._launch(job_id, attempt['launch_id'])
        if not attempt['launch_started']:
            self.jobs.reconcile_result(lease, 'FAILED', job['error'])
            return outcome(job_id, lease.attempt_id, 'FAILED', error=job['error'])
        if not (launch / 'remote-attempt.json').is_file() or node not in self.delivered:
            return None
        status = self.delivered.pop(node)
        self._result(job, lease, attempt['launch_id'], status, None if status == 'COMPLETED' else 'late failure')
        if job['cancel_requested'] or self.store.get_run(job['graph_run_id'])['status'] == 'CANCELED':
            # The runner's semantics (11.5 R1 I-1): a late result collected after a cancel is CANCELED.
            self.jobs.reconcile_result(lease, 'CANCELED', 'cancellation requested')
            return outcome(job_id, lease.attempt_id, 'CANCELED', error='cancellation requested')
        if status != 'COMPLETED':
            self.jobs.reconcile_result(lease, status, 'late failure')
            return outcome(job_id, lease.attempt_id, status, error='late failure')
        outputs, targets = self._produce(job, lease)
        if self.jobs.reconcile_result(lease, 'COMPLETED') == 'CANCELED':   # a cancel during the freeze (M1)
            return outcome(job_id, lease.attempt_id, 'CANCELED', error='cancellation requested')
        return outcome(job_id, lease.attempt_id, 'COMPLETED', outputs=outputs, targets=targets)

    def outputs_for(self, job_id):
        job = self.jobs.get(job_id)
        self.calls.append(('outputs_for', job['graph_node_id']))
        assert job['status'] == 'COMPLETED'
        lease = self.jobs.current_lease(job_id)
        outputs, targets = self._produce(job, lease)
        return outcome(job_id, lease.attempt_id, 'COMPLETED', outputs=outputs, targets=targets)

    def failure_kind(self, job_id):
        self.calls.append(('failure_kind', self.jobs.get(job_id)['graph_node_id']))
        return self.runner.failure_kind(job_id)

    def _produce(self, job, lease):
        """The attempt's outputs, frozen once under its producer identity (a rebuild returns the same receipt)."""
        key = (job['id'], lease.attempt_id)
        if key in self.outputs:
            return self.outputs[key]
        gs = readiness.api()
        node, operation = job['graph_node_id'], job['manifest']['operation']
        claimed = {'id': job['id'], 'lease': lease, 'manifest': job['manifest'], 'graph_node_id': node}
        self.produced[node] += 1
        targets = ()
        if operation['kind'] == 'evaluate':
            outputs = gs.NodeOutputs(evaluation=evaluation_output(self.w, claimed, self.accuracy.get(node, 0.5)))
        else:
            if operation['kind'] == 'materialize':
                adapter = ModelArtifactRef.model_validate(operation['model'])
                base = self.store.artifacts.verify(adapter).effective_base
                parents = tuple(sorted((base, adapter), key=lambda ref: (ref.artifact_id, ref.inventory_sha256)))
                pair = inputs.full_model(self.w.tmp, 'out-' + lease.attempt_id, base=base, parents=parents)
            else:
                if operation['kind'] == 'merge':
                    first = ModelArtifactRef.model_validate(operation['models'][0])
                    base = self.store.artifacts.verify(first).effective_base
                else:
                    raw = operation['model_input']['policy']['base']
                    base = ModelArtifactRef.model_validate(raw) if 'artifact_id' in raw else None
                pair = inputs.peft_adapter(self.w.tmp, 'out-' + lease.attempt_id, base=base,
                                           parents=(base,) if base is not None else ())
            ref = self.store.artifacts.freeze(pair[0], produced(self.w, claimed, pair[1]))
            outputs = gs.NodeOutputs(model=NodeModelBinding(node_id=node, model=ref))
            if operation['kind'] == 'materialize':
                targets = (inputs.evidence(ref),)
        self.outputs[key] = (outputs, targets)
        return outputs, targets


# -- worlds and drivers --------------------------------------------------------------------------

def world(tmp_path, monkeypatch, nodes, *, destinations=None, auth_mode='none', **plan):
    """A submitted real plan (``test_graph_readiness.build``) pinned to the fake MLflow destination, with the fake
    provider, the scripted dispatcher and a manual pool. Its pre-controller run lease is expired at once."""
    monkeypatch.setattr(readiness, 'TRACKING', outbox_tests.destination(auth_mode))
    destinations = destinations or pods('pod1')
    w = build(tmp_path, nodes, destinations=destinations, resolver=False, **plan)
    with w.store.jobs.connection() as db:
        db.execute('UPDATE controller_leases SET expires=0')   # build()'s owner-less lease, as if it expired
    w.fake = outbox_tests.FakeMlflowClient()
    w.tracker = outbox_tests.tracking().MLflowTracker(outbox_tests.URI, client=w.fake)
    w.provider = FakeProvider(destinations, w.clock)
    w.dispatcher = ScriptedDispatcher(w)
    w.pool = ManualPool()
    return w


def start(w, owner_id='controller-a', *, settings=None, pool=None, dispatcher=None, provider=None, **options):
    gc = api()
    w.pool = pool or ManualPool()
    controller = gc.GraphController(w.store, provider or w.provider, dispatcher or w.dispatcher, w.tracker, w.clock,
                                    pool=w.pool, settings=settings or gc.ControllerSettings(), **options)
    return controller, controller.acquire_controller(owner_id)


def restart(w, owner_id='controller-b', *, advance=None, **options):
    """A new controller process: the old owner lease expired and the old claims went stale, then new instances."""
    gc = api()
    settings = options.get('settings') or gc.ControllerSettings()
    w.clock.advance(advance or settings.lease_ttl_seconds + settings.stale_attempt_seconds + 1)
    return start(w, owner_id, **options)


def events(w, status=None):
    return [row for row in outbox_tests.rows(w) if status is None or row['status'] == status]


def run_row(w):
    return w.store.get_run(w.run_id)


def quiet(w):
    return run_row(w)['status'] in {'COMPLETED', 'FAILED', 'CANCELED'} and not events(w, 'PENDING') and not w.pool.pending


def drive(w, controller, owner, *, ticks=60, until=None, each=None):
    """Tick and run every dispatched attempt until ``until()`` (by default: the run is final and tracked)."""
    for _ in range(ticks):
        controller.tick(owner)
        if each is not None:
            each()
        w.pool.run_all()
        if (until or (lambda: quiet(w)))():
            return
    errors = {item['node_id']: (item['error'], item['wait_reason']) for item in run_row(w)['nodes']}
    raise AssertionError(f'the graph did not settle: {statuses(w)} {run_row(w)["status"]} {errors}')


def node(w, node_id):
    return next(item for item in run_row(w)['nodes'] if item['node_id'] == node_id)


def job_of(w, node_id):
    return node(w, node_id)['job_id']


def pinned(w, node_id):
    return w.store.jobs.get(job_of(w, node_id))['manifest']['operation']['resources']['executor']['profile_id']


def attempts(w, node_id):
    return readiness.table(w, 'job_attempts', 'WHERE job_id=?', (job_of(w, node_id),))


def status_of(w, node_id, controller=None):
    status = controller.status() if controller else api().graph_status(w.store, w.run_id, now=w.clock())
    (run,) = [item for item in status['runs'] if item['run_id'] == w.run_id]
    return next(item for item in run['nodes'] if item['node_id'] == node_id)


def active_jobs(w):
    return [job for job in w.store.jobs.list() if job['status'] in {'PROVISIONING', 'RUNNING', 'EVALUATING'}]


def chain(r):
    return [train(r, 'a'), train(r, 'b', 'a')]


# -- 1-3. concurrency and exclusivity ------------------------------------------------------------

def test_two_resources_run_a_diamond_concurrently(tmp_path, monkeypatch):
    gc = api()
    w = world(tmp_path, monkeypatch, diamond, destinations=pods('pod1', 'pod2'), max_concurrent=2)
    controller, owner = start(w)
    for _ in range(10):
        report = controller.tick(owner)
        if len(w.pool.pending) == 2:
            break
        w.pool.run_all()
    b, c = job_of(w, 'b'), job_of(w, 'c')
    assert isinstance(report, gc.TickReport) and set(report.claimed) == {b, c}   # one tick claims both
    assert set(w.pool.pending) == {b, c} and {job['id'] for job in active_jobs(w)} == {b, c}
    assert sorted((pinned(w, 'b'), pinned(w, 'c'))) == ['p1', 'p2']
    state = controller.status()['controller']
    assert set(state['in_flight']) == {b, c}
    assert state['busy'] == ['existing_runpod:pod1', 'existing_runpod:pod2']
    w.pool.run(c)   # both were in flight before either finished
    w.pool.run(b)
    drive(w, controller, owner)
    assert statuses(w) == {'a': 'COMPLETED', 'b': 'COMPLETED', 'c': 'COMPLETED', 'd': 'COMPLETED'}
    assert run_row(w)['status'] == 'COMPLETED'
    assert w.dispatcher.training_calls == {'a': 1, 'b': 1, 'c': 1}
    assert w.dispatcher.launches == {'a': 1, 'b': 1, 'c': 1, 'd': 1}
    assert [(row['kind'], row['status']) for row in events(w) if row['status'] != 'ACKNOWLEDGED'] == []


def test_one_pod_runs_ready_branches_sequentially(tmp_path, monkeypatch):
    w = world(tmp_path, monkeypatch, lambda r: diamond(r, profiles=('p1',)), max_concurrent=2)
    controller, owner = start(w)

    def one_at_a_time():
        assert len(w.pool.pending) <= 1 and len(active_jobs(w)) <= 1
        waiting = [item for item in ('b', 'c') if node(w, item)['status'] == 'READY']
        if waiting and w.pool.pending:   # the other branch waits for the one Pod, and says so
            assert node(w, waiting[0])['wait_reason'] == 'waiting for destination existing_runpod:pod1'

    drive(w, controller, owner, each=one_at_a_time)
    assert set(statuses(w).values()) == {'COMPLETED'}
    assert w.dispatcher.launches == {'a': 1, 'b': 1, 'c': 1, 'd': 1}


def shared_uuid_destinations():
    """Two SSH servers (two server IDs) whose GPU inventories share a UUID."""
    from test_gpu_servers import observation, settings
    first, second = settings(), settings().model_copy(update={'remote_root': '/home/researcher/third'})
    a, b = observation(first), observation(second, server_id='c' * 64)
    return [(first.profile(a, profile_id='s1'), a), (second.profile(b, profile_id='s4'), b)]


@pytest.mark.parametrize('case', ['runpod_alias', 'ssh_alias', 'shared_gpu_uuid'])
def test_aliases_of_one_pod_are_mutually_exclusive(tmp_path, monkeypatch, case):
    destinations, (one, other) = {
        'runpod_alias': (pods('pod1', aliases=(('p3', 'pod1'),)), ('p1', 'p3')),
        'ssh_alias': (ssh_destinations()[:2], ('s1', 's2')),
        'shared_gpu_uuid': (shared_uuid_destinations(), ('s1', 's4'))}[case]
    w = world(tmp_path, monkeypatch, lambda r: [evaluate(r, 'x', r.first, (one,)), evaluate(r, 'y', r.second, (other,))],
              destinations=destinations, max_concurrent=2)
    controller, owner = start(w)
    concurrent_seen = []

    def never_concurrent():
        concurrent_seen.append(len(w.pool.pending))
        assert len(w.pool.pending) <= 1 and len(active_jobs(w)) <= 1

    drive(w, controller, owner, each=never_concurrent)
    assert max(concurrent_seen) == 1
    assert statuses(w) == {'x': 'COMPLETED', 'y': 'COMPLETED'}
    assert (pinned(w, 'x'), pinned(w, 'y')) == (one, other)


# -- 4-7. failure propagation, pause, cancel, duplicate controllers -------------------------------

def evaluations(allow_partial):
    def nodes(r):
        return [evaluate(r, 'e1', r.base), evaluate(r, 'e2', r.first), evaluate(r, 'e3', r.second),
                select('s', ('e1', 'e2', 'e3'), allow_partial=allow_partial, min_success=2), evaluate(r, 'after', 's')]
    return nodes


@pytest.mark.parametrize('case', ['partial', 'strict', 'train'])
def test_parent_failure_blocks_and_partial_join_proceeds(tmp_path, monkeypatch, case):
    if case == 'train':
        w = world(tmp_path, monkeypatch, lambda r: [train(r, 'a'), train(r, 'b', 'a'), evaluate(r, 'e', 'b')])
        w.dispatcher.script['a'] = ['fail_remote']
    else:
        w = world(tmp_path, monkeypatch, evaluations(case == 'partial'))
        w.dispatcher.script['e1'] = ['fail_remote']
        w.dispatcher.accuracy.update(e2=0.4, e3=0.7)
    controller, owner = start(w)
    drive(w, controller, owner)
    if case == 'train':
        assert statuses(w) == {'a': 'FAILED', 'b': 'BLOCKED', 'e': 'BLOCKED'}
        assert node(w, 'b')['error'] == 'parent a failed' and job_of(w, 'b') is None
    elif case == 'strict':
        assert statuses(w) == {'e1': 'FAILED', 'e2': 'COMPLETED', 'e3': 'COMPLETED', 's': 'BLOCKED',
                               'after': 'BLOCKED'}
        assert node(w, 's')['error'] == 'partial selection is not allowed'
    else:   # the select proceeds with two of three evaluations, and its winner is evaluated
        assert statuses(w) == {'e1': 'FAILED', 'e2': 'COMPLETED', 'e3': 'COMPLETED', 's': 'COMPLETED',
                               'after': 'COMPLETED'}
        assert status_of(w, 's')['selection'] == w.refs.second.artifact_id
    assert run_row(w)['status'] == 'FAILED'   # a failed node fails the run (finalize_run)
    assert all(count == 1 for count in w.dispatcher.launches.values())


def two_pods_two_trains(r):
    return [train(r, 'x', profiles=('p1',)), train(r, 'y', profiles=('p2',))]


def test_pause_dispatches_nothing_new(tmp_path, monkeypatch):
    w = world(tmp_path, monkeypatch, two_pods_two_trains, destinations=pods('pod1', 'pod2'), max_concurrent=2)
    controller, owner = start(w)
    controller.tick(owner)
    x, y = job_of(w, 'x'), job_of(w, 'y')
    assert set(w.pool.pending) == {x, y}
    w.dispatcher.during['x'] = lambda: w.store.pause(w.run_id)   # pause arrives while x runs
    w.pool.run(x)
    w.pool.run(y)   # claimed before the pause, launched after it: begin_launch refuses (D12)
    # Returned unlaunched at once, from the dispatch thread (hand-off note 2), before any tick.
    assert w.store.jobs.get(y)['status'] == 'QUEUED' and node(w, 'y')['status'] == 'READY'
    assert [row['disposition'] for row in readiness.table(w, 'attempt_dispositions')] == ['returned_unlaunched']
    executor_y = next(item for item in w.provider.executors if item.profile_id == 'p2')
    assert executor_y.calls == [] and w.dispatcher.launches['y'] == 0
    resolves, runs = len(w.provider.resolves), len(w.dispatcher.calls)
    for _ in range(3):
        report = controller.tick(owner)
        assert report.claimed == ()
    assert len(w.provider.resolves) == resolves                       # no launch resolution while paused
    assert [call for call in w.dispatcher.calls[runs:] if call[0] == 'run_claimed'] == []
    assert node(w, 'x')['status'] == 'COMPLETED'                      # the in-flight attempt settled and was recorded
    assert status_of(w, 'x', controller)['tracking_status'] == 'saved'
    assert w.store.jobs.get(y)['status'] == 'QUEUED'
    w.store.resume(w.run_id)
    before = len([call for call in w.dispatcher.calls if call[0] == 'run_claimed'])
    drive(w, controller, owner)
    assert len([call for call in w.dispatcher.calls if call[0] == 'run_claimed']) == before + 1   # one dispatch
    assert w.dispatcher.launches == {'x': 1, 'y': 1} and run_row(w)['status'] == 'COMPLETED'


@pytest.mark.parametrize('refusal', ['pause', 'job_cancel'])
def test_refused_launches_are_returned_at_once(tmp_path, monkeypatch, refusal):
    """Hand-off note 2: GRAPH_LAUNCH_REFUSED or GRAPH_CANCEL_REFUSED from run_claimed returns the attempt at once."""
    w = world(tmp_path, monkeypatch, chain)
    controller, owner = start(w)
    controller.tick(owner)
    a = job_of(w, 'a')
    if refusal == 'pause':
        w.store.pause(w.run_id)
    else:
        w.store.jobs.cancel(a)   # a job-level cancel (CLI/desktop) of a claimed, unlaunched attempt
    future = w.pool.run(a)
    assert str(future.exception()) == (GRAPH_LAUNCH_REFUSED if refusal == 'pause' else GRAPH_CANCEL_REFUSED)
    expected = ('QUEUED', 'READY') if refusal == 'pause' else ('CANCELED', 'CANCELED')
    assert (w.store.jobs.get(a)['status'], node(w, 'a')['status']) == expected
    assert not w.store.jobs.gpu_locked('existing_runpod:pod1') and w.store.reserved_budget(w.run_id) == 0
    report = controller.tick(owner)
    assert a in report.released and controller.status()['controller']['in_flight'] == []
    if refusal == 'job_cancel':
        assert statuses(w) == {'a': 'CANCELED', 'b': 'BLOCKED'}
        drive(w, controller, owner)
        assert run_row(w)['status'] == 'FAILED'
    assert w.dispatcher.launches == Counter()


def test_cancel_wins_over_join(tmp_path, monkeypatch):
    w = world(tmp_path, monkeypatch, lambda r: [evaluate(r, 'b', r.first, ('p1',)), evaluate(r, 'c', r.second, ('p2',)),
                                                 select('d', ('b', 'c'))],
              destinations=pods('pod1', 'pod2'), max_concurrent=2)
    controller, owner = start(w)
    controller.tick(owner)
    b, c = job_of(w, 'b'), job_of(w, 'c')
    w.pool.run(b)
    controller.tick(owner)   # b recorded and its tracking saved; c still in flight
    assert node(w, 'b')['status'] == 'COMPLETED' and status_of(w, 'b')['tracking_status'] == 'saved'
    w.dispatcher.during['c'] = lambda: w.store.cancel(w.run_id)   # cancel arrives while c runs
    late = w.pool.run(c).result()
    assert late.status == 'CANCELED'                               # its late completion is CANCELED
    drive(w, controller, owner)
    assert statuses(w) == {'b': 'COMPLETED', 'c': 'CANCELED', 'd': 'CANCELED'}
    assert job_of(w, 'd') is None                                  # the select was never compiled or claimed
    assert run_row(w)['status'] == 'CANCELED'
    assert status_of(w, 'b')['tracking_status'] == 'saved'
    summary = [row for row in events(w) if row['kind'] == 'graph_summary']
    assert [json.loads(row['payload'])['final_status'] for row in summary] == ['CANCELED']


def test_duplicate_controller_is_refused(tmp_path, monkeypatch):
    gc = api()
    w = world(tmp_path, monkeypatch, chain)
    controller, owner = start(w)
    second = gc.GraphController(w.store, w.provider, w.dispatcher, w.tracker, w.clock, pool=ManualPool())
    with pytest.raises(ValueError, match='controller already running'):
        second.acquire_controller('controller-b')
    forged = type(owner)('controller-b', 'f' * 32)
    with pytest.raises(ValueError, match='stale controller owner lease'):
        second.tick(forged)
    assert readiness.table(w, 'job_attempts') == [] and w.provider.resolves == []   # zero claims
    controller.tick(owner)
    assert len(readiness.table(w, 'job_attempts')) == 1


# -- 8-12. restart, late results, the operator, fencing and the reconcile scope -------------------

def tracked_chain(r):
    return [train(r, 'a'), evaluate(r, 'e', 'a'), select('s', ('e',))]


@pytest.mark.parametrize('crash', ['after_claim', 'after_begin_launch', 'before_record_outputs',
                                   'before_tracking_receipt', 'inside_local_selection'])
def test_restart_adopts_runs_and_never_relaunches(tmp_path, monkeypatch, crash):
    gs = readiness.api()
    outbox_api = outbox_tests.api()
    w = world(tmp_path, monkeypatch, tracked_chain)
    controller, owner = start(w)
    old_pool, advance = None, None
    if crash == 'after_claim':
        controller.tick(owner)
        assert list(w.pool.pending) == [job_of(w, 'a')]
        old_pool = w.pool   # the process dies before its dispatch thread ran
    elif crash == 'after_begin_launch':
        w.dispatcher.crash['a'] = 'after_begin_launch'
        controller.tick(owner)
        with pytest.raises(Crash):
            w.pool.run_all()
        assert w.store.jobs.get(job_of(w, 'a'))['status'] == 'PROVISIONING'
    elif crash == 'before_record_outputs':
        original = gs.GraphStore.record_outputs

        def crashing(self, lease, outputs):
            monkeypatch.setattr(gs.GraphStore, 'record_outputs', original)
            raise Crash('after run_claimed returned, before record_outputs')

        monkeypatch.setattr(gs.GraphStore, 'record_outputs', crashing)
        controller.tick(owner)
        w.pool.run_all()
        with pytest.raises(Crash):
            controller.tick(owner)
        assert w.store.jobs.get(job_of(w, 'a'))['status'] == 'COMPLETED' and node(w, 'a')['status'] == 'RUNNING'
    elif crash == 'before_tracking_receipt':
        original = outbox_api.TrackingOutbox.record_receipt

        def crashing(self, event_id, identity, *, claim_token):
            row = next(item for item in outbox_tests.rows(w) if item['event_id'] == event_id)
            if row['kind'] == 'attempt_evidence':
                monkeypatch.setattr(outbox_api.TrackingOutbox, 'record_receipt', original)
                raise Crash('after record_outputs and the upload, before the tracking receipt')
            return original(self, event_id, identity, claim_token=claim_token)

        monkeypatch.setattr(outbox_api.TrackingOutbox, 'record_receipt', crashing)
        with pytest.raises(Crash):
            drive(w, controller, owner)
        assert node(w, 'a')['status'] == 'COMPLETED' and node(w, 'e')['job_id'] is None
        advance = 700   # the old claim (600 s TTL) must expire as well
    else:
        original = gs.GraphStore.complete_local_selection

        def crashing(self, lease, claimed):
            real_release = self.jobs._release

            def crash_release(db, attempt_lease):
                raise Crash('inside the local selection transaction')

            self.jobs._release = crash_release
            try:
                return original(self, lease, claimed)
            finally:
                self.jobs._release = real_release
                monkeypatch.setattr(gs.GraphStore, 'complete_local_selection', original)

        monkeypatch.setattr(gs.GraphStore, 'complete_local_selection', crashing)
        with pytest.raises(Crash):
            drive(w, controller, owner)
        assert w.store.jobs.get(job_of(w, 's'))['status'] == 'PROVISIONING'
    first_attempt = attempts(w, 'a')[0]['attempt_id']
    controller, owner = restart(w, advance=advance)
    drive(w, controller, owner)
    assert statuses(w) == {'a': 'COMPLETED', 'e': 'COMPLETED', 's': 'COMPLETED'} and run_row(w)['status'] == 'COMPLETED'
    assert w.dispatcher.training_calls == {'a': 1} and w.dispatcher.launches == {'a': 1, 'e': 1}
    assert w.dispatcher.produced == {'a': 1, 'e': 1}                     # unique artifacts
    assert w.store.reserved_budget(w.run_id) <= w.plan.max_estimated_cost_usd
    if crash == 'after_claim':   # the same attempt resumed, and the dead process's actor is fenced
        assert [row['attempt_id'] for row in attempts(w, 'a')] == [first_attempt]
        stale = old_pool.run(job_of(w, 'a')).exception()
        assert 'stale attempt lease' in str(stale) and w.dispatcher.launches['a'] == 1
    if crash == 'after_begin_launch':   # released before contact: uncounted, and requeued once
        assert [row['disposition'] for row in readiness.table(w, 'attempt_dispositions')] == ['released_before_contact']
    if crash == 'before_tracking_receipt':
        attempt = attempts(w, 'a')[-1]['attempt_id']
        assert len(outbox_tests.keyed(w.fake, f'attempt:{attempt}')) == 1   # one MLflow attempt run
    assert [row['status'] for row in events(w) if row['status'] != 'ACKNOWLEDGED'] == []


def test_late_result_never_retrains(tmp_path, monkeypatch):
    gc = api()
    w = world(tmp_path, monkeypatch, chain, max_attempts=3)
    w.dispatcher.script['a'] = ['uncertain']
    controller, owner = start(w)
    drive(w, controller, owner, until=lambda: bool(w.provider.recovers))
    a = job_of(w, 'a')
    settings = gc.ControllerSettings()
    for _ in range(3):   # reconcile keeps returning None, across intervals and restarts
        for _ in range(4):   # ticks well inside the owner TTL, two reconcile intervals in all
            w.clock.advance(settings.reconcile_interval_seconds / 2)
            controller.tick(owner)
        assert w.store.jobs.get(a)['status'] == 'FAILED' and w.store.jobs.holds_lock(w.store.jobs.current_lease(a))
        controller, owner = restart(w, f'controller-{len(w.provider.recovers)}')
        controller.tick(owner)
    reconciles = [call for call in w.dispatcher.calls if call == ('reconcile', 'a')]
    assert len(reconciles) >= 6
    assert len(attempts(w, 'a')) == 1 and node(w, 'b')['job_id'] is None   # no new attempt, no claim of the node
    w.dispatcher.deliver('a')
    for _ in range(2):
        w.clock.advance(settings.reconcile_interval_seconds / 2)
        controller.tick(owner)
    drive(w, controller, owner)
    assert statuses(w) == {'a': 'COMPLETED', 'b': 'COMPLETED'} and run_row(w)['status'] == 'COMPLETED'
    assert w.dispatcher.training_calls == {'a': 1, 'b': 1} and len(attempts(w, 'a')) == 1


def desktop_release(w, node_id, attempt_id):
    """Task 9.8: the desktop operator release (``graph_release_lock``) on this world's ledger and clock."""
    import tuning_factory.workflow_runs  # noqa: F401  (registers the run actions)
    from tuning_factory import desktop_workflows as wf
    context = wf.WorkflowContext(state_dir=w.store.state_dir, clock=w.clock)
    body = {'run_id': w.run_id, 'node_id': node_id, 'job_id': job_of(w, node_id), 'attempt_id': attempt_id,
            'confirm_execution_stopped': True}
    return wf.dispatch_action(context, 'graph_release_lock', body).model_dump(mode='json')


@pytest.mark.parametrize('release', ['cli', 'desktop'])
@pytest.mark.parametrize('record', [True, False])
def test_ambiguous_launch_waits_for_the_operator(tmp_path, monkeypatch, record, release):
    from typer.testing import CliRunner

    from tuning_factory.cli import app
    gc = api()
    w = world(tmp_path, monkeypatch, chain, max_attempts=3)
    controller, owner = start(w)
    if record:   # the launch's remote record exists, and no result ever comes
        w.dispatcher.script['a'] = ['uncertain']
        drive(w, controller, owner, until=lambda: bool(w.provider.recovers))
    else:   # the actor died after RUNNING, before its remote record: nothing can ever be collected
        w.dispatcher.crash['a'] = 'before_record'
        controller.tick(owner)
        with pytest.raises(Crash):
            w.pool.run_all()
        controller, owner = restart(w)
        controller.tick(owner)
    a = job_of(w, 'a')
    for _ in range(10):
        w.clock.advance(gc.ControllerSettings().reconcile_interval_seconds / 2)
        controller.tick(owner)
        w.pool.run_all()
    item = status_of(w, 'a', controller)
    assert item['uncertain'] and item['job_status'] == 'FAILED' and w.store.jobs.gpu_locked('existing_runpod:pod1')
    if record:
        # 8.5 review Minor 6: before the launch's time limit (900 s) plus a margin, a late result may still come, so
        # nobody is asked to release the lock yet.
        assert (item['awaiting_result'], item['needs_operator'], item['wait_reason']) == (True, False,
                                                                                          gc.AWAITING_RESULT)
        if release == 'desktop':   # 9.8: the desktop release is refused while a late result is plausible
            refused = desktop_release(w, 'a', attempts(w, 'a')[0]['attempt_id'])
            assert [(issue['code'], issue['path']) for issue in refused['issues']] == [('awaiting_result', '/node_id')]
            assert refused['status'] == 'conflict' and w.store.jobs.gpu_locked('existing_runpod:pod1')
        uncertain_at = max(row['timestamp'] for row in readiness.table(w, 'events', 'WHERE job_id=?', (a,)))
        assert item['result_deadline'] == pytest.approx(uncertain_at + 900 + gc.RESULT_MARGIN_SECONDS)
        controller, owner = restart(w, 'controller-late', advance=900 + gc.RESULT_MARGIN_SECONDS)
        controller.tick(owner)
        item = status_of(w, 'a', controller)
        assert (item['awaiting_result'], item['needs_operator'], item['wait_reason']) == (False, True, gc.RESULT_OVERDUE)
    else:   # nothing can ever be collected without the remote record
        assert (item['awaiting_result'], item['needs_operator'], item['wait_reason']) == (False, True,
                                                                                          gc.OPERATOR_CONFIRMATION)
    assert w.dispatcher.launches == ({'a': 1} if record else Counter()) and len(attempts(w, 'a')) == 1
    attempt = attempts(w, 'a')[0]['attempt_id']
    if release == 'cli':
        result = CliRunner().invoke(app, ['--state-dir', str(w.store.state_dir), 'release-lock', a, attempt,
                                          '--confirm-execution-stopped'])
        assert result.exit_code == 0, result.output
    else:   # the same store path from the desktop, with the same outcome
        result = desktop_release(w, 'a', attempt)
        assert result['status'] == 'ok', result
        assert result['data']['released'] == {'run_id': w.run_id, 'node_id': 'a', 'job_id': a, 'attempt_id': attempt}
    assert not w.store.jobs.gpu_locked('existing_runpod:pod1')
    drive(w, controller, owner)
    assert statuses(w) == {'a': 'FAILED', 'b': 'BLOCKED'} and run_row(w)['status'] == 'FAILED'
    assert w.store.attempt_disposition(a)['disposition'] == 'failed_before_submit'   # D4: never retried
    assert len(attempts(w, 'a')) == 1 and w.dispatcher.training_calls['a'] <= 1


def test_takeover_fences_the_old_actor_before_reconcile(tmp_path, monkeypatch):
    gs = readiness.api()
    w = world(tmp_path, monkeypatch, chain)
    w.dispatcher.script['a'] = ['uncertain']
    controller, owner = start(w)
    controller.tick(owner)
    a = job_of(w, 'a')
    old = w.store.jobs.current_lease(a)   # the dispatch thread's own attempt lease
    order = []
    takeover = gs.GraphStore.takeover_attempt

    def spy_takeover(self, lease, job_id):
        order.append('takeover_attempt')
        return takeover(self, lease, job_id)

    reconcile = w.dispatcher.reconcile

    def spy_reconcile(job_id, executor):
        order.append('reconcile')
        return reconcile(job_id, executor)

    monkeypatch.setattr(gs.GraphStore, 'takeover_attempt', spy_takeover)
    monkeypatch.setattr(w.dispatcher, 'reconcile', spy_reconcile)
    w.pool.run_all()
    controller.tick(owner)
    assert order[:2] == ['takeover_attempt', 'reconcile']
    for stale in (lambda: w.store.jobs.heartbeat(old), lambda: w.store.jobs.transition(old, 'RUNNING')):
        with pytest.raises(ValueError, match='stale attempt lease'):
            stale()


def test_reconcile_is_called_only_for_lock_holding_attempts(tmp_path, monkeypatch):
    w = world(tmp_path, monkeypatch, lambda r: [train(r, 'a'), train(r, 'f'), train(r, 'u')], max_attempts=1)
    w.dispatcher.script.update(f=['fail_remote'], u=['uncertain'])
    w.dispatcher.final_race.add('u')   # its lock is released between the controller's read and reconcile
    controller, owner = start(w)
    controller.tick(owner)
    w.pool.run_all()
    controller, owner = restart(w)   # no cached outcome: the completed attempt is rebuilt by outputs_for
    drive(w, controller, owner)
    calls = w.dispatcher.calls
    assert ('reconcile', 'a') not in calls and ('outputs_for', 'a') in calls   # final COMPLETED: never reconciled
    assert ('reconcile', 'f') not in calls                                    # final FAILED: classified directly
    assert calls.count(('reconcile', 'u')) == 1                               # 'attempt is final': settled as final
    assert statuses(w) == {'a': 'COMPLETED', 'f': 'FAILED', 'u': 'FAILED'}
    dispositions = {row['node_id']: row['disposition'] for row in readiness.table(w, 'attempt_dispositions')}
    assert dispositions == {'f': 'remote_failed', 'u': 'failed_before_submit'}


# -- 13-14. inspected targets and the cache-seeding window ---------------------------------------

def test_inspected_targets_from_outcomes_are_persisted(tmp_path, monkeypatch):
    w = world(tmp_path, monkeypatch, lambda r: [
        materialize(r, 'full', r.first), train(r, 'b', 'full'),
        {'kind': 'materialize', 'id': 'c', 'label': 'Materialize', 'model': {'node_id': 'b', 'output_name': 'model'},
         'executor_profile_ids': ['p1']}])
    controller, owner = start(w)
    drive(w, controller, owner)
    assert statuses(w) == {'full': 'COMPLETED', 'b': 'COMPLETED', 'c': 'COMPLETED'}
    (saved,) = [outputs for evidence, outputs in w.store.ledger(w.run_id) if evidence.node_id == 'full']
    ref = saved.model.model
    assert saved.inspected_targets == (inputs.evidence(ref),)            # from the outcome, verified, persisted
    operation = w.store.jobs.get(job_of(w, 'c'))['manifest']['operation']
    assert w.store.artifacts.verify(ModelArtifactRef.model_validate(operation['model'])).effective_base == ref
    assert inputs.evidence(ref) in w.store.bindings_for(w.run_id, 'c').base_targets   # what c compiled with


def test_cache_seeding_window_keeps_the_resource_exclusive_and_is_not_retried(tmp_path, monkeypatch):
    w = world(tmp_path, monkeypatch, lambda r: [train(r, 'x', profiles=('p1',)), train(r, 'y', profiles=('p1',)),
                                                 train(r, 'z', profiles=('p2',))],
              destinations=pods('pod1', 'pod2'), max_concurrent=3)
    controller, owner = start(w)
    controller.tick(owner)
    x, z = job_of(w, 'x'), job_of(w, 'z')
    assert set(w.pool.pending) == {x, z}   # y waits for pod1
    w.pool.run(z)
    seen = {}

    def while_seeding():
        assert not w.store.jobs.gpu_locked('existing_runpod:pod1')   # x released pod1 before seeding (F9)
        seen['report'] = controller.tick(owner)                      # a tick while x's thread still seeds

    w.dispatcher.seed['x'] = while_seeding
    w.pool.run(x)
    assert job_of(w, 'y') not in seen['report'].claimed   # pod1 stays busy until x's thread returned (D8)
    assert node(w, 'y')['wait_reason'] == 'waiting for destination existing_runpod:pod1'
    report = controller.tick(owner)
    assert job_of(w, 'y') in report.claimed
    w.dispatcher.crash['y'] = 'seeding'   # the process dies while y seeds the cache
    with pytest.raises(Crash):
        w.pool.run_all()
    controller, owner = restart(w)
    drive(w, controller, owner)
    assert set(statuses(w).values()) == {'COMPLETED'} and w.dispatcher.launches == {'x': 1, 'y': 1, 'z': 1}
    publishes = [executor.calls.count('publish_cached_artifact') for executor in w.provider.executors]
    assert sum(publishes) == 2   # x and z seeded; y crashed while seeding, and D8 never retries it
    assert status_of(w, 'y')['cache_seed'] == 'missing' and status_of(w, 'x')['cache_seed'] == 'published'


# -- 15-16. tracking repair end to end -----------------------------------------------------------

def test_upload_failure_restart_resave_trains_once(tmp_path, monkeypatch):
    w = world(tmp_path, monkeypatch, chain)
    real_create = w.fake.create_run
    lost = []

    def create_run(experiment_id, start_time=None, tags=None, run_name=None):
        run = real_create(experiment_id, start_time=start_time, tags=tags, run_name=run_name)
        if (tags or {}).get('factory.tracking_key', '').startswith('attempt:') and not lost:
            lost.append(run.info.run_id)
            w.fake.fail['log_artifact'] = [ConnectionError('artifact upload failed')]   # the next upload fails
            raise Crash('the attempt run was created, then the controller died')
        return run

    monkeypatch.setattr(w.fake, 'create_run', create_run)
    controller, owner = start(w)
    with pytest.raises(Crash):
        drive(w, controller, owner)
    assert node(w, 'b')['job_id'] is None
    controller, owner = restart(w, advance=700)
    drive(w, controller, owner, each=lambda: w.clock.advance(31))   # past the evidence upload's 30 s backoff
    assert statuses(w) == {'a': 'COMPLETED', 'b': 'COMPLETED'} and w.dispatcher.training_calls == {'a': 1, 'b': 1}
    attempt = attempts(w, 'a')[0]['attempt_id']
    (run,) = outbox_tests.keyed(w.fake, f'attempt:{attempt}')   # one MLflow attempt run, adopted
    assert run.info.run_id == lost[0]
    receipt = json.loads(outbox_tests.event(w, f'attempt:{attempt}')['remote_identity'])
    assert receipt == {'run_id': lost[0], 'adopted': True, 'duplicates': []}
    assert 'lineage/manifest.json' in run.artifacts


def test_registration_receipt_lost_then_adopted_end_to_end(tmp_path, monkeypatch):
    w = world(tmp_path, monkeypatch, lambda r: [train(r, 'a')])
    w.fake.commit_then_fail['create_model_version'] = Crash('the version was created, then the controller died')
    controller, owner = start(w)
    with pytest.raises(Crash):
        drive(w, controller, owner)
    assert len(w.fake.versions) == 1
    controller, owner = restart(w, advance=700)
    drive(w, controller, owner)
    (registration,) = [row for row in events(w) if row['kind'] == 'model_registration']
    assert registration['status'] == 'ACKNOWLEDGED' and json.loads(registration['remote_identity'])['adopted']
    assert len(w.fake.versions) == 1 and outbox_tests.count(w.fake, 'create_model_version') == 1
    assert status_of(w, 'a')['registration_status'] == 'registered'


# -- 17-19. budget, changed destinations, cross-provider quarantine ------------------------------

def test_budget_reservation_and_bounded_retry(tmp_path, monkeypatch):
    w = world(tmp_path, monkeypatch, lambda r: [train(r, 'a')], max_attempts=3, backoff=60)
    w.dispatcher.script['a'] = ['fail_remote', 'fail_remote', 'fail_remote']
    controller, owner = start(w)
    approved = w.plan.max_estimated_cost_usd
    for expected in (1, 2):
        drive(w, controller, owner, until=lambda: len(readiness.table(w, 'attempt_dispositions')) == expected)
        assert node(w, 'a')['status'] == 'RUNNING' and node(w, 'a')['retry_not_before'] == w.clock() + 60
        controller.tick(owner)
        assert len(attempts(w, 'a')) == expected                      # no retry before the backoff
        assert status_of(w, 'a')['wait_reason'] == f'waiting for the retry backoff (attempt {expected + 1} of 3)'
        w.clock.advance(30)
        controller.tick(owner)
        assert len(attempts(w, 'a')) == expected
        w.clock.advance(30)
    drive(w, controller, owner)
    assert statuses(w) == {'a': 'FAILED'} and len(attempts(w, 'a')) == 3   # bounded by max_attempts
    (run,) = api().graph_status(w.store, w.run_id, now=w.clock())['runs']
    assert run['reserved_cost_usd'] == pytest.approx(1.5) and run['approved_cost_usd'] == approved
    assert w.store.reserved_budget(w.run_id) <= approved * (1 + 1e-9)
    assert status_of(w, 'a')['counted_attempts'] == 3


def test_a_budget_exhausted_by_history_is_named(tmp_path, monkeypatch):
    """8.2 review M2: reservations the 8.1 backfill made for historical attempts can exhaust an old run."""
    gs = readiness.api()
    w = world(tmp_path, monkeypatch, lambda r: [train(r, 'a'), train(r, 'h')])
    with w.store.jobs.connection() as db:   # what the migration leaves for an unpriced pre-Task-8 attempt of h
        db.execute(gs.RESERVE, ('f' * 32, w.run_id, 'h', 'historical', 'existing_runpod:pod1',
                                w.plan.max_estimated_cost_usd, 'RESERVED', w.clock()))
        db.execute("UPDATE nodes SET wait_reason=? WHERE node_id='h'", (gs.UNPRICED_ATTEMPT,))
    controller, owner = start(w)
    for _ in range(3):
        report = controller.tick(owner)
        assert report.claimed == ()
    reason = status_of(w, 'a', controller)['wait_reason']
    assert reason.startswith('approved budget exhausted') and gs.UNPRICED_ATTEMPT in reason
    assert 'historical attempts' in reason and w.provider.resolves == []   # never contacted
    (run,) = controller.status()['runs']
    assert run['budget_exhausted'] and run['unpriced_attempts'] == 1


@pytest.mark.parametrize('change', ['profile', 'price'])
def test_profile_or_price_change_never_runs_the_old_plan(tmp_path, monkeypatch, change):
    w = world(tmp_path, monkeypatch, lambda r: [train(r, 'a')])
    if change == 'profile':
        w.provider.refuse['p1'] = ValueError(PROFILE_CHANGED)
    else:
        w.provider.price['p1'] = 3.0   # the live price is above the pinned 2.0
    controller, owner = start(w)
    for _ in range(3):
        controller.tick(owner)
        w.pool.run_all()
    assert readiness.table(w, 'job_attempts') == [] and w.dispatcher.calls == []
    assert 're-plan' in node(w, 'a')['wait_reason'] and status_of(w, 'a')['attempts'] == 0
    assert w.store.reserved_budget(w.run_id) == 0


def test_mixed_provider_plan_serializes_by_quarantine(tmp_path, monkeypatch):
    gc = api()
    destinations = pods('pod1') + ssh_destinations()[:1]
    w = world(tmp_path, monkeypatch, lambda r: [train(r, 'x', profiles=('p1',)), evaluate(r, 'y', r.first, ('s1',))],
              destinations=destinations, max_concurrent=2)
    controller, owner = start(w)
    controller.tick(owner)
    assert list(w.pool.pending) == [job_of(w, 'x')]
    assert node(w, 'y')['wait_reason'] == gc.CROSS_PROVIDER
    w.pool.run_all()
    drive(w, controller, owner, each=lambda: (len(w.pool.pending) <= 1) or pytest.fail('concurrent'))
    assert statuses(w) == {'x': 'COMPLETED', 'y': 'COMPLETED'}


# -- 20-24. dry runs, status, the real runner, run_forever, legacy -------------------------------

def simulated_diamond(r):
    """a -> (b, c -> ec) -> d: an evaluation of a, and of its continuation c, joined by a no-winner selection
    (simulated outputs are never selection-eligible). Two evaluations of one model would be duplicate candidates, and
    compile refuses simulated merge inputs (the 8.4 merge gap), so the join is the selection."""
    return [train(r, 'a'), evaluate(r, 'b', 'a'), train(r, 'c', 'a'), evaluate(r, 'ec', 'c'),
            select('d', ('b', 'ec'))]


@pytest.mark.parametrize('variant', ['straight', 'restart', 'cancel'])
def test_simulated_diamond_dry_run_end_to_end(tmp_path, monkeypatch, isolated, variant):
    gc = api()
    from tuning_factory import simulated_dispatch as sd
    w = world(tmp_path, monkeypatch, simulated_diamond, simulation=True)
    dispatcher = sd.SimulatedOperationDispatcher(w.store, root=w.store.state_dir)
    executes = Counter()
    real_execute = sd.SimulatedWorker.execute

    def execute(self, operation, *args, **kwargs):
        executes[operation.lineage.node_id] += 1
        return real_execute(self, operation, *args, **kwargs)

    monkeypatch.setattr(sd.SimulatedWorker, 'execute', execute)
    if variant == 'restart':   # the process dies after a's result was published, while freezing
        real_outputs = sd.SimulatedOperationDispatcher._outputs

        def outputs(self, job, *args, **kwargs):
            if job['graph_node_id'] == 'a' and not executes.get('crashed'):
                executes['crashed'] += 1
                raise Crash('while freezing the simulated output')
            return real_outputs(self, job, *args, **kwargs)

        monkeypatch.setattr(sd.SimulatedOperationDispatcher, '_outputs', outputs)
    mlflow_calls = len(w.fake.calls)   # the tracker's own construction only
    controller, owner = start(w, dispatcher=dispatcher, provider=None)
    if variant == 'restart':
        controller.tick(owner)
        with pytest.raises(Crash):
            w.pool.run_all()
        controller, owner = restart(w, dispatcher=dispatcher, provider=None)
    if variant == 'cancel':   # canceled while a branch is claimed but not launched
        for _ in range(30):
            controller.tick(owner)
            if any(job_of(w, item) in w.pool.pending for item in ('b', 'c')):
                break
            w.pool.run_all()
        w.store.cancel(w.run_id)
    drive(w, controller, owner)
    assert isolated == []   # zero socket connects
    assert len(w.fake.calls) == mlflow_calls   # simulations never touch MLflow (D7)
    if variant == 'cancel':
        assert statuses(w)['a'] == 'COMPLETED' and set(statuses(w).values()) == {'COMPLETED', 'CANCELED'}
        assert statuses(w)['d'] == 'CANCELED' and run_row(w)['status'] == 'CANCELED'
        assert executes['ec'] == 0 and sum(executes.values()) <= 2
        return
    assert set(statuses(w).values()) == {'COMPLETED'}
    assert status_of(w, 'd')['selection'] == 'no_winner' and run_row(w)['status'] == 'COMPLETED'
    assert {key: executes[key] for key in ('a', 'b', 'c', 'ec')} == {'a': 1, 'b': 1, 'c': 1, 'ec': 1}
    receipts = list((w.store.state_dir / 'tracking' / w.run_id).glob('*.json'))
    assert len(receipts) == len(events(w)) and {row['status'] for row in events(w)} == {'ACKNOWLEDGED'}
    assert all(lease.startswith('simulation:') for lease in
               {row['resource_id'] for row in readiness.table(w, 'job_attempts')} - {'local:' + w.run_id})
    assert isinstance(controller.status()['controller']['in_flight'], list) and gc.graph_status


def test_status_is_db_only_and_secret_free(tmp_path, monkeypatch):
    gc = api()
    gs = readiness.api()
    monkeypatch.setenv('MLFLOW_TRACKING_TOKEN', SECRET)
    w = world(tmp_path, monkeypatch, lambda r: [train(r, 'a'), train(r, 'b', 'a'), train(r, 'f')], max_attempts=1,
              auth_mode='token')
    w.dispatcher.script['f'] = ['fail_remote']   # its error text carries the secret and a remote path
    w.fake.fail['log_artifact'] = [ConnectionError(f'upload refused: Bearer {SECRET}')] * 3
    controller, owner = start(w)
    for _ in range(4):
        controller.tick(owner)
        w.pool.run_all()
    fresh = gs.GraphStore(w.store.jobs.path, clock=w.clock)   # another process: no controller object
    status = gc.graph_status(fresh, now=w.clock())
    text = json.dumps(status)
    assert SECRET not in text and '/workspace/' not in text and 'settings' not in text
    assert status['controller'] == {'owner_id': 'controller-a', 'state': 'running',
                                    'heartbeat_age_seconds': status['controller']['heartbeat_age_seconds']}
    (run,) = status['runs']
    nodes = {item['node_id']: item for item in run['nodes']}
    assert (nodes['f']['status'], nodes['f']['error'].count('[redacted]') + nodes['f']['error'].count('[path]')) == (
        'FAILED', 2)
    assert run['tracking']['graph'] == 'retrying' and nodes['a']['tracking_status'] == 'pending'
    errors = [row['last_error'] for row in events(w) if row['last_error']]
    assert errors and all(SECRET not in error and '[redacted]' in error for error in errors)
    assert nodes['b']['wait_reason'] == 'waiting for tracking of a'
    assert run['tracking']['events']['PENDING'] >= 1 and run['simulation'] is False
    assert set(nodes['a']) >= {'node_id', 'label', 'kind', 'status', 'job_status', 'attempt_id', 'attempts',
                               'counted_attempts', 'max_attempts', 'resource_id', 'destination_profile_id',
                               'uncertain', 'needs_operator', 'wait_reason', 'error', 'retry_at', 'tracking_status',
                               'registration_status', 'output', 'selection', 'cache_seed'}
    assert nodes['a']['destination_profile_id'] == 'p1' and nodes['a']['resource_id'] == 'existing_runpod:pod1'
    w.clock.advance(61)
    assert gc.graph_status(fresh, now=w.clock())['controller']['state'] == 'stale'
    fresh.release_owner(owner)
    assert gc.graph_status(fresh, now=w.clock())['controller']['state'] == 'absent'
    assert SECRET not in json.dumps(controller.status())


def host_world(tmp_path, monkeypatch, nodes, **plan):
    """``world`` with the real ``OperationAttemptRunner`` and the real ``SSHExecutor`` on a fake GPU host
    (``test_operation_runner.gpu_host``): ``w.runner``, ``w.host``, ``w.engine_calls`` and ``w.host_provider``."""
    from test_operation_runner import gpu_host

    from tuning_factory.attempt_runner import OperationAttemptRunner
    w = world(tmp_path, monkeypatch, nodes, **plan)
    w.engine_calls = []
    w.host, executor = gpu_host(tmp_path, monkeypatch, w.engine_calls)

    class HostProvider(FakeProvider):
        @contextmanager
        def resolve(self, profile_id, approved_revision, *, pinned, runtime_profile=None, model_ops=False):
            self.resolves.append(profile_id)
            _, facts = self.destinations[profile_id]
            observed = facts.model_copy(update={'observed_at': datetime.fromtimestamp(self.clock(), tz=timezone.utc)})
            yield ResolvedDestination(profile=pinned, observation=observed, executor=executor)

        @contextmanager
        def recover(self, profile_id, approved_revision, *, pinned, pinned_facts, manifest_sha256,
                    runtime_profile=None):
            self.recovers.append(profile_id)
            yield executor

    w.runner = OperationAttemptRunner(w.store, root=w.store.state_dir)
    w.host_provider = HostProvider(pods('pod1'), w.clock)
    return w


def test_end_to_end_with_the_real_attempt_runner(tmp_path, monkeypatch):
    w = host_world(tmp_path, monkeypatch, lambda r: [train(r, 'a'), evaluate(r, 'e', 'a')])
    controller, owner = start(w, dispatcher=w.runner, provider=w.host_provider)
    drive(w, controller, owner)
    assert statuses(w) == {'a': 'COMPLETED', 'e': 'COMPLETED'} and run_row(w)['status'] == 'COMPLETED'
    calls = w.engine_calls
    assert calls.count('train.bound.yaml') == 1 and calls.count('predict.yaml') == 1   # one launch per node
    for node_id in ('a', 'e'):
        assert status_of(w, node_id)['tracking_status'] == 'saved'
    assert status_of(w, 'a')['cache_seed'] == 'published' and status_of(w, 'a')['registration_status'] == 'registered'


@pytest.mark.parametrize('when', ['while_running', 'while_uncertain'])
@pytest.mark.parametrize('scope', ['run', 'job'])
def test_a_cancel_wins_over_a_late_completion_through_reconcile(tmp_path, monkeypatch, scope, when):
    """Task 11.5 R1 I-1 with the real runner: the run (or ``factory cancel <job>``) is canceled while ``a`` runs, or once
    it is FAILED-uncertain, its start response is lost, and a late COMPLETED result arrives. ``a`` ends CANCELED, its
    child is never claimed, nothing is registered and ``a`` trained once."""
    w = host_world(tmp_path, monkeypatch, lambda r: [train(r, 'a'), evaluate(r, 'e', 'a')])
    w.host.mode = 'lose_start_response'

    def cancel():
        if scope == 'run':
            w.store.cancel(w.run_id)
        else:
            w.store.jobs.cancel(job_of(w, 'a'))

    if when == 'while_running':
        launch = w.host.launch

        def canceled_while_running(args):
            cancel()
            return launch(args)

        w.host.launch = canceled_while_running
    controller, owner = start(w, dispatcher=w.runner, provider=w.host_provider)
    controller.tick(owner)
    w.pool.run_all()
    a = job_of(w, 'a')
    assert w.store.jobs.get(a)['status'] == 'FAILED' and w.store.jobs.gpu_locked('existing_runpod:pod1')
    if when == 'while_uncertain':
        cancel()
    assert w.store.jobs.get(a)['cancel_requested'] == 1
    w.host.finish()   # the late COMPLETED result
    drive(w, controller, owner)
    assert statuses(w) == {'a': 'CANCELED', 'e': 'CANCELED' if scope == 'run' else 'BLOCKED'}
    assert run_row(w)['status'] == ('CANCELED' if scope == 'run' else 'FAILED')
    assert job_of(w, 'e') is None   # the child was never compiled or claimed
    assert [row for row in events(w) if row['kind'] == 'model_registration'] == []
    assert readiness.table(w, 'node_outputs') == [] and w.store.attempt_disposition(a)['disposition'] == 'canceled'
    assert w.engine_calls == ['train.bound.yaml'] and len(w.host.launches) == 1   # trained once, launched once


def test_run_forever_stops_claiming_and_releases_owner(tmp_path, monkeypatch):
    gc = api()
    w = world(tmp_path, monkeypatch, two_pods_two_trains, destinations=pods('pod1', 'pod2'), max_concurrent=1)
    settings = gc.ControllerSettings(tick_seconds=0.01, lease_heartbeat_seconds=0.01)
    controller, owner = start(w, settings=settings)
    controller.tick(owner)
    x, y = job_of(w, 'x'), job_of(w, 'y')
    assert list(w.pool.pending) == [x]
    stop = threading.Event()
    stop.set()
    errors = []
    thread = threading.Thread(target=lambda: errors.append(None) or controller.run_forever(stop))
    thread.start()
    w.pool.run(x)   # the in-flight attempt drains
    thread.join(60)
    assert not thread.is_alive()
    assert node(w, 'x')['status'] == 'COMPLETED' and w.store.jobs.get(y)['status'] == 'QUEUED'   # no new claim
    assert w.dispatcher.launches == {'x': 1}
    assert w.store.owner_state()['expires'] == 0                      # release_owner ran
    successor, successor_owner = start(w, 'controller-b')              # a new controller acquires at once
    drive(w, successor, successor_owner)
    assert w.dispatcher.launches == {'x': 1, 'y': 1} and run_row(w)['status'] == 'COMPLETED'


def test_legacy_work_and_graph_controller_coexist(tmp_path, monkeypatch):
    from test_graph_store import queued

    from tuning_factory.runner import Factory
    w = world(tmp_path, monkeypatch, lambda r: [train(r, 'a')])
    controller, owner = start(w)
    controller.tick(owner)
    factory = Factory(w.store.state_dir)
    assert factory.work() is None   # Factory.work never claims a graph job
    legacy = queued(w.store.jobs, 'legacy')
    assert w.store.jobs.claim('existing_runpod:pod9', max_concurrent=1) is None   # D2: graph attempts count
    w.pool.run_all()
    drive(w, controller, owner)
    claimed = w.store.jobs.claim('existing_runpod:pod9', max_concurrent=1)
    assert claimed['id'] == legacy['id']
    assert w.store.jobs.get(job_of(w, 'a'))['attempts'] == 1


# -- hand-off notes and review fixes --------------------------------------------------------------

def test_owner_release_and_expiry_fence_run_leases_at_once(tmp_path, monkeypatch):
    """8.2 review M3: a run lease bound to an owner is valid only while that owner lease is (D14)."""
    w = world(tmp_path, monkeypatch, chain)
    store, clock = w.store, w.clock
    owner = store.acquire_owner('controller-a', ttl_seconds=30)
    lease = store.acquire_controller(w.run_id, ttl_seconds=600, owner=owner)
    store.heartbeat_controller(lease, 600)
    store.release_owner(owner)
    for call in (lambda: store.heartbeat_controller(lease, 600), lambda: store.prepare_ready(lease, node_ids=['a']),
                 lambda: store.finalize_run(lease)):
        with pytest.raises(ValueError, match='stale controller lease'):
            call()
    second = store.acquire_owner('controller-b', ttl_seconds=30)
    lease = store.acquire_controller(w.run_id, ttl_seconds=600, owner=second)
    clock.advance(31)   # the owner expired; its run lease has not
    with pytest.raises(ValueError, match='stale controller lease'):
        store.heartbeat_controller(lease, 600)
    third = store.acquire_owner('controller-c', ttl_seconds=600)
    store.acquire_controller(w.run_id, ttl_seconds=600, owner=third)   # replaced at once
    legacy = readiness.second_run(w)   # the pre-Task-8 API: no owner
    store.release_owner(third)
    store.heartbeat_controller(legacy, 600)   # an owner-less run lease is unaffected


def test_ledger_integrity_errors_fail_nothing(tmp_path, monkeypatch):
    """8.2 review M-2: a run-wide record that no longer verifies holds the run; only a node's own compile refusal
    fails that node."""
    gs = readiness.api()
    w = world(tmp_path, monkeypatch, lambda r: [materialize(r, 'full', r.first), evaluate(r, 'e', 'full'),
                                                 evaluate(r, 'g', 'full')], max_concurrent=1)
    controller, owner = start(w)
    drive(w, controller, owner, until=lambda: node(w, 'full')['status'] == 'COMPLETED'
          and status_of(w, 'full')['tracking_status'] == 'saved')
    with w.store.jobs.connection() as db:   # the persisted inspected targets of 'full' are tampered with
        payload = json.loads(db.execute("SELECT outputs FROM node_outputs WHERE node_id='full'").fetchone()[0])
        payload['inspected_targets'][0]['modules'][0]['output_dimension'] = 3
        db.execute("UPDATE node_outputs SET outputs=? WHERE node_id='full'", (canonical(payload).decode(),))
    for _ in range(3):
        controller.tick(owner)
        w.pool.run_all()
    pending = [item for item in ('e', 'g') if node(w, item)['status'] == 'PENDING']
    assert pending and all(node(w, item)['error'] is None for item in pending)   # nothing FAILED
    item = status_of(w, pending[0], controller)
    assert item['wait_reason'].startswith(gs.LEDGER_INTEGRITY) and item['needs_operator']
    assert 'inspected base dimensions differ' in item['wait_reason']
    # A node-specific compile refusal still fails that node alone and blocks its subtree.
    original = gs.compile_node

    def refuse(plan, node_id, bindings):
        if node_id == 'b':
            raise ValueError('dataset evidence changed after planning')
        return original(plan, node_id, bindings)

    other = world(tmp_path / 'node', monkeypatch, chain)
    monkeypatch.setattr(gs, 'compile_node', refuse)
    controller, owner = start(other)
    drive(other, controller, owner)
    assert statuses(other) == {'a': 'COMPLETED', 'b': 'FAILED'}
    assert node(other, 'b')['error'] == 'dataset evidence changed after planning'


def test_long_uploads_keep_the_owner_and_the_claim_alive(tmp_path, monkeypatch):
    """Hand-off note 6 and 8.3 review Important 1: a heartbeat renews the owner lease, the run leases and the claim
    being sent while one upload or create outlasts their TTLs; the claim TTL is configurable."""
    gc = api()
    gs = readiness.api()
    w = world(tmp_path, monkeypatch, lambda r: [train(r, 'a')])
    settings = gc.ControllerSettings(lease_ttl_seconds=60, lease_heartbeat_seconds=0.01,
                                     tracking_claim_ttl_seconds=300)
    controller, owner = start(w, settings=settings)
    assert controller.outbox.claim_ttl == 300
    renewed = threading.Event()
    renew = gs.GraphStore.renew_owner

    def spy(self, *args, **kwargs):
        renew(self, *args, **kwargs)
        renewed.set()

    monkeypatch.setattr(gs.GraphStore, 'renew_owner', spy)
    real_versions = w.fake.create_model_version
    duplicates = []

    def slow_create(*args, **kwargs):   # one create lasting 2.5 owner TTLs and more than the claim TTL
        for _ in range(10):
            w.clock.advance(40)
            renewed.clear()
            assert renewed.wait(30)
        # Meanwhile another publisher pass in this process finds nothing to claim: the claim is still ours.
        duplicates.append(controller.publisher.outbox.claim_pending(owner))
        assert os.environ.get('MLFLOW_HTTP_REQUEST_MAX_RETRIES') == '0'   # never re-sent by the transport
        assert os.environ.get('MLFLOW_HTTP_REQUEST_TIMEOUT') == '60'
        return real_versions(*args, **kwargs)

    monkeypatch.setattr(w.fake, 'create_model_version', slow_create)
    drive(w, controller, owner)
    assert duplicates == [()] and len(w.fake.versions) == 1
    assert 'MLFLOW_HTTP_REQUEST_MAX_RETRIES' not in os.environ   # restored after the call
    controller.tick(owner)   # the owner lease survived the long upload
    with pytest.raises(ValueError, match='controller already running'):
        w.store.acquire_owner('controller-b')


def test_settings_router_and_test_hooks(tmp_path, monkeypatch):
    gc = api()
    gs = readiness.api()
    for bad in ({'lease_ttl_seconds': 0}, {'tick_seconds': float('inf')}, {'max_dispatch_threads': 0},
                {'lease_heartbeat_seconds': 40}, {'tracking_claim_ttl_seconds': 30}):
        with pytest.raises(ValueError):
            gc.ControllerSettings(**bad)
    w = world(tmp_path, monkeypatch, chain)
    real, simulated = SimpleNamespace(graph=w.store), SimpleNamespace(graph=w.store)
    calls = []
    real.failure_kind = lambda job_id: calls.append(('real', job_id))
    simulated.failure_kind = lambda job_id: calls.append(('simulated', job_id))
    router = gc.DispatchRouter(real, simulated)
    controller, owner = start(w, dispatcher=router)
    controller.tick(owner)
    router.failure_kind(job_of(w, 'a'))
    assert calls == [('real', job_of(w, 'a'))]   # routed by the manifest's lineage.simulation
    with pytest.raises(TypeError):
        gc.DispatchRouter(None, None)
    with pytest.raises(ValueError, match='test hook'):   # 8.3 review Minor 6: production never opens the gate
        w.store.acknowledge_tracking(w.store.jobs.current_lease(job_of(w, 'a')))
    assert gs.GraphStore.test_acknowledgement is False
    lazy = gc.controller_tracker('https://mlflow.invalid')
    assert lazy.bounded_requests == (0, 60)   # creates are never re-sent by the transport (8.5 review Important 2)


def test_changed_evidence_is_named_for_the_operator(tmp_path, monkeypatch):
    """8.3 review Minor 2: re-arming cannot repair changed evidence, so the waiting child and the status say what to
    do instead."""
    outbox_api = outbox_tests.api()
    w = world(tmp_path, monkeypatch, chain)
    controller, owner = start(w)
    process = controller.publisher.process
    controller.publisher.process = lambda *args, **kwargs: 0   # record a's outputs, but send nothing yet
    drive(w, controller, owner, until=lambda: node(w, 'a')['status'] == 'COMPLETED')
    controller.publisher.process = process
    manifest = w.store.state_dir / 'jobs' / job_of(w, 'a') / 'manifest.json'
    manifest.write_bytes(manifest.read_bytes().replace(b'"train"', b'"tampered"', 1))
    for _ in range(3):
        controller.tick(owner)
    item = status_of(w, 'a', controller)
    assert item['tracking_status'] == 'failed' and item['tracking_action'] == outbox_api.EVIDENCE_CHANGED_ACTION
    reason = node(w, 'b')['wait_reason']
    assert reason.startswith('waiting for tracking of a (tracking failed: ') and 're-arming cannot repair' in reason


def test_a_simulated_attempt_stopped_while_running_is_terminal(tmp_path, monkeypatch):
    """8.4 review Important 1 through the controller: a simulated actor that died while RUNNING without a result is
    settled by reconcile's own FAILED result as failed_before_submit: counted and terminal, never re-run (D4)."""
    from tuning_factory import simulated_dispatch as sd
    w = world(tmp_path, monkeypatch, chain, simulation=True, max_attempts=3)
    dispatcher = sd.SimulatedOperationDispatcher(w.store, root=w.store.state_dir)
    executes = []
    real_execute = sd.SimulatedWorker.execute

    def execute(self, operation, *args, **kwargs):
        executes.append(operation.lineage.node_id)
        if len(executes) == 1:
            raise Crash('the simulated actor died while running')
        return real_execute(self, operation, *args, **kwargs)

    monkeypatch.setattr(sd.SimulatedWorker, 'execute', execute)
    controller, owner = start(w, dispatcher=dispatcher, provider=None)
    controller.tick(owner)
    with pytest.raises(Crash):
        w.pool.run_all()
    controller, owner = restart(w, dispatcher=dispatcher, provider=None)
    drive(w, controller, owner)
    assert statuses(w) == {'a': 'FAILED', 'b': 'BLOCKED'} and run_row(w)['status'] == 'FAILED'
    assert w.store.attempt_disposition(job_of(w, 'a'))['disposition'] == 'failed_before_submit'
    assert executes == ['a'] and len(attempts(w, 'a')) == 1   # max_attempts=3, and still never re-run


def test_the_seeding_window_covers_aliases_and_other_providers(tmp_path, monkeypatch):
    """D8 under the same rules as the DB locks: while x's thread seeds (its lock already released), neither a server
    sharing one of its GPU UUIDs nor another provider's destination (D3) may be claimed."""
    destinations = shared_uuid_destinations() + pods('pod1')
    w = world(tmp_path, monkeypatch, lambda r: [evaluate(r, 'x', r.first, ('s1',)), evaluate(r, 'y', r.second, ('s4',)),
                                                 train(r, 'z', profiles=('p1',))],
              destinations=destinations, max_concurrent=3)
    controller, owner = start(w)
    controller.tick(owner)
    x = job_of(w, 'x')
    assert list(w.pool.pending) == [x]
    seen = {}

    def while_seeding():
        assert not w.store.jobs.gpu_locked('ssh_server:' + 'a' * 64)
        seen['report'] = controller.tick(owner)

    w.dispatcher.seed['x'] = while_seeding
    w.pool.run(x)
    assert seen['report'].claimed == ()   # neither the shared-UUID server nor the RunPod Pod while x seeds
    reasons = {item: node(w, item)['wait_reason'] for item in ('y', 'z')}
    assert reasons['z'] == api().CROSS_PROVIDER and reasons['y'].startswith('waiting for destination ssh_server:')
    drive(w, controller, owner, each=lambda: len(w.pool.pending) <= 1 or pytest.fail('concurrent'))
    assert set(statuses(w).values()) == {'COMPLETED'}


# -- 8.5 review fixes ------------------------------------------------------------------------------

@pytest.mark.parametrize('runtime', ['same_runtime', 'other_runtime'])
def test_a_claim_never_goes_to_another_jobs_destination(tmp_path, monkeypatch, runtime):
    """Important 1: p1 and p3 are two profiles of pod1. x is pinned to p1, whose profile changed after approval; y is
    pinned to p3. Opening p3 for y must claim y, never x (x would run the old plan through another profile), and a
    job of another runtime is never claimed and returned (the ledger would grow every tick)."""
    w = world(tmp_path, monkeypatch, lambda r: [
        train(r, 'x', profiles=('p1',)) if runtime == 'same_runtime' else materialize(r, 'x', r.first, ('p1',)),
        train(r, 'y', profiles=('p3',))], destinations=pods('pod1', aliases=(('p3', 'pod1'),)))
    w.provider.refuse['p1'] = ValueError(PROFILE_CHANGED)
    controller, owner = start(w)
    for _ in range(6):
        controller.tick(owner)
        w.pool.run_all()
    x = job_of(w, 'x')
    assert attempts(w, 'x') == [] and w.dispatcher.launches['x'] == 0   # never claimed, never launched
    assert [row for row in readiness.table(w, 'attempt_dispositions') if row['job_id'] == x] == []
    assert [row for row in readiness.table(w, 'budget_reservations') if row['job_id'] == x] == []
    assert len(readiness.table(w, 'events', 'WHERE job_id=?', (x,))) == 2   # PENDING and QUEUED only
    assert node(w, 'y')['status'] == 'COMPLETED' and w.dispatcher.launches['y'] == 1
    assert 're-plan' in node(w, 'x')['wait_reason']


def test_the_runner_refuses_an_executor_resolved_for_another_profile(tmp_path, monkeypatch):
    """Important 1, defence in depth: the runner compares the profile a provider resolved the executor for."""
    from test_operation_runner import gpu_host

    from tuning_factory.attempt_runner import OperationAttemptRunner
    from tuning_factory.executor_profiles import ResourceFacts
    w = world(tmp_path, monkeypatch, lambda r: [train(r, 'x', profiles=('p1',))],
              destinations=pods('pod1', aliases=(('p3', 'pod1'),)))
    calls = []
    _, executor = gpu_host(tmp_path, monkeypatch, calls)
    executor.destination_profile = w.provider.destinations['p3'][0]   # what the provider opened: the alias
    with w.store.jobs.connection() as db:
        db.execute('UPDATE controller_leases SET expires=? WHERE run_id=?', (w.clock() + 3600, w.run_id))
    w.store.prepare_ready(w.ctl, node_ids=['x'])
    claimed = w.store.claim_ready(ResourceFacts(pod_id='pod1', gpu_type='A100', gpu_count=1, total_hourly_cost_usd=2,
                                                observed_at='2026-10-20T00:00:00Z'), w.ctl)
    outcome = OperationAttemptRunner(w.store, root=w.store.state_dir).run_claimed(claimed, executor,
                                                                                approval=w.approval)
    assert outcome.status == 'FAILED' and 'executor destination differs' in outcome.error
    assert calls == [] and w.store.jobs.validated_attempt(claimed['lease'])['launch_started'] == 0


def test_controller_creates_are_never_resent_by_the_transport(tmp_path, monkeypatch):
    """Important 2: a create the server committed but whose response was lost must not be POSTed again by the HTTP
    session inside one intent, and a model version create does not wait for READY."""
    from mlflow.environment_variables import MLFLOW_HTTP_REQUEST_MAX_RETRIES, MLFLOW_HTTP_REQUEST_TIMEOUT
    w = world(tmp_path, monkeypatch, lambda r: [train(r, 'a')])
    seen = []
    for name in ('create_run', 'create_model_version'):
        real = getattr(w.fake, name)

        def bounded(*args, _real=real, _name=name, **kwargs):
            seen.append((_name, MLFLOW_HTTP_REQUEST_MAX_RETRIES.get(), MLFLOW_HTTP_REQUEST_TIMEOUT.get(),
                         kwargs.get('await_creation_for', 'default')))
            return _real(*args, **{key: value for key, value in kwargs.items() if key != 'await_creation_for'})

        monkeypatch.setattr(w.fake, name, bounded)
    controller, owner = start(w)
    drive(w, controller, owner)
    assert {item[0] for item in seen} == {'create_run', 'create_model_version'}
    assert all(item[1:3] == (0, 60) for item in seen)   # the session retries nothing
    assert [item[3] for item in seen if item[0] == 'create_model_version'] == [0]
    assert os.environ.get('MLFLOW_HTTP_REQUEST_MAX_RETRIES') is None   # restored afterwards


def test_integrity_checks_are_scoped_to_the_nodes_own_lineage(tmp_path, monkeypatch):
    """Minor 1: a damaged record of an unrelated branch never holds this node; at dispatch, an integrity failure of the
    node's own lineage is returned unlaunched (uncounted) with an operator wait reason, never a counted failure."""
    gs = readiness.api()
    w = world(tmp_path, monkeypatch, lambda r: [materialize(r, 'full', r.first), train(r, 'a'), train(r, 'b', 'a'),
                                                 evaluate(r, 'e', 'full')], max_concurrent=1)
    controller, owner = start(w)
    drive(w, controller, owner, until=lambda: node(w, 'full')['status'] == 'COMPLETED'
          and status_of(w, 'full')['tracking_status'] == 'saved' and node(w, 'e')['job_id'] is None
          and node(w, 'b')['job_id'] is None)
    (saved,) = [outputs for evidence, outputs in w.store.ledger(w.run_id) if evidence.node_id == 'full']
    target = next(path for path in sorted(w.store.artifacts.path(saved.model.model).rglob('*')) if path.is_file())
    target.write_bytes(target.read_bytes() + b'damaged')   # full's verified artifact no longer verifies
    # b (a continuation of a) never depended on 'full': it compiles and runs.
    drive(w, controller, owner, until=lambda: node(w, 'b')['status'] == 'COMPLETED')
    assert node(w, 'e')['status'] == 'PENDING' and node(w, 'e')['wait_reason'].startswith(gs.LEDGER_INTEGRITY)
    assert status_of(w, 'e')['needs_operator'] and node(w, 'e')['error'] is None
    # At dispatch (the simulated dispatcher re-resolves bindings before launch): a's artifact, b's producer, is damaged
    # after b was claimed but before its thread ran.
    from tuning_factory import simulated_dispatch as sd
    other = world(tmp_path / 'dispatch', monkeypatch, chain, simulation=True, max_attempts=1)
    dispatcher = sd.SimulatedOperationDispatcher(other.store, root=other.store.state_dir)
    controller, owner = start(other, dispatcher=dispatcher, provider=None)
    for _ in range(20):
        controller.tick(owner)
        if node(other, 'b')['job_id'] and job_of(other, 'b') in other.pool.pending:
            break
        other.pool.run_all()
    b = job_of(other, 'b')
    ref = ModelArtifactRef.model_validate(
        other.store.jobs.get(b)['manifest']['operation']['model_input']['policy']['adapter'])
    target = next(path for path in sorted(other.store.artifacts.path(ref).rglob('*')) if path.is_file())
    target.write_bytes(target.read_bytes() + b'damaged')
    other.pool.run_all()
    for _ in range(4):
        controller.tick(owner)
        other.pool.run_all()
    (attempt,) = attempts(other, 'b')   # returned uncounted, and never claimed again
    assert other.store.jobs.get(b)['status'] == 'QUEUED' and attempt['launch_started'] == 0
    assert [(row['disposition'], row['counted']) for row in readiness.table(other, 'attempt_dispositions')
            if row['job_id'] == b] == [('returned_unlaunched', 0)]
    item = status_of(other, 'b', controller)
    assert item['needs_operator'] and item['wait_reason'].startswith(gs.LEDGER_INTEGRITY)


def test_a_stale_started_launch_is_settled_in_one_transaction(tmp_path, monkeypatch):
    """Minor 3: the FAILED step for an attempt that may have been contacted happens in the same transaction as the
    token rotation, and an actor that passed RUNNING meanwhile is left alone."""
    gs = readiness.api()
    w = world(tmp_path, monkeypatch, lambda r: [train(r, 'a', profiles=('p1',)), train(r, 'c', profiles=('p2',))],
              destinations=pods('pod1', 'pod2'), max_concurrent=2)
    with w.store.jobs.connection() as db:
        db.execute('UPDATE controller_leases SET expires=? WHERE run_id=?', (w.clock() + 3600, w.run_id))
    w.store.prepare_ready(w.ctl, node_ids=['a', 'c'])
    results = {}
    for node_id, case in (('a', 'record'), ('c', 'running')):
        claimed = readiness.claim(w, node_id)
        lease = claimed['lease']
        w.store.jobs.begin_launch(lease)
        if case == 'record':
            launch = w.store.state_dir / 'jobs' / claimed['id'] / 'launches' / claimed['launch_id']
            launch.mkdir(parents=True)
            (launch / gs.REMOTE_ATTEMPT).write_text('{}', encoding='utf-8')
        else:
            w.store.jobs.transition(lease, 'RUNNING')   # the old actor passed RUNNING after the controller's read
        results[case] = (w.store.settle_stale_launch(w.ctl, claimed['id']), claimed)
    outcome, claimed = results['record']
    assert outcome == 'uncertain'
    job = w.store.jobs.get(claimed['id'])
    assert (job['status'], job['error']) == ('FAILED', gs.STALE_HEARTBEAT) and w.store.jobs.holds_lock(
        w.store.jobs.current_lease(claimed['id']))
    with pytest.raises(ValueError, match='stale attempt lease'):
        w.store.jobs.transition(claimed['lease'], 'RUNNING')   # the old actor is fenced
    outcome, claimed = results['running']
    assert outcome == 'unchanged' and w.store.jobs.get(claimed['id'])['status'] == 'RUNNING'
    w.store.jobs.heartbeat(claimed['lease'])   # its token was not rotated


def test_one_unreadable_run_never_hides_the_others(tmp_path, monkeypatch):
    """Minor 4: graph_status and status() report an error entry for a run they cannot read, and every other run."""
    gc = api()
    w = world(tmp_path, monkeypatch, chain)
    other = readiness.second_run(w)
    controller, owner = start(w)
    with w.store.jobs.connection() as db:
        db.execute("UPDATE graph_runs SET plan='{}' WHERE id=?", (other.run_id,))
    status = gc.graph_status(w.store, now=w.clock())
    by_id = {item['run_id']: item for item in status['runs']}
    assert set(by_id) == {w.run_id, other.run_id}
    assert by_id[other.run_id]['error'].startswith('unreadable graph run') and 'nodes' not in by_id[other.run_id]
    assert by_id[w.run_id]['status'] == 'RUNNING' and len(by_id[w.run_id]['nodes']) == 2
    controller.tick(owner)   # the controller keeps scheduling the readable run
    assert {item['run_id'] for item in controller.status()['runs']} == {w.run_id, other.run_id}
    assert node(w, 'a')['status'] in {'READY', 'RUNNING'}


class FailingFuturePool(ManualPool):
    """A dispatch thread that died with a BaseException (what ThreadPoolExecutor stores in its future)."""

    def submit(self, key, fn):
        future = concurrent.futures.Future()
        future.set_running_or_notify_cancel()
        future.set_exception(Crash('the dispatch thread died'))
        return future


def test_a_dying_dispatch_thread_still_releases_the_owner(tmp_path, monkeypatch):
    """Minor 5: a BaseException from a dispatch future escapes run_forever only after the owner lease is released."""
    gc = api()
    w = world(tmp_path, monkeypatch, chain)
    controller, owner = start(w, pool=FailingFuturePool(), settings=gc.ControllerSettings(tick_seconds=0.01,
                                                                                          lease_heartbeat_seconds=0.01))
    with pytest.raises(Crash):
        controller.run_forever(threading.Event())
    assert w.store.owner_state()['expires'] == 0
    start(w, 'controller-b')   # a successor acquires at once


def test_secrets_the_controller_knows_never_reach_the_ledger(tmp_path, monkeypatch):
    """Minor 7: error text that echoes a destination's configured secret is redacted before any ledger write."""
    w = world(tmp_path, monkeypatch, lambda r: [train(r, 'a'), train(r, 'f')], max_attempts=1)
    w.provider.secret = SECRET   # the FakeProvider redacts it, as SettingsExecutorProvider.redact does
    w.dispatcher.script['f'] = ['fail_remote']   # its error text echoes the secret
    controller, owner = start(w)
    drive(w, controller, owner)
    assert node(w, 'f')['status'] == 'FAILED'
    with w.store.jobs.connection() as db:
        dump = '\n'.join(db.iterdump())
    assert SECRET not in dump and '[redacted]' in dump
    files = b''.join(path.read_bytes() for path in w.store.state_dir.rglob('*') if path.is_file()
                     and path.suffix in {'.txt', '.json', '.log', ''})
    assert SECRET.encode() not in files   # the accepted result and error.txt are redacted too
    uploaded = repr({run_id: (run.data, run.artifacts) for run_id, run in w.fake.runs.items()})
    assert SECRET not in uploaded


def test_refused_destinations_back_off_and_finished_runs_are_forgotten(tmp_path, monkeypatch, caplog):
    """Minor 8: a refused destination is not re-opened every tick; in-process maps forget finished runs; a refusal
    whose return fails for another reason is still returned later."""
    gc = api()
    w = world(tmp_path, monkeypatch, lambda r: [train(r, 'a')])
    w.provider.refuse['p1'] = ConnectionError('ssh connect timed out')
    controller, owner = start(w, settings=gc.ControllerSettings(lease_ttl_seconds=600))
    for _ in range(4):
        controller.tick(owner)
        w.clock.advance(10)
    assert w.provider.resolves == ['p1']   # backed off
    assert node(w, 'a')['wait_reason'].startswith('destination unavailable')
    w.clock.advance(gc.ControllerSettings().destination_retry_seconds)
    del w.provider.refuse['p1']
    drive(w, controller, owner)
    assert w.provider.resolves.count('p1') == 2 and run_row(w)['status'] == 'COMPLETED'
    controller.tick(owner)
    assert not [key for key in controller._reasons if key[0] == w.run_id]
    assert not controller._outcomes and not controller._reconciled_at and not controller._simulated
    assert controller.status()['controller']['in_flight'] == []


def test_the_runner_redacts_results_and_error_files(tmp_path, monkeypatch):
    """Minor 7: the accepted result and error.txt pass through the store's error filter (the controller installs it)."""
    from tuning_factory.attempt_runner import OperationAttemptRunner
    from tuning_factory.worker_protocol import WorkerRequest
    w = world(tmp_path, monkeypatch, lambda r: [train(r, 'a')])
    w.store.jobs.error_filter = lambda text: text.replace(SECRET, '[redacted]')
    runner = OperationAttemptRunner(w.store, root=w.store.state_dir)
    launch = w.store.state_dir / 'jobs' / 'j1' / 'launches' / ('a' * 32)
    launch.mkdir(parents=True)
    request = WorkerRequest(attempt_id='b' * 32, launch_id='a' * 32, manifest_sha256='c' * 64,
                            operation_sha256='d' * 64, timeout_seconds=60)
    raw = {'schema_version': 2, 'status': 'FAILED', 'launch_id': 'a' * 32, 'attempt_id': 'b' * 32,
           'error': f'remote said {SECRET}'}
    result = runner._accept(raw, request, launch)
    assert result.error == 'remote said [redacted]'
    assert SECRET.encode() not in (launch.parent / f'{"a" * 32}.result.json').read_bytes()
    runner._write_error('j1', f'refused: {SECRET}')
    assert (w.store.state_dir / 'jobs' / 'j1' / 'error.txt').read_text(encoding='utf-8') == 'refused: [redacted]'


def test_heartbeat_warnings_are_rate_limited(tmp_path, monkeypatch, caplog):
    """8.5 review Minor 8: a database that stays locked logs the first failure and then only occasionally."""
    import logging
    import sqlite3

    from tuning_factory.attempt_runner import OperationAttemptRunner
    w = world(tmp_path, monkeypatch, lambda r: [train(r, 'a')])
    runner = OperationAttemptRunner(w.store, root=w.store.state_dir)
    runner.heartbeat_seconds = 0.001
    failures, done = [], threading.Event()

    def heartbeat(lease):
        failures.append(lease)
        if len(failures) >= 40:
            done.set()
        raise sqlite3.OperationalError('database is locked')

    runner.jobs = SimpleNamespace(heartbeat=heartbeat)
    caplog.set_level(logging.WARNING)
    stop = runner._heartbeat(SimpleNamespace(job_id='j'))
    try:
        assert done.wait(30)
    finally:
        stop.set()
    warnings = [record for record in caplog.records if 'heartbeat failed' in record.getMessage()]
    assert 1 <= len(warnings) <= 3


def test_a_failed_return_never_hides_the_refusal(tmp_path, monkeypatch):
    """8.5 review Minor 8: if returning a refused launch fails for another reason (a locked database), the refusal
    itself still surfaces and the next harvest returns the attempt."""
    import sqlite3
    gs = readiness.api()
    w = world(tmp_path, monkeypatch, chain)
    controller, owner = start(w)
    controller.tick(owner)
    a = job_of(w, 'a')
    w.store.pause(w.run_id)
    original = gs.GraphStore.return_unlaunched

    def locked(self, *args, **kwargs):
        raise sqlite3.OperationalError('database is locked')

    monkeypatch.setattr(gs.GraphStore, 'return_unlaunched', locked)
    future = w.pool.run(a)
    assert str(future.exception()) == GRAPH_LAUNCH_REFUSED   # not the database error
    assert w.store.jobs.get(a)['status'] == 'PROVISIONING'
    monkeypatch.setattr(gs.GraphStore, 'return_unlaunched', original)
    report = controller.tick(owner)
    assert a in report.released and w.store.jobs.get(a)['status'] == 'QUEUED'



# -- 8.5 re-review Minors ----------------------------------------------------------------------------

def backdate(w, job_id, seconds, statuses=('PENDING', 'QUEUED', 'PROVISIONING', 'RUNNING')):
    """As if the job's attempt was claimed (and its earlier steps happened) ``seconds`` ago, in wall time."""
    with w.store.jobs.connection() as db:
        db.execute('UPDATE job_attempts SET created=created-? WHERE job_id=?', (seconds, job_id))
        db.execute('UPDATE jobs SET updated=updated-? WHERE id=?', (seconds, job_id))
        db.execute('UPDATE events SET timestamp=timestamp-? WHERE job_id=? AND status IN (%s)'
                   % ','.join('?' * len(statuses)), (seconds, job_id, *statuses))


@pytest.mark.parametrize('case', ['resumed_claim', 'slow_upload'])
def test_the_result_deadline_starts_when_the_attempt_became_uncertain(tmp_path, monkeypatch, case):
    """Re-review Minor 1: an attempt resumed long after its claim, or one whose input upload took hours after RUNNING,
    still has its whole time limit after the launch: no RESULT_OVERDUE (and no release-lock prompt) at once."""
    gc = api()
    w = world(tmp_path, monkeypatch, chain)
    w.dispatcher.script['a'] = ['uncertain']
    controller, owner = start(w)
    controller.tick(owner)
    a = job_of(w, 'a')
    if case == 'resumed_claim':   # claimed an hour ago by a controller that died; resumed and launched now
        backdate(w, a, 3600)
        controller, owner = restart(w, advance=61)
    else:   # RUNNING two hours ago; the inputs uploaded until now, then the launch's response was lost
        w.dispatcher.during['a'] = lambda: backdate(w, a, 7200)
    drive(w, controller, owner, until=lambda: bool(w.provider.recovers))
    item = status_of(w, 'a', controller)
    assert item['uncertain'] and item['awaiting_result'] and not item['needs_operator']
    assert node(w, 'a')['wait_reason'] == gc.AWAITING_RESULT
    assert item['result_deadline'] > w.clock() + 900   # the whole time limit lies ahead


def test_replan_waits_back_off_and_never_rehash_the_lineage(tmp_path, monkeypatch):
    """Re-review Minor 2: a live price above the pinned one is not re-opened every tick, and the lineage is checked
    before a claim only where the dispatcher needs it (a continuation), once per retry period."""
    gs = readiness.api()
    w = world(tmp_path, monkeypatch, chain)
    resolver_calls = []
    real = gs.GraphStore.bindings_for

    def spy(self, run_id, node_id):
        resolver_calls.append(node_id)
        return real(self, run_id, node_id)

    monkeypatch.setattr(gs.GraphStore, 'bindings_for', spy)
    controller, owner = start(w)
    drive(w, controller, owner, until=lambda: node(w, 'a')['status'] == 'COMPLETED'
          and status_of(w, 'a')['tracking_status'] == 'saved' and node(w, 'b')['job_id'] is None)
    assert resolver_calls.count('a') == 1   # its compile only: a root training needs no pre-claim lineage check
    w.provider.price['p1'] = 3.0   # the live price rose above the pinned 2.0
    resolves, calls = len(w.provider.resolves), len(resolver_calls)
    for _ in range(6):
        controller.tick(owner)
        w.pool.run_all()
        w.clock.advance(5)
    assert len(w.provider.resolves) - resolves == 1   # opened once, then backed off
    assert len(resolver_calls) - calls <= 2          # b's compile and one pre-claim check, never per tick
    assert 're-plan' in node(w, 'b')['wait_reason'] and attempts(w, 'b') == []
    for _ in range(2):   # the retry period passes (ticks keep the owner lease alive)
        w.clock.advance(api().ControllerSettings().destination_retry_seconds / 2)
        controller.tick(owner)
    assert len(w.provider.resolves) - resolves == 2   # tried again after the retry period, once


def test_the_dispatcher_must_use_the_controllers_store(tmp_path, monkeypatch):
    """Re-review Minor 3: the error filter is installed on the controller's store, so a dispatcher (or a router's real
    or simulated dispatcher) on another GraphStore is refused at construction."""
    gc = api()
    gs = readiness.api()
    from tuning_factory.attempt_runner import OperationAttemptRunner
    from tuning_factory.simulated_dispatch import SimulatedOperationDispatcher
    w = world(tmp_path, monkeypatch, chain)
    other = gs.GraphStore(w.store.jobs.path, clock=w.clock)   # the same file, another instance
    for dispatcher in (OperationAttemptRunner(other, root=other.state_dir),
                       gc.DispatchRouter(OperationAttemptRunner(w.store, root=w.store.state_dir),
                                         SimulatedOperationDispatcher(other, root=other.state_dir))):
        with pytest.raises(ValueError, match=gc.DISPATCHER_STORE_DIFFERS):
            gc.GraphController(w.store, w.provider, dispatcher, w.tracker, w.clock, pool=ManualPool())
    controller = gc.GraphController(w.store, w.provider, gc.DispatchRouter(
        OperationAttemptRunner(w.store, root=w.store.state_dir), SimulatedOperationDispatcher(
            w.store, root=w.store.state_dir)), w.tracker, w.clock, pool=ManualPool())
    assert w.store.jobs.error_filter == controller.redact


def test_an_uncertain_attempt_holds_a_concurrency_slot(tmp_path, monkeypatch):
    """Task 11.5 R1 m1: with max_concurrent=1 on two Pods, the first attempt's lost response leaves it uncertain (it
    may still run on its GPU), so the other node is not dispatched until the late result is reconciled."""
    gc = api()
    w = world(tmp_path, monkeypatch, two_pods_two_trains, destinations=pods('pod1', 'pod2'), max_concurrent=1)
    controller, owner = start(w)
    controller.tick(owner)
    (first,) = w.pool.pending
    held = w.store.jobs.get(first)['graph_node_id']
    other = ({'x', 'y'} - {held}).pop()
    w.dispatcher.script[held] = ['uncertain']
    w.pool.run_all()
    resolves = len(w.provider.resolves)
    for _ in range(3):
        controller.tick(owner)
        assert not w.pool.pending and w.dispatcher.launches == {held: 1}
    assert len(w.provider.resolves) == resolves   # the other destination is not even opened
    assert w.store.jobs.get(job_of(w, other))['status'] == 'QUEUED'
    assert node(w, other)['wait_reason'] == gc.AT_CONCURRENCY
    w.dispatcher.deliver(held)
    for _ in range(2):   # ticks well inside the owner TTL, one reconcile interval in all
        w.clock.advance(gc.ControllerSettings().reconcile_interval_seconds / 2)
        controller.tick(owner)
    drive(w, controller, owner)
    assert statuses(w) == {'x': 'COMPLETED', 'y': 'COMPLETED'} and w.dispatcher.launches == {'x': 1, 'y': 1}


# -- Task 11.5 re-review: outputs that cannot be saved locally, a cancel in a retry backoff --------------------

def windows_path_error(winerror):
    """What CPython raises on Windows for WinError 206 (path too long) or 53/67 (network path or name gone): the
    error maps to ENOENT, so a FileNotFoundError carrying ``winerror``; built on any OS."""
    error = FileNotFoundError(2, 'synthetic: the path is too long or its network share is gone')
    error.winerror = winerror
    return error


@pytest.mark.parametrize('error,then', [('sharing_violation', 'fixed'), ('sharing_violation', 'released'),
                                        ('path_too_long', 'fixed'), ('network_path_gone', 'fixed'),
                                        ('open_path_too_long', 'fixed')])
def test_outputs_that_cannot_be_saved_locally_name_the_local_fix(tmp_path, monkeypatch, error, then):
    """Re-review M2 (b, c): an accepted COMPLETED result whose freeze keeps failing on this PC (an antivirus holding
    the ``.freeze-*`` directory, a path too long, a network share gone) is named for its local fix, not "could not
    reach the destination". A result arrived, so nothing is awaited and the desktop offers the release. Fixed, the
    next reconcile completes it; released, it is a terminal ``output_invalid``. Windows raises a path too long
    (WinError 206) and a gone network path (53) as FileNotFoundError: still transient (final re-review). The freeze
    copy's ``open()`` raises a path too long without any ``winerror``: judged by its length (last re-review)."""
    from pathlib import Path

    from test_model_artifacts import sharing_violation

    from tuning_factory import execution, model_artifacts
    gc = api()
    w = host_world(tmp_path, monkeypatch, lambda r: [train(r, 'a')])
    rename, opener, busy = Path.rename, Path.open, {'on': True}
    failure = {'sharing_violation': lambda: sharing_violation(32), 'path_too_long': lambda: windows_path_error(206),
               'network_path_gone': lambda: windows_path_error(53),
               'open_path_too_long': lambda: FileNotFoundError(2, 'synthetic: the path is too long',
                                                               'C:/' + 'd' * 300)}[error]

    def held(self, target):
        if busy['on'] and self.name.startswith('.freeze-'):
            raise failure()
        return rename(self, target)

    def long_open(self, mode='r', *args, **kwargs):
        if busy['on'] and mode == 'xb' and any(part.startswith('.freeze-') for part in self.parts):
            raise failure()
        return opener(self, mode, *args, **kwargs)

    if error == 'open_path_too_long':
        monkeypatch.setattr(execution, 'WINDOWS', True)
        monkeypatch.setattr(Path, 'open', long_open)
    else:
        monkeypatch.setattr(Path, 'rename', held)
    monkeypatch.setattr(model_artifacts, 'RENAME_RETRY_SECONDS', 0)
    controller, owner = start(w, dispatcher=w.runner, provider=w.host_provider)
    controller.tick(owner)
    w.pool.run_all()   # the freeze fails: uncertain, the lock kept
    controller.tick(owner)   # reconcile collects the same launch again; the freeze fails again
    a = job_of(w, 'a')
    reason = node(w, 'a')['wait_reason']
    assert reason.startswith(gc.OUTPUTS_NOT_SAVED.split('{')[0]) and type(failure()).__name__ in reason
    assert reason.startswith(gc.OPERATOR_PREFIXES)
    item = status_of(w, 'a', controller)
    assert (item['uncertain'], item['awaiting_result'], item['needs_operator']) == (True, False, True)
    assert w.store.jobs.get(a)['status'] == 'FAILED' and w.store.jobs.gpu_locked('existing_runpod:pod1')
    if then == 'fixed':
        busy['on'] = False
        for _ in range(2):   # ticks well inside the owner TTL, one reconcile interval in all
            w.clock.advance(gc.ControllerSettings().reconcile_interval_seconds / 2)
            controller.tick(owner)
        drive(w, controller, owner)
        assert statuses(w) == {'a': 'COMPLETED'} and run_row(w)['status'] == 'COMPLETED'
    else:
        released = desktop_release(w, 'a', attempts(w, 'a')[0]['attempt_id'])
        assert released['status'] == 'ok', released
        drive(w, controller, owner)
        assert statuses(w) == {'a': 'FAILED'} and w.store.attempt_disposition(a)['disposition'] == 'output_invalid'
    assert w.engine_calls == ['train.bound.yaml'] and len(w.host.launches) == 1


@pytest.mark.parametrize('when', ['before_classification', 'in_backoff'])
def test_a_job_cancel_during_a_retry_backoff_is_honoured(tmp_path, monkeypatch, when):
    """Re-review M3: ``factory cancel <job>`` of a remotely failed attempt (FAILED, lock released) did nothing, so
    the retry relaunched it after the backoff. The cancel is flagged while the node is unfinished, and the node is
    settled CANCELED with its subtree BLOCKED: before classification by ``_settle_node``, in the backoff by
    ``settle_canceled_jobs``."""
    w = world(tmp_path, monkeypatch, chain, max_attempts=2, backoff=300)
    w.dispatcher.script['a'] = ['fail_remote']
    controller, owner = start(w)
    controller.tick(owner)
    w.pool.run_all()   # a remote failure: FAILED, its lock released
    a = job_of(w, 'a')
    if when == 'in_backoff':
        controller.tick(owner)
        assert node(w, 'a')['status'] == 'RUNNING' and node(w, 'a')['retry_not_before'] is not None
    w.store.jobs.cancel(a)
    assert w.store.jobs.get(a)['cancel_requested'] == 1
    for _ in range(12):   # well past the backoff, ticking inside the owner TTL
        w.clock.advance(30)
        controller.tick(owner)
    drive(w, controller, owner)
    assert statuses(w) == {'a': 'CANCELED', 'b': 'BLOCKED'} and run_row(w)['status'] == 'FAILED'
    assert w.dispatcher.launches == {'a': 1} and len(attempts(w, 'a')) == 1
    assert w.store.attempt_disposition(a)['disposition'] == 'remote_failed'
    assert node(w, 'a')['retry_not_before'] is None
