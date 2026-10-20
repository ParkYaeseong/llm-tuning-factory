"""Task 8.4: the simulated dispatcher for dry-run graphs (plan §5, Q21) and the simulated executor provider.

Main env, fakes only: no GPU, SSH, network, provider, model download or MLflow. This module imports only light modules
at top level (no test_operation_runner, which pulls paramiko), so the full simulated chain can also run in a
subprocess that proves nothing network-capable or heavy was imported.
"""
import dataclasses
import importlib
import itertools
import json
import os
import socket
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
import test_model_inputs as inputs

from tuning_factory import graph_store as graph_store_module
from tuning_factory import jobs as jobs_module
from tuning_factory.benchmarks import BenchmarkSpec, SelectionSpec, select_best
from tuning_factory.executor_profiles import ExecutorProfile, ResourceFacts, observation_fits
from tuning_factory.graph_planner import (
    ApprovalGrant,
    BenchmarkRevision,
    ExecutionChoices,
    ModelContext,
    NodeResources,
    ProducerEvidence,
    TrackingDestination,
    plan_graph,
    snapshot_artifact,
    snapshot_dataset,
)
from tuning_factory.graph_schema import GraphSpec
from tuning_factory.graph_store import REMOTE_ATTEMPT, GraphStore, LedgerBindingResolver, accepted_result
from tuning_factory.model_artifacts import ModelInput, ModelLoadRef
from tuning_factory.model_ops import continuation_mismatches, expected_continuation_adapter
from tuning_factory.operations import OperationResources
from tuning_factory.projects import DatasetBinding, DatasetRef, ProjectSpec, Revision
from tuning_factory.recipes import RecipeSpec
from tuning_factory.registry import canonical, digest

FORBIDDEN = ("paramiko", "mlflow", "torch", "transformers", "peft", "safetensors", "huggingface_hub",
             "tuning_factory.desktop_runtime", "tuning_factory.execution", "tuning_factory.attempt_runner")
EXECUTOR = ExecutorProfile(profile_id="p", revision="1", name="Pod", pod_id="pod1", credential_scope="local",
                           transport_settings_sha256="1" * 64)
FACTS = ResourceFacts(pod_id="pod1", gpu_type="A100", gpu_count=1, total_hourly_cost_usd=2,
                      observed_at="2026-10-20T00:00:00Z")
TRACKING = TrackingDestination(profile_id="mlflow", revision="1", public_settings_sha256="2" * 64)
TRAIN_ROWS = [{"instruction": "train question", "output": "train answer"}]
HELD_ROWS = [{"instruction": "held-out first", "output": "positive"},
             {"instruction": "held-out second", "output": "negative"}]
SIMULATED_RESOURCE = "simulation:existing_runpod:pod1"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Simulation never opens a connection: local SQLite, local files and fakes only."""
    def refuse(*args, **kwargs):
        raise AssertionError("simulated dispatch must not open a network connection")
    monkeypatch.setattr(socket.socket, "connect", refuse)


def api():
    # A missing implementation is a test failure, rather than a collection error.
    try:
        return importlib.import_module("tuning_factory.simulated_dispatch")
    except ModuleNotFoundError:
        pytest.fail("the simulated dispatcher (Task 8.4) is missing")


def providers():
    module = importlib.import_module("tuning_factory.executor_profiles")
    if not hasattr(module, "SimulatedExecutorProvider"):
        pytest.fail("executor_profiles.SimulatedExecutorProvider (Task 8.4) is missing")
    return module


class Crash(BaseException):
    """A process crash at a chosen step: it escapes every ``except Exception``, so nothing after it runs."""


class FakeClock:
    """Wall-clock compatible: it starts at time.time(), because attempt heartbeats are written in wall time."""
    def __init__(self, start=None):
        self.now = time.time() if start is None else start

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


# -- graph definitions ---------------------------------------------------------------------------

def revision(spec):
    return Revision(spec=spec, revision_sha256=digest(canonical(spec.model_dump(mode="json"))))


def register(registry, tmp_path, name, rows):
    path = tmp_path / f"{name}.json"
    path.write_bytes(canonical(rows))
    record = registry.register_dataset(name, "1", path, export_allowed=True)
    return DatasetRef(dataset_id=name, version="1", sha256=record["sha256"])


def producer(ref, marker):
    return ProducerEvidence(model=ref, job_id=marker, attempt_id=marker, manifest_sha256="b" * 64,
                            runtime_profile="primary", training_stage="sft", status="completed",
                            tracking_acknowledged=True)


def node_train(r, node_id, source=None, *, base=None, recipe=None):
    """A training of ``base`` (the base artifact by default), or a continuation of ``source``'s adapter."""
    recipe = recipe or r.recipe
    if source:
        model_input = {"node_id": source, "output_name": "model"}
    else:
        initialization = "full_weights" if recipe is r.full_recipe else "new_adapter"
        model_input = ModelInput(policy=ModelLoadRef(base=base or r.base),
                                 initialization=initialization).model_dump(mode="json")
    return {"kind": "train", "id": node_id, "label": "Train", "recipe_revision": recipe.revision_sha256,
            "model_input": model_input, "dataset_binding": "data", "executor_profile_ids": ["p"]}


def _model(value):
    return {"node_id": value, "output_name": "model"} if isinstance(value, str) else value.model_dump(mode="json")


def node_evaluate(r, node_id, model):
    return {"kind": "evaluate", "id": node_id, "label": "Evaluate", "model": _model(model),
            "benchmark_revision": r.benchmark.fingerprint(), "executor_profile_ids": ["p"]}


def node_select(node_id, evaluations, **selection):
    return {"kind": "select", "id": node_id, "label": "Select",
            "evaluations": [{"node_id": item, "output_name": "evaluation"} for item in evaluations],
            "selection": {"metric": "accuracy", "direction": "max", **selection}}


def node_merge(node_id, models):
    return {"kind": "merge", "id": node_id, "label": "Merge", "models": [_model(item) for item in models],
            "weights": [0.25, 0.75], "executor_profile_ids": ["p"]}


def node_materialize(node_id, model):
    return {"kind": "materialize", "id": node_id, "label": "Materialize", "model": _model(model),
            "executor_profile_ids": ["p"]}


def build(tmp_path, nodes, *, simulation=True, depends_on=(), max_attempts=1, root_evidence=True):
    """A submitted plan over frozen base/adapter artifacts in the GraphStore's own store, with the trusted ledger
    resolver installed and a run lease. Nothing is prepared: the caller drives readiness.

    ``root_evidence`` adds inspected target evidence of the HF root itself (as Task 9's inspector would supply)."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    clock = FakeClock()
    store = GraphStore(tmp_path / "state" / "jobs.db", clock=clock)
    registry, models = store.registry, store.artifacts
    base = models.freeze(*inputs.full_model(tmp_path, "base"))
    first = models.freeze(*inputs.peft_adapter(tmp_path, "first", base=base, parents=(base,)))
    second = models.freeze(*inputs.peft_adapter(tmp_path, "second", rank=3, alpha=9, base=base, parents=(base,)))
    data = register(registry, tmp_path, "data", TRAIN_ROWS)
    held = register(registry, tmp_path, "held", HELD_ROWS)
    recipe = revision(RecipeSpec(recipe_id="r", name="recipe",
                                 hyperparameters={"lora_rank": 2, "lora_target": "q_proj,v_proj"}))
    all_recipe = revision(RecipeSpec(recipe_id="all", name="all", hyperparameters={"lora_rank": 2,
                                                                                   "lora_target": "all"}))
    full_recipe = revision(RecipeSpec(recipe_id="full", name="full", training_method="full"))
    benchmark = BenchmarkSpec(dataset=held, evaluator_kind="classification", evaluator_version="1",
                              ordered_sample_ids=("row:1", "row:0"), max_new_tokens=8, cutoff_len=64, seed=1,
                              precision="bf16", quantization_bit=None,
                              tokenizer_fingerprint=inputs.ROOT.tokenizer_fingerprint, template="qwen",
                              semantic_model_kind="causal_lm")
    refs = SimpleNamespace(base=base, first=first, second=second, recipe=recipe, all_recipe=all_recipe,
                           full_recipe=full_recipe, benchmark=benchmark, root=inputs.ROOT)
    graph = GraphSpec.model_validate({
        "nodes": nodes(refs), "retry_policy": {"max_attempts": max_attempts},
        "depends_on": [{"node_id": child, "depends_on": parent} for child, parent in depends_on]})
    project = revision(ProjectSpec(project_id="project.1", name="Project", datasets=(
        DatasetBinding(binding_id="data", role="train", dataset=data),
        DatasetBinding(binding_id="held", role="validation", dataset=held)), graph=graph.model_dump(mode="json")))
    producers = tuple(producer(ref, marker) for marker, ref in (("base", base), ("first", first), ("second", second)))
    targets = (inputs.evidence(base), *((inputs.evidence(inputs.ROOT),) if root_evidence else ()))
    choices = ExecutionChoices(
        recipes=(recipe, all_recipe, full_recipe),
        benchmarks=(BenchmarkRevision(spec=benchmark, revision_sha256=benchmark.fingerprint()),),
        datasets=(snapshot_dataset(registry, data), snapshot_dataset(registry, held)),
        models=(ModelContext(root=inputs.ROOT, profile=inputs.PROFILE),),
        artifacts=tuple(snapshot_artifact(models, item.model, item) for item in producers), executors=(EXECUTOR,),
        node_resources=tuple(NodeResources(node_id=node.id, timeout_seconds=900) for node in graph.nodes
                             if node.kind != "select"),
        base_targets=targets, tracking_destination=TRACKING)
    plan = plan_graph(project, choices, (FACTS,), simulation=simulation)
    approval = ApprovalGrant.for_plan(plan)
    run = store.submit(plan, "run", approval)
    store.binding_resolver = LedgerBindingResolver(store)
    ctl = store.acquire_controller(run["id"], ttl_seconds=3600)
    return SimpleNamespace(store=store, plan=plan, approval=approval, run_id=run["id"], ctl=ctl, refs=refs,
                           clock=clock, tmp=tmp_path, state=tmp_path / "state")


def write_root_snapshot(tmp_path, *, config=inputs.CONFIG):
    """A local config/tokenizer snapshot of the HF root, as DirectoryRootSnapshots lays it out."""
    owner, name = inputs.ROOT.repo_id.split("/")
    directory = tmp_path / "roots" / owner / name / inputs.ROOT.revision
    inputs.write_snapshot(directory, config=config)
    return directory


def dispatcher(w, *, roots=True):
    sd = api()
    source = sd.DirectoryRootSnapshots(w.tmp / "roots") if roots else None
    return sd.SimulatedOperationDispatcher(w.store, root=w.state, roots=source)


def prepare(w, node_id):
    jobs = w.store.prepare_ready(w.ctl, node_ids=[node_id])
    node = next(item for item in w.store.get_run(w.run_id)["nodes"] if item["node_id"] == node_id)
    assert jobs, node["error"]
    return jobs[0]


def pinned_resources(w, job_id):
    return OperationResources.model_validate(w.store.jobs.get(job_id)["manifest"]["operation"]["resources"])


def claim_job(w, job_id):
    """Claim a queued operation with the simulated provider's observation of its pinned destination."""
    executor = pinned_resources(w, job_id).executor
    provider = providers().SimulatedExecutorProvider.for_plan(w.plan, clock=w.clock)
    with provider.resolve(executor.profile_id, executor.revision, pinned=executor) as resolved:
        assert resolved.executor is None
        claimed = w.store.claim_ready(resolved.observation, w.ctl)
    assert claimed["id"] == job_id and claimed["lease"].resource_id == SIMULATED_RESOURCE
    return claimed


def claim(w, node_id):
    return claim_job(w, prepare(w, node_id))


def record(w, claimed, outcome):
    """What 8.5 does with a COMPLETED outcome: persist outputs (with verified targets) and the tracking receipt."""
    outputs = dataclasses.replace(outcome.outputs, inspected_targets=outcome.inspected_targets)
    w.store.record_outputs(claimed["lease"], outputs)
    w.store.acknowledge_tracking(claimed["lease"])


def run_node(w, d, node_id):
    claimed = claim(w, node_id)
    outcome = d.run_claimed(claimed, None, approval=w.approval)
    assert outcome.status == "COMPLETED", outcome.error
    record(w, claimed, outcome)
    return claimed, outcome


def run_select(w, node_id):
    prepare(w, node_id)
    claimed = w.store.claim_ready(None, w.ctl)
    outputs = w.store.complete_local_selection(w.ctl, claimed)
    w.store.acknowledge_tracking(claimed["lease"])
    return outputs


def launch_dir(w, claimed):
    return w.state / "jobs" / claimed["id"] / "launches" / claimed["launch_id"]


def artifact_entries(store):
    return sorted(path.parent.name + "/" + path.name for path in store.artifacts.root.glob("*/*")
                  if not path.parent.name.startswith("."))


def attempt(w, claimed):
    return w.store.jobs.validated_attempt(w.store.jobs.current_lease(claimed["id"]))


def held(w):
    return w.store.jobs.gpu_locked(SIMULATED_RESOURCE)


def execution_spy(monkeypatch, *, fail=None):
    """Record SimulatedWorker.execute launch directories; while ``spy.fail`` is set, raise it before any write."""
    sd = api()
    spy = SimpleNamespace(calls=[], fail=fail)
    real = sd.SimulatedWorker.execute

    def execute(self, *args, **kwargs):
        spy.calls.append(args[1])
        if spy.fail is not None:
            raise spy.fail
        return real(self, *args, **kwargs)

    monkeypatch.setattr(sd.SimulatedWorker, "execute", execute)
    return spy


CHAIN_NODES = ("train", "continue", "merge", "full", "eval")


def chain(r):
    """train (HF root) -> continue; merge (real adapters, after continue) -> materialize -> evaluate -> select."""
    return [node_train(r, "train", base=r.root), node_train(r, "continue", "train"),
            node_merge("merge", (r.first, r.second)), node_materialize("full", "merge"),
            node_evaluate(r, "eval", "full"), node_select("select", ["eval"])]


def full_chain(tmp_path):
    """Run the whole simulated chain child by child, as 8.5's controller will; also used by the subprocess test."""
    w = build(tmp_path, chain, depends_on=(("merge", "continue"),))
    write_root_snapshot(tmp_path)
    d = dispatcher(w)
    outcomes = {}
    for node_id in CHAIN_NODES:
        ready = [item.node_id for item in w.store.readiness(w.run_id) if item.state == "ready"]
        assert ready == [node_id], ready   # each child compiles only after its parent's evidence was saved
        outcomes[node_id] = run_node(w, d, node_id)
    selection = run_select(w, "select")
    return w, outcomes, selection


# -- 7. provider ---------------------------------------------------------------------------------

def subprocess_modules(code, *args, cwd):
    """Run ``code`` in a fresh interpreter of this environment and return the JSON it prints last."""
    import tuning_factory
    paths = [str(Path(tuning_factory.__file__).resolve().parents[1]), str(Path(__file__).resolve().parent)]
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([*paths, os.environ.get("PYTHONPATH", "")])}
    result = subprocess.run([sys.executable, "-c", code, *map(str, args)], capture_output=True, text=True,
                            cwd=cwd, env=env, timeout=600)
    assert result.returncode == 0, result.stderr[-4000:]
    return json.loads(result.stdout.strip().splitlines()[-1])


PROVIDER_PROBE = """
import json, socket, sys
def refuse(*args, **kwargs):
    raise AssertionError("network")
socket.socket.connect = refuse
from tuning_factory.executor_profiles import ExecutorProfile, ResourceFacts, SimulatedExecutorProvider, observation_fits
profile = ExecutorProfile(profile_id="p", revision="1", name="Pod", pod_id="pod1", credential_scope="local")
facts = ResourceFacts(pod_id="pod1", gpu_type="A100", gpu_count=1, total_hourly_cost_usd=2,
                      observed_at="2026-10-20T00:00:00Z")
provider = SimulatedExecutorProvider((facts,), clock=lambda: 1.8e9)
with provider.resolve("p", "1", pinned=profile) as resolved:
    assert resolved.executor is None and observation_fits(resolved.observation, facts)
with provider.recover("p", "1", pinned=profile, pinned_facts=facts, manifest_sha256="1" * 64) as executor:
    assert executor is None
print(json.dumps(sorted(name for name in sys.argv[1:] if name in sys.modules)))
"""


def test_simulated_provider_contacts_nothing(tmp_path):
    ep = providers()
    w = build(tmp_path, lambda r: [node_train(r, "train")])
    resources = w.plan.nodes[0].allowed_resources[0]
    provider = ep.SimulatedExecutorProvider.for_plan(w.plan, clock=lambda: 1.8e9)
    job_id = prepare(w, "train")
    with provider.resolve("p", "1", pinned=resources.executor, runtime_profile="primary") as resolved:
        assert resolved.executor is None and resolved.profile == resources.executor
        assert observation_fits(resolved.observation, resources.facts)
        assert resolved.observation.observed_at == datetime.fromtimestamp(1.8e9, timezone.utc)
        claimed = w.store.claim_ready(resolved.observation, w.ctl)
    assert claimed["id"] == job_id and claimed["lease"].resource_id == SIMULATED_RESOURCE
    with provider.recover("p", "1", pinned=resources.executor, pinned_facts=resources.facts,
                          manifest_sha256="1" * 64) as executor:
        assert executor is None
    other = resources.executor.model_copy(update={"pod_id": "pod2"})
    for profile_id, approved, pinned, message in (
            ("p", "2", resources.executor, ep.PROFILE_CHANGED),
            ("q", "1", resources.executor, ep.PROFILE_CHANGED),
            ("p", "1", other, ep.PROFILE_MISSING)):
        with pytest.raises(ValueError) as error:
            with provider.resolve(profile_id, approved, pinned=pinned):
                pytest.fail("a changed or unknown destination resolved")
        assert str(error.value) == message
    assert provider.status() == {"provider": "simulated", "destinations": ["existing_runpod:pod1"]}
    real = build(tmp_path / "real", lambda r: [node_train(r, "train")], simulation=False)
    with pytest.raises(ValueError, match="simulation plans only"):
        ep.SimulatedExecutorProvider.for_plan(real.plan)
    assert subprocess_modules(PROVIDER_PROBE, *FORBIDDEN, cwd=tmp_path) == []


# -- 8. refusals before launch -------------------------------------------------------------------

def test_simulated_dispatch_refuses_real_manifests(tmp_path):
    sd = api()
    w = build(tmp_path, lambda r: [node_train(r, "train")], simulation=False)
    prepare(w, "train")
    claimed = w.store.claim_ready(FACTS, w.ctl)
    d = dispatcher(w)
    outcome = d.run_claimed(claimed, None, approval=w.approval)
    assert (outcome.status, outcome.uncertain, outcome.outputs) == ("FAILED", False, None)
    assert outcome.error == "ValueError: " + sd.SIMULATION_ONLY
    job = w.store.jobs.get(claimed["id"])
    assert job["status"] == "FAILED" and attempt(w, claimed)["launch_started"] == 0
    assert not w.store.jobs.gpu_locked("existing_runpod:pod1")   # released atomically by abandon_unlaunched
    assert not (w.state / "jobs" / claimed["id"] / "launches").exists()
    assert (w.state / "jobs" / claimed["id"] / "error.txt").read_text(encoding="utf-8") == outcome.error
    # A deterministic refusal is counted and terminal (8.2 M4 applies only to a refusal that kept its lock).
    assert d.failure_kind(claimed["id"]) == "refused_before_launch"
    assert w.store.classify_attempt(w.ctl, claimed["id"], "refused_before_launch") == "refused_before_launch"
    assert w.store.attempt_disposition(claimed["id"])["counted"] == 1
    for action in (lambda: d.reconcile(claimed["id"], None), lambda: d.outputs_for(claimed["id"])):
        with pytest.raises(ValueError) as error:
            action()
        assert str(error.value) == sd.SIMULATION_ONLY   # a real attempt is never settled by simulation


def test_simulated_dispatch_refuses_an_executor_or_a_foreign_approval(tmp_path):
    sd = api()
    w = build(tmp_path, lambda r: [node_train(r, "train"), node_train(r, "second")])
    d = dispatcher(w)
    claimed = claim(w, "train")
    outcome = d.run_claimed(claimed, SimpleNamespace(remote=True), approval=w.approval)
    assert outcome.error == "ValueError: " + sd.NO_EXECUTOR
    assert not held(w) and not launch_dir(w, claimed).exists()
    other = claim(w, "second")
    foreign = ApprovalGrant(plan_sha256="f" * 64, destination_profile_revisions=w.approval.destination_profile_revisions)
    outcome = d.run_claimed(other, None, approval=foreign)
    assert outcome.status == "FAILED" and "approval does not match" in outcome.error
    assert not held(w) and not launch_dir(w, other).exists()


# -- 9. a simulated train through the real freezer -----------------------------------------------

def test_simulated_train_freezes_through_build_output_descriptor(tmp_path, monkeypatch):
    from tuning_factory import operation_outputs
    sd, built = api(), []
    real = operation_outputs.build_output_descriptor

    def spy(*args, **kwargs):
        source, descriptor = real(*args, **kwargs)
        built.append(descriptor)
        return source, descriptor

    monkeypatch.setattr(operation_outputs, "build_output_descriptor", spy)
    w = build(tmp_path, lambda r: [node_train(r, "train")])
    claimed, outcome = run_node(w, dispatcher(w), "train")
    ref = outcome.outputs.model.model
    descriptor = w.store.artifacts.verify(ref)
    job = w.store.jobs.get(claimed["id"])
    assert built == [descriptor] and outcome.inspected_targets == ()
    assert descriptor.simulation is True and not descriptor.eligible_for_real_use and not descriptor.synthetic
    assert (descriptor.producer_job_id, descriptor.producer_attempt_id, descriptor.producer_manifest_sha256) == (
        claimed["id"], claimed["lease"].attempt_id, job["manifest"]["sha256"])
    assert (descriptor.root, descriptor.effective_base, descriptor.parents) == (inputs.ROOT, w.refs.base,
                                                                                (w.refs.base,))
    files, base_files = w.store.artifacts.path(ref), w.store.artifacts.path(w.refs.base)
    for name in ("config.json", *inputs.TOKENIZER):
        assert (files / name).read_bytes() == (base_files / name).read_bytes()
    exported = json.loads((files / "adapter_config.json").read_bytes())
    assert {key: exported[key] for key in ("r", "lora_alpha", "target_modules", "task_type", "inference_mode",
                                           "base_model_name_or_path")} == {
        "r": 2, "lora_alpha": 4, "target_modules": ["q_proj", "v_proj"], "task_type": "CAUSAL_LM",
        "inference_mode": True, "base_model_name_or_path": inputs.ROOT.repo_id}
    config = w.plan.execution_choices.recipes[0].spec
    assert continuation_mismatches(descriptor.adapter, expected_continuation_adapter(
        config, descriptor.adapter.target_modules)) == ()
    assert {tensor.name: tensor.shape for tensor in descriptor.tensors} == {   # the base evidence's dimensions
        "base_model.model.model.q_proj.lora_A.weight": (2, 2), "base_model.model.model.q_proj.lora_B.weight": (2, 2),
        "base_model.model.model.v_proj.lora_A.weight": (2, 2), "base_model.model.model.v_proj.lora_B.weight": (2, 2)}
    assert job["status"] == "COMPLETED" and not held(w)
    assert w.store.ledger(w.run_id)[0][1].model == outcome.outputs.model   # record_outputs accepted it
    launch = launch_dir(w, claimed)
    result = accepted_result(w.state, claimed["id"], claimed["launch_id"], claimed["lease"].attempt_id)
    assert result.status == "COMPLETED" and result.engine_version == "simulated"
    assert {"model-inputs.json", "train.yaml", sd.SIMULATED_MARKER, "base-snapshot/config.json",
            "output/adapter_config.json", "output/adapter_model.safetensors"} <= set(result.evidence)
    assert json.loads((launch / sd.SIMULATED_MARKER).read_bytes())["simulated"] is True
    marker = json.loads((launch / REMOTE_ATTEMPT).read_bytes())   # the documented local record: no contact
    assert marker == {"schema_version": 1, "simulated": True, "contact": "none", "attempt_id":
                      claimed["lease"].attempt_id, "launch_id": claimed["launch_id"],
                      "manifest_sha256": job["manifest"]["sha256"]}


# -- 10. the whole chain, child by child ---------------------------------------------------------

def test_simulated_outputs_chain_through_compile_node(tmp_path):
    w, outcomes, selection = full_chain(tmp_path)
    store = w.store
    models = {node_id: outcomes[node_id][1].outputs.model.model for node_id in ("train", "continue", "merge", "full")}
    descriptors = {node_id: store.artifacts.verify(ref) for node_id, ref in models.items()}
    assert all(item.simulation for item in descriptors.values())
    assert descriptors["train"].effective_base == inputs.ROOT and descriptors["train"].parents == ()
    assert descriptors["continue"].effective_base == inputs.ROOT
    assert descriptors["continue"].parents == (models["train"],)   # the continued adapter is the train output
    assert descriptors["merge"].adapter.rank == 5 and descriptors["merge"].effective_base == w.refs.base
    assert set(descriptors["merge"].parents) == {w.refs.first, w.refs.second, w.refs.base}
    assert descriptors["full"].storage_kind == "full_model" and models["merge"] in descriptors["full"].parents
    targets = outcomes["full"][1].inspected_targets
    assert [item.base for item in targets] == [models["full"]]
    ledger = {evidence.node_id: saved for evidence, saved in store.ledger(w.run_id)}
    assert ledger["full"].inspected_targets == targets   # persisted through the verifier
    record = ledger["eval"].evaluation.evaluation
    assert record.simulation is True and record.model == models["full"]
    assert selection.model is None and selection.selection.status == "no_winner"
    assert ledger["select"].selection == selection.selection
    assert store.finalize_run(w.ctl) == "COMPLETED"


# -- 11. HF roots need a local snapshot ----------------------------------------------------------

class WrongSnapshot:
    """An untrusted source that returns a snapshot of another config: the dispatcher re-checks it."""
    def __init__(self, directory):
        self.directory = directory

    def snapshot(self, root):
        return self.directory


@pytest.mark.parametrize("case", ["missing", "wrong_config", "no_source", "untrusted_source", "present"])
def test_simulated_hf_root_requires_a_local_snapshot(tmp_path, monkeypatch, case):
    sd = api()
    w = build(tmp_path, lambda r: [node_train(r, "train", base=r.root)], root_evidence=False)
    if case in {"wrong_config", "untrusted_source"}:
        wrong = write_root_snapshot(tmp_path, config=canonical({"model_type": "other"}))
    elif case == "present":
        snapshot = write_root_snapshot(tmp_path)
    d = dispatcher(w, roots=case != "no_source")
    if case == "untrusted_source":
        d = sd.SimulatedOperationDispatcher(w.store, root=w.state, roots=WrongSnapshot(wrong))
    calls = execution_spy(monkeypatch).calls
    claimed = claim(w, "train")
    outcome = d.run_claimed(claimed, None, approval=w.approval)
    if case == "present":
        assert outcome.status == "COMPLETED"
        ref = outcome.outputs.model.model
        files = w.store.artifacts.path(ref)
        for name in ("config.json", *inputs.TOKENIZER):
            assert (files / name).read_bytes() == (snapshot / name).read_bytes()
        # Without evidence of the root, an explicit lora_target uses the documented model.<target> placeholder.
        assert {tensor.name: tensor.shape for tensor in w.store.artifacts.verify(ref).tensors} == {
            "base_model.model.model.q_proj.lora_A.weight": (2, 1), "base_model.model.model.q_proj.lora_B.weight": (1, 2),
            "base_model.model.model.v_proj.lora_A.weight": (2, 1), "base_model.model.model.v_proj.lora_B.weight": (1, 2)}
        return
    message = sd.ROOT_SNAPSHOT_MISSING.format(repo=inputs.ROOT.repo_id, revision=inputs.ROOT.revision)
    assert outcome.status == "FAILED" and outcome.error == "ValueError: " + message
    assert attempt(w, claimed)["launch_started"] == 0 and calls == []   # refused before begin_launch
    assert not held(w) and not launch_dir(w, claimed).exists()
    assert d.failure_kind(claimed["id"]) == "refused_before_launch"


@pytest.mark.parametrize("case", ["full_on_root", "all_targets_on_root"])
def test_simulated_targets_the_worker_cannot_invent_are_refused_before_launch(tmp_path, case):
    sd = api()

    def nodes(r):
        recipe = r.full_recipe if case == "full_on_root" else r.all_recipe
        return [node_train(r, "train", base=r.root, recipe=recipe)]

    w = build(tmp_path, nodes, root_evidence=False)
    write_root_snapshot(tmp_path)
    claimed = claim(w, "train")
    outcome = dispatcher(w).run_claimed(claimed, None, approval=w.approval)
    assert outcome.error == "ValueError: " + sd.TARGETS_REQUIRED.format(kind="train")
    assert attempt(w, claimed)["launch_started"] == 0 and not held(w) and not launch_dir(w, claimed).exists()


def test_simulated_full_training_carries_verified_target_inventory(tmp_path):
    w = build(tmp_path, lambda r: [node_train(r, "train", recipe=r.full_recipe)])
    claimed, outcome = run_node(w, dispatcher(w), "train")
    ref = outcome.outputs.model.model
    descriptor = w.store.artifacts.verify(ref)
    assert descriptor.storage_kind == "full_model" and descriptor.simulation
    assert {tensor.name: tensor.shape for tensor in descriptor.tensors} == {
        "model.q_proj.weight": (2, 2), "model.v_proj.weight": (2, 2)}
    assert [item.base for item in outcome.inspected_targets] == [ref]
    assert w.store.ledger(w.run_id)[0][1].inspected_targets == outcome.inspected_targets


# -- 12. determinism, idempotency and crash recovery ---------------------------------------------

def deterministic_ids(monkeypatch):
    counter = itertools.count(1)
    fake = SimpleNamespace(uuid4=lambda: uuid.UUID(int=next(counter)))
    monkeypatch.setattr(jobs_module, "uuid", fake)
    monkeypatch.setattr(graph_store_module, "uuid", fake)


def test_simulated_dispatch_is_deterministic_and_idempotent(tmp_path, monkeypatch):
    results = []
    for name in ("one", "two"):
        deterministic_ids(monkeypatch)   # a fresh state gets the same ledger identities
        w = build(tmp_path / name, lambda r: [node_train(r, "train")])
        d = dispatcher(w)
        claimed, outcome = run_node(w, d, "train")
        assert d.outputs_for(claimed["id"]) == d.outputs_for(claimed["id"]) == outcome
        results.append((outcome, artifact_entries(w.store)))
    assert results[0] == results[1]   # identical artifact IDs and inventories


@pytest.mark.parametrize("crash_at", ["after_freeze", "before_release"])
def test_simulated_crash_after_the_freeze_rebuilds_identically(tmp_path, monkeypatch, crash_at):
    sd = api()
    w = build(tmp_path, lambda r: [node_train(r, "train")])
    calls = execution_spy(monkeypatch).calls
    claimed = claim(w, "train")
    before = artifact_entries(w.store)
    with monkeypatch.context() as patch:
        if crash_at == "after_freeze":
            transition = w.store.jobs.transition

            def crash(subject, status, error=None):
                if status == "COMPLETED":
                    raise Crash()
                return transition(subject, status, error)

            patch.setattr(w.store.jobs, "transition", crash)
        else:
            patch.setattr(w.store.jobs, "release_gpu", lambda *a, **kw: (_ for _ in ()).throw(Crash()))
        with pytest.raises(Crash):
            dispatcher(w).run_claimed(claimed, None, approval=w.approval)
    frozen = artifact_entries(w.store)
    assert len(frozen) == len(before) + 1 and held(w)
    d = sd.SimulatedOperationDispatcher(w.store, root=w.state, roots=None)   # a new controller process
    job_id = claimed["id"]
    if crash_at == "after_freeze":
        assert w.store.jobs.get(job_id)["status"] == "EVALUATING"
        with pytest.raises(ValueError, match="recover stale active attempts"):
            d.reconcile(job_id, None)
        assert w.store.recover_stale(w.ctl, stale_before=time.time() + 1) == (job_id,)
        assert d.failure_kind(job_id) == "uncertain"
    outcome = d.reconcile(job_id, None)
    assert outcome.status == "COMPLETED" and not held(w)
    assert d.outputs_for(job_id) == outcome
    assert artifact_entries(w.store) == frozen and calls == [launch_dir(w, claimed)]   # one execution, one artifact
    assert d.failure_kind(job_id) is None
    with pytest.raises(ValueError, match="attempt is final"):
        d.reconcile(job_id, None)
    record(w, claimed, outcome)


def test_simulated_attempt_crashed_before_running_is_released_and_runs_once(tmp_path, monkeypatch):
    """D9 with simulation: the worker runs only after transition(RUNNING), so a started PROVISIONING attempt has had
    no effect and carries no local record; release_uncontacted frees it and the fenced actor can never run."""
    w = build(tmp_path, lambda r: [node_train(r, "train")])
    calls = execution_spy(monkeypatch).calls
    claimed = claim(w, "train")
    with monkeypatch.context() as patch:
        transition = w.store.jobs.transition

        def crash(subject, status, error=None):
            if status == "RUNNING":
                raise Crash()
            return transition(subject, status, error)

        patch.setattr(w.store.jobs, "transition", crash)
        with pytest.raises(Crash):
            dispatcher(w).run_claimed(claimed, None, approval=w.approval)
    job_id, launch = claimed["id"], launch_dir(w, claimed)
    assert w.store.jobs.get(job_id)["status"] == "PROVISIONING" and attempt(w, claimed)["launch_started"] == 1
    assert not (launch / REMOTE_ATTEMPT).exists() and calls == []
    assert w.store.release_uncontacted(w.ctl, job_id)
    assert w.store.attempt_disposition(job_id)["disposition"] == "released_before_contact"
    assert dispatcher(w).failure_kind(job_id) == "failed_before_submit"   # the settled disposition answers (M-4)
    with pytest.raises(ValueError, match="stale attempt lease"):
        w.store.jobs.transition(claimed["lease"], "RUNNING")   # the old actor is fenced before any effect
    assert w.store.requeue_retry(w.ctl, job_id)
    retried = claim_job(w, job_id)
    outcome = dispatcher(w).run_claimed(retried, None, approval=w.approval)
    assert outcome.status == "COMPLETED" and calls == [launch_dir(w, retried)]
    assert retried["lease"].attempt_id != claimed["lease"].attempt_id


def test_simulated_attempt_crashed_while_running_settles_once_by_reconcile(tmp_path, monkeypatch):
    sd = api()
    w = build(tmp_path, lambda r: [node_train(r, "train")], max_attempts=2)
    spy = execution_spy(monkeypatch, fail=Crash())
    claimed = claim(w, "train")
    with pytest.raises(Crash):
        dispatcher(w).run_claimed(claimed, None, approval=w.approval)
    job_id = claimed["id"]
    assert w.store.jobs.get(job_id)["status"] == "RUNNING" and held(w)
    assert (launch_dir(w, claimed) / REMOTE_ATTEMPT).is_file()   # written after RUNNING, before the first effect
    assert w.store.release_uncontacted(w.ctl, job_id) is False   # RUNNING is never released automatically
    assert w.store.recover_stale(w.ctl, stale_before=time.time() + 1) == (job_id,)
    d = sd.SimulatedOperationDispatcher(w.store, root=w.state, roots=None)
    assert d.failure_kind(job_id) == "uncertain"
    outcome = d.reconcile(job_id, None)
    assert (outcome.status, outcome.uncertain, outcome.error) == ("FAILED", False, sd.SIMULATION_STOPPED)
    assert not held(w)
    result = accepted_result(w.state, job_id, claimed["launch_id"], claimed["lease"].attempt_id)
    assert (result.status, result.error, result.engine_version) == ("FAILED", sd.SIMULATION_STOPPED, None)
    # 8.4 review Important 1: the result reconcile published itself proves only that the actor stopped, as an
    # operator's confirmation does on the real path: counted and terminal (D4), never a retry. A suspended original
    # actor that resumes therefore cannot overlap a second attempt of the same node.
    assert d.failure_kind(job_id) == "failed_before_submit"
    assert w.store.classify_attempt(w.ctl, job_id, "failed_before_submit") == "failed_before_submit"
    assert w.store.attempt_disposition(job_id)["counted"] == 1
    w.clock.advance(600)
    assert not w.store.requeue_retry(w.ctl, job_id)   # max_attempts=2, and still no retry
    assert w.store.get_run(w.run_id)["nodes"][0]["status"] == "FAILED"
    assert spy.calls == [launch_dir(w, claimed)]   # the worker ran once, and the crashed attempt never re-ran
    # A FAILED result the simulated worker itself published stays a retryable remote failure.
    worker_failed = result.model_copy(update={"engine_version": sd.ENGINE_VERSION, "error": "simulated failure"})
    assert not sd._stopped_by_reconcile(worker_failed) and sd._stopped_by_reconcile(result)


def test_a_suspended_simulated_actor_cannot_publish_after_reconcile(tmp_path, monkeypatch):
    sd = api()
    w = build(tmp_path, lambda r: [node_train(r, "train")])
    entered, resume = threading.Event(), threading.Event()
    real = sd.SimulatedWorker.execute

    def suspended(self, *args, **kwargs):
        entered.set()
        assert resume.wait(30)
        return real(self, *args, **kwargs)

    monkeypatch.setattr(sd.SimulatedWorker, "execute", suspended)
    claimed = claim(w, "train")
    before = artifact_entries(w.store)
    late = []
    actor = threading.Thread(target=lambda: late.append(
        dispatcher(w).run_claimed(claimed, None, approval=w.approval)))
    actor.start()
    assert entered.wait(30)
    job_id = claimed["id"]
    assert w.store.recover_stale(w.ctl, stale_before=time.time() + 1) == (job_id,)
    settled = sd.SimulatedOperationDispatcher(w.store, root=w.state, roots=None).reconcile(job_id, None)
    assert settled.status == "FAILED" and not held(w)
    resume.set()
    actor.join(30)
    assert not actor.is_alive() and late[0].status == "FAILED" and sd.RESULT_PUBLISHED in late[0].error
    assert w.store.jobs.get(job_id)["status"] == "FAILED"
    result = accepted_result(w.state, job_id, claimed["launch_id"], claimed["lease"].attempt_id)
    assert result.error == sd.SIMULATION_STOPPED   # the first published result stays the only one
    assert artifact_entries(w.store) == before   # the late actor froze nothing


def test_simulated_outcome_matches_the_runner_outcome():
    from tuning_factory.attempt_runner import OperationAttemptRunner, OperationOutcome
    sd = api()
    assert [(item.name, item.default) for item in dataclasses.fields(sd.OperationOutcome)] == [
        (item.name, item.default) for item in dataclasses.fields(OperationOutcome)]
    for name in ("run_claimed", "reconcile", "outputs_for", "failure_kind"):
        assert callable(getattr(sd.SimulatedOperationDispatcher, name))
        assert callable(getattr(OperationAttemptRunner, name))


# -- 13. simulated evaluations never select ------------------------------------------------------

def test_simulated_evaluation_is_never_selection_eligible(tmp_path):
    w = build(tmp_path, lambda r: [node_evaluate(r, "eval", r.first)])
    claimed, outcome = run_node(w, dispatcher(w), "eval")
    record_ = outcome.outputs.evaluation.evaluation
    assert record_.simulation is True and record_.producer_attempt_id == claimed["lease"].attempt_id
    predictions = json.loads((launch_dir(w, claimed) / "output" / "predictions.json").read_bytes())
    assert predictions["ordered_sample_ids"] == ["row:1", "row:0"] and predictions["predictions"] == ["", ""]
    receipt = select_best((record_,), SelectionSpec(metric="accuracy", direction="max", allow_partial=True),
                          requested_models=(record_.model,))
    assert receipt.winner is None and "simulation" in receipt.excluded[0].reasons


# -- 14. nothing network-capable or heavy --------------------------------------------------------

CHAIN_PROBE = """
import json, socket, sys
from pathlib import Path
def refuse(*args, **kwargs):
    raise AssertionError("network")
socket.socket.connect = refuse
import tuning_factory.manifest
tuning_factory.manifest.code_revision = lambda: "0" * 40
import tuning_factory.graph_store
tuning_factory.graph_store.GraphStore.test_acknowledgement = True   # as tests/conftest.py does
import test_simulated_dispatch
w, outcomes, selection = test_simulated_dispatch.full_chain(Path(sys.argv[1]))
assert selection.selection.status == "no_winner" and len(outcomes) == 5
print(json.dumps(sorted(name for name in sys.argv[2:] if name in sys.modules)))
"""
IMPORT_PROBE = """
import json, sys
import tuning_factory.simulated_dispatch
print(json.dumps(sorted(name for name in sys.argv[1:] if name in sys.modules)))
"""


def test_simulated_dispatch_imports_no_network_or_heavy_modules(tmp_path):
    assert subprocess_modules(CHAIN_PROBE, tmp_path / "chain", *FORBIDDEN, cwd=tmp_path) == []
    # SimulatedWorker needs no ledger: importing the module loads neither graph_store nor jobs.
    assert subprocess_modules(IMPORT_PROBE, *FORBIDDEN, "tuning_factory.graph_store", "tuning_factory.jobs",
                              cwd=tmp_path) == []


# -- 8.4 review fixes (folded into 8.5) -------------------------------------------------------------

def big_root_evidence():
    """Inspected targets of a 7B-class HF root: 28 layers of seven 3584-wide linear modules (about 10 GB of F32)."""
    from tuning_factory.graph_planner import ModelTargetEvidence, TargetModuleEvidence
    modules = tuple(TargetModuleEvidence(name=f"model.layers.{layer}.{name}", input_dimension=3584,
                                         output_dimension=3584)
                    for layer in range(28) for name in ("q_proj", "k_proj", "v_proj", "o_proj", "gate_proj",
                                                        "up_proj", "down_proj"))
    return ModelTargetEvidence(base=inputs.ROOT, modules=modules)


def test_simulated_outputs_above_the_size_cap_are_refused_before_launch(tmp_path):
    """8.4 review Important 2: zero-valued tensors are real bytes (written, then copied by the freeze). Above the cap
    (1 GiB by default, configurable on the dispatcher) a dry run refuses before begin_launch and writes nothing."""
    sd = api()
    assert sd.MAX_SIMULATED_OUTPUT_BYTES == 1024 ** 3
    w = build(tmp_path, lambda r: [node_train(r, "full", base=r.root, recipe=r.full_recipe)])
    roots = sd.DirectoryRootSnapshots(write_root_snapshot(tmp_path).parents[2])
    job_id = prepare(w, "full")
    operation = w.store.jobs.get(job_id)["manifest"]["operation"]
    worker = sd.SimulatedWorker()
    worker.check(operation, artifacts=w.store.artifacts, roots=roots, target_evidence=(inputs.evidence(inputs.ROOT),))
    with pytest.raises(ValueError, match=sd.SIMULATED_OUTPUT_TOO_LARGE):   # the default cap, a 7B-class base
        worker.check(operation, artifacts=w.store.artifacts, roots=roots, target_evidence=(big_root_evidence(),))
    with pytest.raises(ValueError, match="positive number of bytes"):
        sd.SimulatedOperationDispatcher(w.store, root=w.state, roots=roots, max_output_bytes=0)
    small = sd.SimulatedOperationDispatcher(w.store, root=w.state, roots=roots, max_output_bytes=16)
    claimed = claim_job(w, job_id)
    outcome = small.run_claimed(claimed, None, approval=w.approval)
    assert outcome.status == "FAILED" and sd.SIMULATED_OUTPUT_TOO_LARGE in outcome.error
    assert attempt(w, claimed)["launch_started"] == 0 and not held(w)   # abandon_unlaunched: nothing launched
    assert not launch_dir(w, claimed).exists()
    assert small.failure_kind(job_id) == "refused_before_launch"


def test_a_filesystem_without_hard_links_is_refused_before_launch(tmp_path, monkeypatch):
    """8.4 review Minor 2: the exclusive result publish needs hard links; they are probed before begin_launch."""
    sd = api()
    w = build(tmp_path, lambda r: [node_train(r, "train")])
    assert sd.hard_links_supported(tmp_path / "probe")
    claimed = claim(w, "train")

    def no_links(source, target):
        raise OSError("hard links are not supported here")

    monkeypatch.setattr(sd.os, "link", no_links)
    outcome = dispatcher(w).run_claimed(claimed, None, approval=w.approval)
    assert outcome.status == "FAILED" and sd.NO_HARD_LINKS in outcome.error
    assert attempt(w, claimed)["launch_started"] == 0 and not held(w)
    assert not launch_dir(w, claimed).exists()


@pytest.mark.parametrize("module", ["simulated", "runner"])
def test_attempt_heartbeats_survive_a_transient_database_error(tmp_path, module):
    """8.4 review Minor 3: a locked database once must not silently end the heartbeat (the attempt would go stale)."""
    import sqlite3
    w = build(tmp_path, lambda r: [node_train(r, "train")])
    if module == "simulated":
        owner = dispatcher(w)
    else:
        from tuning_factory.attempt_runner import OperationAttemptRunner
        owner = OperationAttemptRunner(w.store, root=w.state)
    owner.heartbeat_seconds = 0.01
    beats, errors = threading.Event(), []

    def heartbeat(lease):
        if len(errors) < 2:
            errors.append(lease)
            raise sqlite3.OperationalError("database is locked")
        beats.set()

    owner.jobs = SimpleNamespace(heartbeat=heartbeat)
    stop = owner._heartbeat(SimpleNamespace(job_id="j"))
    try:
        assert beats.wait(30)   # it kept beating after two failures
    finally:
        stop.set()
    assert len(errors) == 2
