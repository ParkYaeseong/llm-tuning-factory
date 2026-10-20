"""Task 8.2: readiness, outcomes, failure propagation and safe release on the graph ledger.

Main env only, fakes only: no GPU, SSH, network, provider or MLflow. Most nodes are finished at the ledger level
(a fixture artifact frozen under the attempt's producer identity, then ``record_outputs``). One test drives the real
attempt runner against the fake GPU host of ``test_operation_runner``.
"""
import importlib
import json
import socket
import time
from types import SimpleNamespace

import pytest
import test_model_inputs as inputs
from pydantic import TypeAdapter
from test_graph_store import FakeClock, queued
from test_operation_runner import (
    EXECUTOR,
    FACTS,
    HELD_ROWS,
    TRACKING,
    TRAIN_ROWS,
    Crash,
    gpu_host,
    producer,
    register,
    revision,
)

from tuning_factory.benchmarks import BenchmarkSpec, EvaluationRecord
from tuning_factory.executor_profiles import ResourceObservation, observation_key
from tuning_factory.graph_planner import (
    ApprovalGrant,
    BenchmarkRevision,
    ExecutionChoices,
    ModelContext,
    NodeEvaluationBinding,
    NodeModelBinding,
    NodeResources,
    plan_graph,
    snapshot_artifact,
    snapshot_dataset,
)
from tuning_factory.graph_schema import GraphSpec
from tuning_factory.model_artifacts import ModelArtifactRef, ModelInput, ModelLoadRef
from tuning_factory.projects import DatasetBinding, ProjectSpec
from tuning_factory.recipes import RecipeSpec
from tuning_factory.registry import canonical


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Readiness and outcomes never open a connection: local SQLite, local files and fakes only."""
    def refuse(*args, **kwargs):
        raise AssertionError('graph readiness tests must not open a network connection')
    monkeypatch.setattr(socket.socket, 'connect', refuse)


def api():
    # A missing implementation is a test failure, rather than a collection error.
    module = importlib.import_module('tuning_factory.graph_store')
    for name in ('LedgerBindingResolver', 'Readiness'):
        if not hasattr(module, name):
            pytest.fail(f'graph_store.{name} (Task 8.2) is missing')
    return module


# -- graph definitions ---------------------------------------------------------------------------

def train(r, node_id, source=None, profiles=('p1',)):
    """A LoRA training of the base artifact, or a continuation of ``source``'s adapter output."""
    model_input = ({'node_id': source, 'output_name': 'model'} if source else
                   ModelInput(policy=ModelLoadRef(base=r.base), initialization='new_adapter').model_dump(mode='json'))
    return {'kind': 'train', 'id': node_id, 'label': 'Train', 'recipe_revision': r.recipe.revision_sha256,
            'model_input': model_input, 'dataset_binding': 'data', 'executor_profile_ids': list(profiles)}


def evaluate(r, node_id, model, profiles=('p1',)):
    model = {'node_id': model, 'output_name': 'model'} if isinstance(model, str) else model.model_dump(mode='json')
    return {'kind': 'evaluate', 'id': node_id, 'label': 'Evaluate', 'model': model,
            'benchmark_revision': r.benchmark.fingerprint(), 'executor_profile_ids': list(profiles)}


def select(node_id, evaluations, **selection):
    return {'kind': 'select', 'id': node_id, 'label': 'Select',
            'evaluations': [{'node_id': item, 'output_name': 'evaluation'} for item in evaluations],
            'selection': {'metric': 'accuracy', 'direction': 'max', **selection}}


def merge(node_id, models, profiles=('p1',)):
    return {'kind': 'merge', 'id': node_id, 'label': 'Merge', 'weights': [0.5, 0.5],
            'models': [{'node_id': item, 'output_name': 'model'} for item in models],
            'executor_profile_ids': list(profiles)}


def materialize(r, node_id, model, profiles=('p1',)):
    return {'kind': 'materialize', 'id': node_id, 'label': 'Materialize', 'model': model.model_dump(mode='json'),
            'executor_profile_ids': list(profiles)}


def pods(*pod_ids, aliases=()):
    """RunPod destinations ``p1``… on the given Pods; ``aliases`` are extra profiles of an earlier Pod."""
    destinations = [(EXECUTOR.model_copy(update={'profile_id': f'p{index}', 'pod_id': pod}),
                     FACTS.model_copy(update={'pod_id': pod})) for index, pod in enumerate(pod_ids, 1)]
    for profile_id, pod in aliases:
        destinations.append((EXECUTOR.model_copy(update={'profile_id': profile_id, 'pod_id': pod}),
                             FACTS.model_copy(update={'pod_id': pod})))
    return destinations


def diamond(r, profiles=('p1', 'p2')):
    """a -> (b, c) -> d: two continuations of a's adapter, merged."""
    return [train(r, 'a', profiles=profiles), train(r, 'b', 'a', profiles), train(r, 'c', 'a', profiles),
            merge('d', ('b', 'c'), profiles)]


def build(tmp_path, nodes, *, destinations=None, simulation=False, max_attempts=1, backoff=0, max_concurrent=1,
          resolver=True, clock=None):
    """A submitted plan over frozen base/adapter artifacts in the GraphStore's own store, with a run lease.

    Nothing is prepared: the caller drives readiness. The trusted ledger resolver is installed by default.
    """
    gs = api()
    tmp_path.mkdir(parents=True, exist_ok=True)
    clock = clock or FakeClock()
    store = gs.GraphStore(tmp_path / 'state' / 'jobs.db', clock=clock)
    registry, models = store.registry, store.artifacts
    base = models.freeze(*inputs.full_model(tmp_path, 'base'))
    first = models.freeze(*inputs.peft_adapter(tmp_path, 'first', base=base, parents=(base,)))
    second = models.freeze(*inputs.peft_adapter(tmp_path, 'second', rank=3, alpha=9, base=base, parents=(base,)))
    data = register(registry, tmp_path, 'data', TRAIN_ROWS, True)
    held = register(registry, tmp_path, 'held', HELD_ROWS, True)
    recipe = revision(RecipeSpec(recipe_id='r', name='recipe',
                                 hyperparameters={'lora_rank': 2, 'lora_target': 'q_proj,v_proj'}))
    benchmark = BenchmarkSpec(dataset=held, evaluator_kind='classification', evaluator_version='1',
                              ordered_sample_ids=('row:1', 'row:0'), max_new_tokens=8, cutoff_len=64, seed=1,
                              precision='bf16', quantization_bit=None,
                              tokenizer_fingerprint=inputs.ROOT.tokenizer_fingerprint, template='qwen',
                              semantic_model_kind='causal_lm')
    destinations = destinations or pods('pod1')
    observed = {}
    for _, facts in destinations:
        observed.setdefault(observation_key(facts), facts)
    refs = SimpleNamespace(base=base, first=first, second=second, recipe=recipe, benchmark=benchmark)
    graph = GraphSpec.model_validate({'nodes': nodes(refs), 'max_concurrent': max_concurrent,
                                      'retry_policy': {'max_attempts': max_attempts, 'backoff_seconds': backoff}})
    project = revision(ProjectSpec(project_id='project.1', name='Project', datasets=(
        DatasetBinding(binding_id='data', role='train', dataset=data),
        DatasetBinding(binding_id='held', role='validation', dataset=held)), graph=graph.model_dump(mode='json')))
    producers = tuple(producer(ref, marker) for marker, ref in (('base', base), ('first', first), ('second', second)))
    choices = ExecutionChoices(
        recipes=(recipe,), benchmarks=(BenchmarkRevision(spec=benchmark, revision_sha256=benchmark.fingerprint()),),
        datasets=(snapshot_dataset(registry, data), snapshot_dataset(registry, held)),
        models=(ModelContext(root=inputs.ROOT, profile=inputs.PROFILE),),
        artifacts=tuple(snapshot_artifact(models, item.model, item) for item in producers),
        executors=tuple(profile for profile, _ in destinations),
        node_resources=tuple(NodeResources(node_id=node.id, timeout_seconds=900) for node in graph.nodes
                             if node.kind != 'select'),
        base_targets=(inputs.evidence(base),), tracking_destination=TRACKING)
    plan = plan_graph(project, choices, tuple(observed.values()), simulation=simulation)
    approval = ApprovalGrant.for_plan(plan)
    run = store.submit(plan, 'run', approval)
    if resolver:
        store.binding_resolver = gs.LedgerBindingResolver(store)
    ctl = store.acquire_controller(run['id'], ttl_seconds=3600)
    return SimpleNamespace(store=store, plan=plan, approval=approval, run_id=run['id'], ctl=ctl, refs=refs,
                           clock=clock, tmp=tmp_path)


def second_run(w, request='second'):
    run = w.store.submit(w.plan, request, w.approval)
    return w.store.acquire_controller(run['id'], ttl_seconds=3600)


# -- ledger helpers ------------------------------------------------------------------------------

def node_row(w, node_id, run_id=None):
    return next(node for node in w.store.get_run(run_id or w.run_id)['nodes'] if node['node_id'] == node_id)


def statuses(w, run_id=None):
    return {node['node_id']: node['status'] for node in w.store.get_run(run_id or w.run_id)['nodes']}


def states(w, run_id=None):
    return {item.node_id: (item.state, item.reason) for item in w.store.readiness(run_id or w.run_id)}


def ready(w, run_id=None):
    return sorted(item.node_id for item in w.store.readiness(run_id or w.run_id) if item.state == 'ready')


def prepare(w, *node_ids, ctl=None):
    return w.store.prepare_ready(ctl or w.ctl, node_ids=node_ids)


def pinned(w, node_id, run_id=None):
    manifest = w.store.jobs.get(node_row(w, node_id, run_id)['job_id'])['manifest']
    return manifest['operation']['resources']['executor']['profile_id']


def table(w, name, where='', args=()):
    with w.store.jobs.connection() as db:
        return [dict(row) for row in db.execute(f'SELECT * FROM {name} {where} ORDER BY rowid', args)]


def events(w, job_id):
    return [(row['status'], row['detail']) for row in table(w, 'events', 'WHERE job_id=?', (job_id,))]


def claim(w, node_id, ctl=None):
    """Claim this node's queued job on its pinned destination (a local selection without facts)."""
    ctl = ctl or w.ctl
    job_id = node_row(w, node_id, ctl.run_id)['job_id']
    with w.store.jobs.connection() as db:
        db.execute('UPDATE jobs SET priority=(SELECT COALESCE(MAX(priority),0)+1 FROM jobs) WHERE id=?', (job_id,))
    operation = w.store.jobs.get(job_id)['manifest']['operation']
    facts = (None if operation['kind'] == 'select'
             else TypeAdapter(ResourceObservation).validate_python(operation['resources']['facts']))
    claimed = w.store.claim_ready(facts, ctl)
    assert claimed is not None and claimed['id'] == job_id, claimed
    return claimed


def produced(w, claimed, descriptor):
    return descriptor.model_copy(update={
        'producer_job_id': claimed['id'], 'producer_attempt_id': claimed['lease'].attempt_id,
        'producer_manifest_sha256': claimed['manifest']['sha256'], 'simulation': w.plan.simulation})


def adapter_output(w, claimed):
    """What a LoRA attempt produces: an adapter on the base artifact, frozen under this attempt's identity."""
    directory, descriptor = inputs.peft_adapter(w.tmp, 'out-' + claimed['lease'].attempt_id, base=w.refs.base,
                                                parents=(w.refs.base,))
    ref = w.store.artifacts.freeze(directory, produced(w, claimed, descriptor))
    return NodeModelBinding(node_id=claimed['graph_node_id'], model=ref)


def full_output(w, claimed):
    """What a materialize attempt produces: full unquantized weights with the base and adapter as parents."""
    parents = tuple(sorted((w.refs.base, w.refs.first), key=lambda item: (item.artifact_id, item.inventory_sha256)))
    directory, descriptor = inputs.full_model(w.tmp, 'out-' + claimed['lease'].attempt_id, base=w.refs.base,
                                              parents=parents)
    ref = w.store.artifacts.freeze(directory, produced(w, claimed, descriptor))
    return NodeModelBinding(node_id=claimed['graph_node_id'], model=ref)


def evaluation_output(w, claimed, accuracy):
    record = EvaluationRecord(
        model=ModelArtifactRef.model_validate(claimed['manifest']['operation']['model']),
        benchmark_sha256=w.refs.benchmark.fingerprint(), metrics={'accuracy': accuracy}, status='completed',
        synthetic=False, simulation=w.plan.simulation, producer_attempt_id=claimed['lease'].attempt_id)
    return NodeEvaluationBinding(node_id=claimed['graph_node_id'], evaluation=record, tracking_acknowledged=False)


def finished(w, node_id, *, ctl=None, release=True):
    """Claim, launch and complete the node's attempt (its outputs are not recorded yet)."""
    claimed = claim(w, node_id, ctl)
    jobs, lease = w.store.jobs, claimed['lease']
    jobs.begin_launch(lease)
    for status in ('RUNNING', 'EVALUATING', 'COMPLETED'):
        jobs.transition(lease, status)
    if release:
        jobs.release_gpu(lease, execution_stopped=True)
    return claimed


def complete(w, node_id, output=adapter_output, *, ctl=None, ack=True, accuracy=0.5, targets=()):
    gs = api()
    claimed = finished(w, node_id, ctl=ctl)
    if output is evaluation_output:
        outputs = gs.NodeOutputs(evaluation=evaluation_output(w, claimed, accuracy))
    else:
        binding = output(w, claimed)
        outputs = gs.NodeOutputs(model=binding, inspected_targets=tuple(target(binding.model) for target in targets))
    w.store.record_outputs(claimed['lease'], outputs)
    if ack:
        w.store.acknowledge_tracking(claimed['lease'])
    return claimed, outputs


def failed(w, node_id, kind='remote_failed', *, ctl=None):
    """One attempt of the node that ended as ``kind`` with its lock released, then classified by the controller."""
    ctl = ctl or w.ctl
    claimed = claim(w, node_id, ctl)
    jobs, lease = w.store.jobs, claimed['lease']
    if kind == 'refused_before_launch':
        assert jobs.abandon_unlaunched(lease, 'refused before launch')
    else:
        jobs.begin_launch(lease)
        jobs.transition(lease, 'RUNNING')
        jobs.transition(lease, 'CANCELED' if kind == 'canceled' else 'FAILED', 'attempt ended: ' + kind)
        jobs.release_gpu(lease, execution_stopped=True)
    assert w.store.classify_attempt(ctl, claimed['id'], kind) == kind
    return claimed


def evaluations_world(tmp_path, *, simulation=False, **selection):
    """Three evaluations (base, first, second), a selection over them and an evaluation of its winner."""
    return build(tmp_path, lambda r: [evaluate(r, 'e1', r.base), evaluate(r, 'e2', r.first),
                                      evaluate(r, 'e3', r.second), select('s', ('e1', 'e2', 'e3'), **selection),
                                      evaluate(r, 'after', 's')], simulation=simulation)


# -- 1-3. the trusted ledger binding resolver (D1) -----------------------------------------------

def test_resolver_assigns_parallel_branches_to_distinct_destinations(tmp_path):
    for case in ('free', 'legacy_lock'):
        w = build(tmp_path / case, diamond, destinations=pods('pod1', 'pod2'), max_concurrent=2)
        assert ready(w) == ['a']
        prepare(w, 'a')
        assert pinned(w, 'a') == 'p1'   # nothing held, nothing loaded: the first approved alternative
        complete(w, 'a')
        assert ready(w) == ['b', 'c']
        if case == 'legacy_lock':
            queued(w.store.jobs, 'legacy')
            assert w.store.jobs.claim('existing_runpod:pod1', max_concurrent=8)
        assert len(prepare(w, 'b', 'c')) == 2
        # Free: b takes pod1 and c spreads to pod2. With pod1 held by a legacy job, both avoid it.
        assert (pinned(w, 'b'), pinned(w, 'c')) == (('p1', 'p2') if case == 'free' else ('p2', 'p2'))
        assert w.store.bindings_for(w.run_id, 'c').destination_profile.profile_id == pinned(w, 'c')


def ssh_destinations():
    """Two profiles (two transports) of server a, and one profile of server b with other GPUs."""
    from test_gpu_servers import observation, settings

    from tuning_factory.gpu_servers import GPUDevice
    near = settings()
    far = near.model_copy(update={'remote_root': '/home/researcher/other'})
    other = settings().model_copy(update={'selected_gpu_uuids': ('GPU-33333333-3333-3333-3333-333333333333',)})
    a1, a2 = observation(near), observation(far)
    b = observation(other, server_id='b' * 64, inventory=tuple(
        GPUDevice(uuid=uuid, name='A100', memory_mib=81920) for uuid in (
            'GPU-33333333-3333-3333-3333-333333333333', 'GPU-44444444-4444-4444-4444-444444444444')))
    return [(near.profile(a1, profile_id='s1'), a1), (far.profile(a2, profile_id='s2'), a2),
            (other.profile(b, profile_id='s3'), b)]


@pytest.mark.parametrize('provider', ['runpod', 'ssh'])
def test_resolver_aliases_share_load_and_lock(tmp_path, provider):
    if provider == 'runpod':   # p1 and p3 are two profiles of pod1; p2 is pod2
        destinations, (one, alias, other) = pods('pod1', 'pod2', aliases=(('p3', 'pod1'),)), ('p1', 'p3', 'p2')
    else:                      # s1 and s2 are two transports of server a; s3 is server b
        destinations, (one, alias, other) = ssh_destinations(), ('s1', 's2', 's3')
    profiles = {profile.profile_id: (profile, facts) for profile, facts in destinations}
    assert profiles[one][0].exclusivity_key == profiles[alias][0].exclusivity_key   # one key for both aliases
    assert profiles[one][0].exclusivity_key != profiles[other][0].exclusivity_key

    def nodes(r):
        return [evaluate(r, 'x', r.first, (one, other)), evaluate(r, 'y', r.second, (alias, other))]

    for case in ('load', 'lock'):
        w = build(tmp_path / provider / case, nodes, destinations=destinations, max_concurrent=2)
        if case == 'lock':   # an operator hold on the aliased machine blocks both of its profiles
            profile, facts = profiles[one]
            if provider == 'runpod':
                queued(w.store.jobs, 'legacy')
                assert w.store.jobs.claim(facts.exclusivity_key, max_concurrent=8)
            else:
                w.store.jobs.reserve_preparation(profile, facts)
        assert ready(w) == ['x', 'y']
        assert len(prepare(w, 'x', 'y')) == 2
        # load: x took the shared machine, so its alias counts one job there and y goes elsewhere.
        assert (pinned(w, 'x'), pinned(w, 'y')) == ((one, other) if case == 'load' else (other, other))


def test_bindings_for_reuses_the_pinned_destination_and_ledger_evidence(tmp_path, monkeypatch):
    attempt_runner = importlib.import_module('tuning_factory.attempt_runner')
    w = build(tmp_path, lambda r: [train(r, 'a'), train(r, 'b', 'a')])
    store, gs = w.store, api()
    calls = []
    _, executor = gpu_host(tmp_path, monkeypatch, calls)
    runner = attempt_runner.OperationAttemptRunner(store, root=tmp_path / 'state')
    assert prepare(w, 'a', 'b') == [node_row(w, 'a')['job_id']]
    parent = claim(w, 'a')
    outcome = runner.run_claimed(parent, executor, approval=w.approval)
    assert outcome.status == 'COMPLETED', outcome.error
    store.record_outputs(parent['lease'], outcome.outputs)
    assert states(w)['b'] == ('waiting', 'waiting for tracking of a')
    store.acknowledge_tracking(parent['lease'])

    seen, original = [], gs.GraphStore.bindings_for

    def spy(self, run_id, node_id):
        seen.append(original(self, run_id, node_id))
        return seen[-1]

    monkeypatch.setattr(gs.GraphStore, 'bindings_for', spy)
    assert len(prepare(w, 'b')) == 1
    compiled = seen[-1]
    child = claim(w, 'b')
    operation = child['manifest']['operation']
    assert operation['model_input']['initialization'] == 'continue_adapter'
    assert operation['model_input']['policy']['adapter'] == outcome.outputs.model.model.model_dump(mode='json')
    pinned_profile = store.bindings_for(w.run_id, 'b').destination_profile
    assert pinned_profile.model_dump(mode='json') == operation['resources']['executor']
    finished_child = runner.run_claimed(child, executor, approval=w.approval)
    assert finished_child.status == 'COMPLETED', finished_child.error
    continuation = seen[-1]   # the runner's continuation evidence comes from the same trusted hook
    assert (continuation.base_targets, continuation.producers) == (compiled.base_targets, compiled.producers)
    (from_ledger,) = [item for item in compiled.producers if item.node_id == 'a']
    assert (from_ledger.job_id, from_ledger.attempt_id) == (parent['id'], parent['lease'].attempt_id)
    descriptor = store.artifacts.verify(finished_child.outputs.model.model)
    assert outcome.outputs.model.model in descriptor.parents
    assert calls == ['train.bound.yaml', 'train.bound.yaml']


# -- 4-5. readiness gates compilation; failure blocks the subtree --------------------------------

def test_children_compile_only_after_parent_completion_and_tracking_ack(tmp_path):
    w = build(tmp_path, lambda r: [train(r, 'a'), train(r, 'b', 'a')])
    gs = api()
    assert states(w) == {'a': ('ready', None), 'b': ('waiting', 'waiting for a')}
    assert isinstance(w.store.readiness(w.run_id)[0], gs.Readiness)
    assert prepare(w, 'a', 'b') == [node_row(w, 'a')['job_id']]   # b is not ready: never compiled
    assert (node_row(w, 'b')['status'], node_row(w, 'b')['job_id'], node_row(w, 'b')['error']) == (
        'PENDING', None, None)
    claimed, outputs = complete(w, 'a', ack=False)
    assert states(w)['b'] == ('waiting', 'waiting for tracking of a')
    assert prepare(w, 'b') == []
    assert (node_row(w, 'b')['status'], node_row(w, 'b')['error']) == ('PENDING', None)
    w.store.set_wait_reason(w.ctl, 'b', 'waiting for tracking of a')
    assert node_row(w, 'b')['wait_reason'] == 'waiting for tracking of a'
    w.store.acknowledge_tracking(claimed['lease'])
    assert ready(w) == ['b']
    (job_id,) = prepare(w, 'b')
    policy = w.store.jobs.get(job_id)['manifest']['operation']['model_input']['policy']
    assert policy['adapter'] == outputs.model.model.model_dump(mode='json')
    assert states(w)['b'] == ('done', None)
    with pytest.raises(ValueError, match='unknown graph node'):
        prepare(w, 'missing')
    # Without node IDs prepare_ready keeps its pre-Task-8 behaviour: an unready child records its compile error.
    other = build(tmp_path / 'legacy', lambda r: [train(r, 'a'), train(r, 'b', 'a')], resolver=False)
    other.store.prepare_ready(other.ctl)
    assert 'not ready' in node_row(other, 'b')['error'] and node_row(other, 'b')['status'] == 'PENDING'


def test_parent_failure_blocks_descendants(tmp_path, monkeypatch):
    w = build(tmp_path / 'remote', diamond, destinations=pods('pod1', 'pod2'))
    prepare(w, 'a')
    failed(w, 'a', 'remote_failed')   # max_attempts=1: the only attempt is spent
    assert statuses(w) == {'a': 'FAILED', 'b': 'BLOCKED', 'c': 'BLOCKED', 'd': 'BLOCKED'}
    assert node_row(w, 'b')['error'] == 'parent a failed' and node_row(w, 'd')['error'] == 'parent b blocked'
    assert {item.state for item in w.store.readiness(w.run_id)} == {'done'}
    assert w.store.block_descendants(w.ctl, 'a') == ()   # idempotent
    assert prepare(w, 'b', 'c', 'd') == [] and len(w.store.jobs.list()) == 1
    assert w.store.claim_ready(FACTS, w.ctl) is None

    # A ready node whose compilation is refused fails, and its subtree is blocked without a job.
    w = build(tmp_path / 'compile', diamond, destinations=pods('pod1', 'pod2'))
    gs = api()
    original = gs.compile_node

    def refuse(plan, node_id, bindings):
        if node_id == 'a':
            raise ValueError('dataset evidence changed after planning')
        return original(plan, node_id, bindings)

    monkeypatch.setattr(gs, 'compile_node', refuse)
    assert prepare(w, 'a') == []
    assert statuses(w) == {'a': 'FAILED', 'b': 'BLOCKED', 'c': 'BLOCKED', 'd': 'BLOCKED'}
    assert node_row(w, 'a')['error'] == 'dataset evidence changed after planning'
    assert w.store.jobs.list() == []
    with pytest.raises(ValueError, match='only an unfinished node can fail'):
        w.store.fail_node(w.ctl, 'b', 'again')


# -- 6-8. partial join, no winner, atomic local selection ----------------------------------------

@pytest.mark.parametrize('case', ['partial', 'strict', 'one_survivor'])
def test_partial_join_selects_from_surviving_evaluations(tmp_path, case):
    w = evaluations_world(tmp_path, allow_partial=case != 'strict', min_success=2)
    store = w.store
    assert ready(w) == ['e1', 'e2', 'e3']
    prepare(w, 'e1', 'e2', 'e3')
    failed(w, 'e1', 'remote_failed')
    if case == 'strict':
        assert statuses(w)['s'] == 'BLOCKED' and statuses(w)['after'] == 'BLOCKED'
        assert node_row(w, 's')['error'] == 'partial selection is not allowed'
        return
    assert states(w)['s'] == ('waiting', 'waiting for e2')
    if case == 'one_survivor':
        failed(w, 'e2', 'output_invalid')
    else:
        complete(w, 'e2', evaluation_output, accuracy=0.4)
    complete(w, 'e3', evaluation_output, accuracy=0.7)
    (readiness,) = [item for item in store.readiness(w.run_id) if item.node_id == 's']
    assert readiness.state == 'ready'
    assert readiness.failed_parents == (('e1',) if case == 'partial' else ('e1', 'e2'))
    (job_id,) = prepare(w, 's')
    spec = store.jobs.get(job_id)['manifest']['operation']
    assert spec['failed_parent_node_ids'] == list(readiness.failed_parents)
    outputs = store.complete_local_selection(w.ctl, claim(w, 's'))
    if case == 'partial':   # picks between the two survivors
        assert outputs.model.model == w.refs.second and outputs.selection is None
        assert outputs.model.selection.status == 'selected' and len(outputs.model.selection.eligible) == 2
        assert states(w)['after'] == ('waiting', 'waiting for tracking of s')
    else:
        assert outputs.model is None
        assert (outputs.selection.status, outputs.selection.reasons) == ('no_winner',
                                                                         ('insufficient_partial_success',))
        assert states(w)['after'] == ('blocked', 'selection produced no winner')
    assert statuses(w)['s'] == 'COMPLETED'


def test_no_winner_selection_is_recorded_and_blocks_model_children(tmp_path):
    w = evaluations_world(tmp_path, simulation=True)
    store, gs = w.store, api()
    prepare(w, 'e1', 'e2', 'e3')
    for node_id in ('e1', 'e2', 'e3'):
        complete(w, node_id, evaluation_output)
    prepare(w, 's')
    claimed = claim(w, 's')
    assert claimed['lease'].resource_id == 'local:' + w.run_id
    outputs = store.complete_local_selection(w.ctl, claimed)
    assert outputs.model is None
    assert (outputs.selection.status, outputs.selection.reasons) == ('no_winner', ('partial_selection_not_allowed',))
    assert outputs.selection.winner is None
    assert states(w)['after'] == ('blocked', 'selection produced no winner')
    assert store.block_descendants(w.ctl, 's') == ('after',)
    assert statuses(w)['after'] == 'BLOCKED' and node_row(w, 'after')['job_id'] is None
    payloads = {row['node_id']: json.loads(row['outputs']) for row in table(w, 'node_outputs')}
    assert set(payloads['s']) == {'model', 'evaluation', 'inspected_targets', 'selection'}
    assert payloads['s']['model'] is None
    for node_id in ('e1', 'e2', 'e3'):   # earlier receipts keep their exact bytes: no 'selection' key
        row = next(item for item in table(w, 'node_outputs') if item['node_id'] == node_id)
        saved = next(outputs for evidence, outputs in store.ledger(w.run_id) if evidence.node_id == node_id)
        assert row['outputs'] == canonical({'model': None, 'evaluation': saved.evaluation.model_dump(mode='json'),
                                            'inspected_targets': []}).decode()
    (saved,) = [outputs for evidence, outputs in store.ledger(w.run_id) if evidence.node_id == 's']
    assert saved == gs.NodeOutputs(selection=outputs.selection)
    forged = gs.NodeOutputs(selection=outputs.selection.model_copy(update={'reasons': ('forged',)}))
    with pytest.raises(ValueError, match='selection receipt mismatch'):
        store.record_outputs(claimed['lease'], forged)


def test_local_selection_completes_atomically_and_cancel_wins(tmp_path, monkeypatch):
    w = evaluations_world(tmp_path)
    store = w.store
    accuracies = (('e1', 0.2), ('e2', 0.9), ('e3', 0.5))
    prepare(w, 'e1', 'e2', 'e3')
    for node_id, accuracy in accuracies:
        complete(w, node_id, evaluation_output, accuracy=accuracy)
    prepare(w, 's')
    claimed = claim(w, 's')
    job_id, lease = claimed['id'], claimed['lease']
    before = events(w, job_id)
    original = store.jobs._release

    def crash(db, attempt_lease):   # the last step of the transaction, after node_outputs is inserted
        raise Crash('crash after the outputs were inserted')

    monkeypatch.setattr(store.jobs, '_release', crash)
    with pytest.raises(Crash):
        store.complete_local_selection(w.ctl, claimed)
    assert table(w, 'node_outputs', 'WHERE node_id=?', ('s',)) == []   # everything rolled back
    assert table(w, 'tracking_outbox', 'WHERE job_id=?', (job_id,)) == []
    assert store.jobs.get(job_id)['status'] == 'PROVISIONING' and store.jobs.holds_lock(lease)
    assert events(w, job_id) == before and statuses(w)['s'] == 'RUNNING'
    monkeypatch.setattr(store.jobs, '_release', original)
    outputs = store.complete_local_selection(w.ctl, claimed)
    assert outputs.model.model == w.refs.first   # e2 evaluated first, with the best accuracy
    assert store.complete_local_selection(w.ctl, claimed) == outputs   # a rerun records once
    assert len(table(w, 'node_outputs', 'WHERE node_id=?', ('s',))) == 1
    assert len(table(w, 'tracking_outbox', 'WHERE job_id=?', (job_id,))) == 1
    assert events(w, job_id)[len(before):] == [('RUNNING', None), ('EVALUATING', None), ('COMPLETED', None)]
    assert not store.jobs.holds_lock(lease) and statuses(w)['s'] == 'COMPLETED'

    # Cancel wins over the join: a selection claimed before the cancel records nothing.
    for cancel in ('run', 'job'):
        ctl = second_run(w, 'cancel-' + cancel)
        prepare(w, 'e1', 'e2', 'e3', ctl=ctl)
        for node_id, accuracy in accuracies:
            complete(w, node_id, evaluation_output, ctl=ctl, accuracy=accuracy)
        prepare(w, 's', ctl=ctl)
        canceled = claim(w, 's', ctl)
        if cancel == 'run':
            store.cancel(ctl.run_id)
        else:   # a job-level cancel (CLI/desktop accept any job ID) wins as well
            store.jobs.cancel(canceled['id'])
        assert store.complete_local_selection(ctl, canceled) is None
        assert store.jobs.get(canceled['id'])['status'] == 'CANCELED'
        assert statuses(w, ctl.run_id)['s'] == 'CANCELED' and not store.jobs.holds_lock(canceled['lease'])
        assert statuses(w, ctl.run_id)['after'] == ('CANCELED' if cancel == 'run' else 'BLOCKED')
        assert table(w, 'node_outputs', 'WHERE run_id=? AND node_id=?', (ctl.run_id, 's')) == []
        assert events(w, canceled['id'])[-1][0] == 'CANCELED'
        assert store.attempt_disposition(canceled['id'])['counted'] == 0


# -- 9. inspected targets (coordinator item 4) ---------------------------------------------------

def test_inspected_targets_are_verified_and_persisted(tmp_path):
    w = build(tmp_path, lambda r: [materialize(r, 'full', r.first), evaluate(r, 'e', r.first)])
    store, gs = w.store, api()
    prepare(w, 'full', 'e')
    claimed = finished(w, 'full')
    binding = full_output(w, claimed)
    ref, lease = binding.model, claimed['lease']
    targets = inputs.evidence(ref)
    forged = targets.model_copy(update={'modules': (targets.modules[0].model_copy(update={'output_dimension': 3}),
                                                    *targets.modules[1:])})
    for evidence, message in ((forged, 'inspected base dimensions differ from verified tensor inventory'),
                              (inputs.evidence(w.refs.base), 'inspected target evidence belongs to another base')):
        with pytest.raises(ValueError, match=message):
            store.record_outputs(lease, gs.NodeOutputs(model=binding, inspected_targets=(evidence,)))
    outputs = gs.NodeOutputs(model=binding, inspected_targets=(targets,))
    store.record_outputs(lease, outputs)
    store.record_outputs(lease, outputs)   # idempotent: the same immutable receipt
    with pytest.raises(ValueError, match='immutable output receipt mismatch'):
        store.record_outputs(lease, gs.NodeOutputs(model=binding))
    store.acknowledge_tracking(lease)
    (saved,) = [outputs for evidence, outputs in store.ledger(w.run_id) if evidence.node_id == 'full']
    assert saved.inspected_targets == (targets,)
    assert targets in store.bindings_for(w.run_id, 'e').base_targets   # the resolver uses them, re-verified
    evaluation = finished(w, 'e')
    with pytest.raises(ValueError, match='evaluation cannot publish inspected targets'):
        store.record_outputs(evaluation['lease'], gs.NodeOutputs(evaluation=evaluation_output(w, evaluation, 0.5),
                                                                 inspected_targets=(targets,)))
    with store.jobs.connection() as db:   # persisted evidence is re-checked on every read
        payload = json.loads(db.execute("SELECT outputs FROM node_outputs WHERE node_id='full'").fetchone()[0])
        payload['inspected_targets'][0]['modules'][0]['output_dimension'] = 3
        db.execute("UPDATE node_outputs SET outputs=? WHERE node_id='full'", (canonical(payload).decode(),))
    with pytest.raises(ValueError, match='inspected base dimensions differ'):
        store.bindings_for(w.run_id, 'e')


# -- 10-11. dispositions and retries -------------------------------------------------------------

def test_retry_is_bounded_and_backs_off_by_the_injected_clock(tmp_path):
    w = build(tmp_path, lambda r: [train(r, 'a'), train(r, 'b', 'a')], max_attempts=3, backoff=60)
    store, clock = w.store, w.clock
    prepare(w, 'a')
    job_id = node_row(w, 'a')['job_id']
    first = failed(w, 'a', 'remote_failed')   # counted: 1
    assert node_row(w, 'a')['retry_not_before'] == clock.now + 60 and statuses(w)['a'] == 'RUNNING'
    assert not store.requeue_retry(w.ctl, job_id)
    clock.advance(59)
    assert not store.requeue_retry(w.ctl, job_id)
    clock.advance(1)
    assert store.requeue_retry(w.ctl, job_id)
    assert not store.requeue_retry(w.ctl, job_id)   # requeued once
    assert (store.jobs.get(job_id)['status'], statuses(w)['a'], node_row(w, 'a')['retry_not_before']) == (
        'QUEUED', 'READY', None)
    assert events(w, job_id)[-1] == ('QUEUED', 'graph retry')
    with pytest.raises(ValueError, match='stale attempt lease'):
        store.jobs.validated_attempt(first['lease'])   # the finished attempt's actor is fenced
    returned = claim(w, 'a')   # uncounted: provably never launched
    assert store.return_unlaunched(w.ctl, returned['lease'], reason='paused before launch') == 'QUEUED'
    failed(w, 'a', 'remote_failed')   # counted: 2
    clock.advance(60)
    assert store.requeue_retry(w.ctl, job_id)
    released = claim(w, 'a')   # uncounted: stopped before any remote contact
    store.jobs.begin_launch(released['lease'])
    assert store.release_uncontacted(w.ctl, job_id)
    assert store.requeue_retry(w.ctl, job_id)   # no backoff: it never ran
    store.pause(w.run_id)
    assert store.claim_ready(FACTS, w.ctl) is None
    store.resume(w.run_id)
    failed(w, 'a', 'remote_failed')   # counted: 3 = max_attempts
    assert statuses(w) == {'a': 'FAILED', 'b': 'BLOCKED'}
    assert node_row(w, 'a')['retry_not_before'] is None
    clock.advance(600)
    assert not store.requeue_retry(w.ctl, job_id)
    rows = table(w, 'attempt_dispositions')
    assert [(row['disposition'], row['counted']) for row in rows] == [
        ('remote_failed', 1), ('returned_unlaunched', 0), ('remote_failed', 1), ('released_before_contact', 0),
        ('remote_failed', 1)]


@pytest.mark.parametrize('kind', ['output_invalid', 'refused_before_launch', 'failed_before_submit', 'canceled'])
def test_terminal_failures_are_never_retried(tmp_path, kind):
    w = build(tmp_path, lambda r: [train(r, 'a'), train(r, 'b', 'a')], max_attempts=3)
    store = w.store
    prepare(w, 'a')
    claimed = failed(w, 'a', kind)
    final = 'CANCELED' if kind == 'canceled' else 'FAILED'
    assert statuses(w) == {'a': final, 'b': 'BLOCKED'}
    assert node_row(w, 'a')['retry_not_before'] is None
    w.clock.advance(600)
    assert not store.requeue_retry(w.ctl, claimed['id'])
    assert store.attempt_disposition(claimed['id']) == {
        'attempt_id': claimed['lease'].attempt_id, 'disposition': kind, 'counted': 1,
        'detail': store.jobs.get(claimed['id'])['error']}
    assert store.reserved_budget(w.run_id) == 0.5   # it may have run: its reservation is kept
    assert store.classify_attempt(w.ctl, claimed['id'], kind) == kind   # idempotent
    with pytest.raises(ValueError, match='attempt disposition is immutable'):
        store.classify_attempt(w.ctl, claimed['id'], 'remote_failed')
    with pytest.raises(ValueError, match='uncertain attempt is reconciled'):
        store.classify_attempt(w.ctl, claimed['id'], 'uncertain')


def test_an_unlaunched_attempt_failed_by_a_stale_recover_is_uncounted(tmp_path):
    """Coordinator M4: the GRAPH_LAUNCH_REFUSED gate leaves an attempt without a heartbeat; if a stale recover marks
    it FAILED before it is returned, it provably never ran (begin_launch never committed and can no longer commit),
    so it is uncounted and its reservation voided. A refusal by the runner's own checks stays counted (above)."""
    from tuning_factory.jobs import GRAPH_LAUNCH_REFUSED
    w = build(tmp_path, lambda r: [train(r, 'a')])   # max_attempts=1
    store = w.store
    prepare(w, 'a')
    claimed = claim(w, 'a')
    job_id, lease = claimed['id'], claimed['lease']
    store.pause(w.run_id)
    with pytest.raises(ValueError, match=GRAPH_LAUNCH_REFUSED):
        store.jobs.begin_launch(lease)
    store.resume(w.run_id)
    store.jobs.recover(stale_after_seconds=-1)
    assert store.jobs.get(job_id)['status'] == 'FAILED' and store.jobs.holds_lock(lease)
    with pytest.raises(ValueError, match='launch already started or uncertain'):
        store.jobs.begin_launch(lease)   # a FAILED attempt can never begin its launch
    assert store.classify_attempt(w.ctl, job_id, 'refused_before_launch') == 'refused_before_launch'
    assert store.attempt_disposition(job_id)['counted'] == 0
    assert not store.jobs.gpu_locked(lease.resource_id) and store.reserved_budget(w.run_id) == 0
    assert statuses(w)['a'] == 'RUNNING' and store.requeue_retry(w.ctl, job_id)
    assert claim(w, 'a')['lease'].attempt_id != lease.attempt_id   # max_attempts=1 still allows the retry


def test_classification_requires_a_final_attempt_that_matches_its_kind(tmp_path):
    w = build(tmp_path, lambda r: [train(r, 'a')], max_attempts=3)
    store = w.store
    prepare(w, 'a')
    claimed = claim(w, 'a')
    lease = claimed['lease']
    store.jobs.begin_launch(lease)
    store.jobs.transition(lease, 'RUNNING')
    store.jobs.transition(lease, 'FAILED', 'remote execution uncertain')   # the lock is kept: uncertain
    with pytest.raises(ValueError, match='holds its lock'):
        store.classify_attempt(w.ctl, claimed['id'], 'remote_failed')
    store.jobs.release_gpu(lease, execution_stopped=True)
    for kind in ('refused_before_launch', 'canceled'):   # started, and FAILED rather than CANCELED
        with pytest.raises(ValueError, match='failure kind differs from the attempt'):
            store.classify_attempt(w.ctl, claimed['id'], kind)
    with pytest.raises(ValueError, match='unknown failure kind'):
        store.classify_attempt(w.ctl, claimed['id'], 'flaky')
    assert store.attempt_disposition(claimed['id']) is None
    other = second_run(w)
    with pytest.raises(ValueError, match='current graph attempt required'):
        store.classify_attempt(other, claimed['id'], 'remote_failed')   # another run's attempt


# -- 12-14. safe release (D9), run-scoped recovery, finalization ---------------------------------

@pytest.mark.parametrize('case', ['uncontacted', 'record_present', 'running', 'recovered', 'never_started'])
def test_release_uncontacted_only_with_provable_non_contact(tmp_path, case):
    gs = api()
    w = build(tmp_path, lambda r: [train(r, 'a')])
    store = w.store
    prepare(w, 'a')
    claimed = claim(w, 'a')
    job_id, lease = claimed['id'], claimed['lease']
    if case != 'never_started':
        store.jobs.begin_launch(lease)
    if case == 'record_present':
        launch = tmp_path / 'state' / 'jobs' / job_id / 'launches' / claimed['launch_id']
        launch.mkdir(parents=True)
        (launch / gs.REMOTE_ATTEMPT).write_text('{}', encoding='utf-8')
    elif case == 'running':
        store.jobs.transition(lease, 'RUNNING')
    elif case == 'recovered':
        store.jobs.recover(stale_after_seconds=-1)
    status = store.jobs.get(job_id)['status']
    released = store.release_uncontacted(w.ctl, job_id)
    if case != 'uncontacted':
        assert released is False
        assert store.jobs.get(job_id)['status'] == status and store.jobs.gpu_locked(lease.resource_id)
        assert table(w, 'attempt_dispositions') == [] and store.reserved_budget(w.run_id) == 0.5
        if case == 'record_present':   # taken over (its actor is fenced), but it stays for reconciliation
            with pytest.raises(ValueError, match='stale attempt lease'):
                store.jobs.validated_attempt(lease)
        else:
            assert store.jobs.validated_attempt(lease)['active_attempt_id'] == lease.attempt_id
        return
    assert released is True
    job = store.jobs.get(job_id)
    assert (job['status'], job['error']) == ('FAILED', gs.RELEASED_BEFORE_CONTACT)
    assert events(w, job_id)[-1] == ('FAILED', gs.RELEASED_BEFORE_CONTACT)
    assert not store.jobs.gpu_locked(lease.resource_id) and store.reserved_budget(w.run_id) == 0
    assert store.attempt_disposition(job_id) == {'attempt_id': lease.attempt_id,
                                                 'disposition': 'released_before_contact', 'counted': 0,
                                                 'detail': gs.RELEASED_BEFORE_CONTACT}
    with pytest.raises(ValueError, match='stale attempt lease'):
        store.jobs.transition(lease, 'RUNNING')   # the fenced old actor can never reach run_operation
    assert store.requeue_retry(w.ctl, job_id)
    again = claim(w, 'a')   # max_attempts=1, and the released attempt was not counted
    assert again['lease'].attempt_id != lease.attempt_id


def test_recover_stale_is_run_scoped(tmp_path):
    w = build(tmp_path, lambda r: [train(r, node_id, profiles=(profile,)) for node_id, profile in (
        ('a', 'p1'), ('b', 'p2'), ('c', 'p3'), ('d', 'p4'))],
              destinations=pods('pod1', 'pod2', 'pod3', 'pod4'), max_concurrent=4)
    store = w.store
    prepare(w, 'a', 'b', 'c')
    running = claim(w, 'a')
    store.jobs.begin_launch(running['lease'])
    store.jobs.transition(running['lease'], 'RUNNING')
    started = claim(w, 'b')   # PROVISIONING after begin_launch: release_uncontacted's territory
    store.jobs.begin_launch(started['lease'])
    unstarted = claim(w, 'c')   # PROVISIONING before begin_launch: returned unlaunched, never failed here
    ctl = second_run(w)
    prepare(w, 'd', ctl=ctl)
    foreign = claim(w, 'd', ctl)
    store.jobs.begin_launch(foreign['lease'])
    store.jobs.transition(foreign['lease'], 'RUNNING')
    queued(store.jobs, 'legacy')
    legacy = store.jobs.claim('existing_runpod:pod9', max_concurrent=8)
    store.jobs.transition(legacy['lease'], 'RUNNING')
    assert store.recover_stale(w.ctl, stale_before=time.time() - 3600) == ()   # nothing is that old
    assert store.recover_stale(w.ctl, stale_before=time.time() + 1) == (running['id'],)
    job = store.jobs.get(running['id'])
    assert job['status'] == 'FAILED' and 'heartbeat expired' in job['error']
    assert store.jobs.holds_lock(running['lease'])   # still uncertain: reconcile before releasing
    for claimed, status in ((started, 'PROVISIONING'), (unstarted, 'PROVISIONING'), (foreign, 'RUNNING'),
                            (legacy, 'RUNNING')):
        assert store.jobs.get(claimed['id'])['status'] == status


def test_finalize_run_statuses(tmp_path):
    for case in ('completed', 'failed', 'canceled', 'job_canceled'):
        w = build(tmp_path / case, lambda r: [train(r, 'a'), train(r, 'b', 'a')])
        store, ctl = w.store, w.ctl
        prepare(w, 'a')
        assert store.finalize_run(ctl) is None
        if case == 'completed':
            complete(w, 'a')
            prepare(w, 'b')
            claimed = finished(w, 'b', release=False)   # a crash before release_gpu keeps the lock
            store.record_outputs(claimed['lease'], api().NodeOutputs(model=adapter_output(w, claimed)))
            assert set(statuses(w).values()) == {'COMPLETED'}
            assert store.finalize_run(ctl) is None   # a held lock prevents finalization
            store.jobs.release_gpu(claimed['lease'], execution_stopped=True)
            # Tracking never holds back finalization (D5): b's tracking is still unacknowledged.
        elif case == 'failed':
            failed(w, 'a', 'failed_before_submit')
        elif case == 'canceled':
            claimed = claim(w, 'a')
            store.cancel(w.run_id)
            assert store.return_unlaunched(ctl, claimed['lease'], reason='canceled before launch') == 'CANCELED'
        else:   # one node's job canceled while its run keeps running: its child is blocked
            failed(w, 'a', 'canceled')
        expected = {'completed': 'COMPLETED', 'failed': 'FAILED', 'canceled': 'CANCELED',
                    'job_canceled': 'FAILED'}[case]
        assert store.finalize_run(ctl) == expected
        assert store.finalize_run(ctl) == expected and store.get_run(w.run_id)['status'] == expected
