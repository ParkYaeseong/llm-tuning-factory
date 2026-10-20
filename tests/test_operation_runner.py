"""Task 7.5: the control-plane OperationAttemptRunner (fenced claim -> launch -> collect -> freeze).

Main env only. The GPU host is ``test_remote_worker.GPUHost``: a local tree on which the real SSHExecutor
publishes the model cache and launches the real worker v2. The engine and the model operations are fixtures
that write small, well-formed safetensors. No SSH network, GPU, download or institution data.
"""
import importlib
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
import test_model_inputs as inputs
import yaml
from test_model_ops_contract import PEFT_0181_EXPORT, pinned_runtime
from test_remote_worker import RUNPOD_ROOT, GPUHost

from tuning_factory import operation_dispatch, worker
from tuning_factory.benchmarks import BenchmarkSpec, EvaluationRecord
from tuning_factory.evaluation import CommonEvaluator
from tuning_factory.execution import LocalExecutor, SSHExecutor
from tuning_factory.executor_profiles import ExecutorProfile, ResourceFacts
from tuning_factory.graph_planner import (
    ApprovalGrant,
    BenchmarkRevision,
    ExecutionChoices,
    ModelContext,
    NodeEvaluationBinding,
    NodeExecutionEvidence,
    NodeResources,
    ProducerEvidence,
    TrackingDestination,
    VerifiedBindings,
    compile_node,
    plan_graph,
    snapshot_artifact,
    snapshot_dataset,
)
from tuning_factory.graph_schema import GraphSpec
from tuning_factory.graph_store import GraphStore
from tuning_factory.model_artifacts import ModelInput, ModelLoadRef
from tuning_factory.projects import DatasetBinding, DatasetRef, ProjectSpec, Revision
from tuning_factory.recipes import RecipeSpec
from tuning_factory.registry import canonical, digest
from tuning_factory.training import LlamaFactoryBackend


def api():
    # A missing implementation is a test failure, rather than a collection error.
    try:
        return importlib.import_module("tuning_factory.attempt_runner")
    except ModuleNotFoundError:
        pytest.fail("the control-plane operation attempt runner is missing")


def outputs_api():
    try:
        return importlib.import_module("tuning_factory.operation_outputs")
    except ModuleNotFoundError:
        pytest.fail("control-plane output freezing is missing")


class Crash(BaseException):
    """A process crash at a chosen step: it escapes every ``except Exception``, so nothing after it runs."""


LOCK = Path(__file__).resolve().parents[1] / "containers" / "gpu-requirements.lock"
EXECUTOR = ExecutorProfile(profile_id="p", revision="1", name="Pod", pod_id="pod1", credential_scope="local",
                           transport_settings_sha256="1" * 64)
FACTS = ResourceFacts(pod_id="pod1", gpu_type="A100", gpu_count=1, total_hourly_cost_usd=2,
                      observed_at="2026-10-20T00:00:00Z")
TRACKING = TrackingDestination(profile_id="mlflow", revision="1", public_settings_sha256="2" * 64)
POD = {"id": "pod1", "gpu": {"id": "A100", "displayName": "A100", "count": 1}, "costPerHr": 2}
TRAIN_ROWS = [{"instruction": "train question", "output": "train answer"}]
HELD_ROWS = [{"instruction": "held-out first", "output": "positive"},
             {"instruction": "held-out second", "output": "negative"}]
PREDICTIONS = ("negative", "negative")   # the engine's generations for row:1 and row:0, in benchmark order
# LLaMA-Factory re-saves the tokenizer: other bytes, the same vocabulary.
ENGINE_TOKENIZER = {"tokenizer.json": b'{"version": "1.0"}', "tokenizer_config.json": b'{"model_max_length": 64}'}
ARTIFACT_FILES = ["adapter_config.json", "adapter_model.safetensors", "config.json", "tokenizer.json",
                  "tokenizer_config.json"]


# -- engine and model-operation fixtures (run inside the fake GPU host's worker) -----------------

def write_adapter(output, rank, alpha, *, base_model, targets=("v_proj", "q_proj")):
    """A PEFT 0.18.1 LoRA export (set-ordered targets, a provenance base path) with well-formed tensors."""
    exported = {**PEFT_0181_EXPORT, "r": rank, "lora_alpha": alpha, "target_modules": list(targets),
                "base_model_name_or_path": base_model}
    (output / "adapter_config.json").write_text(json.dumps(exported, indent=2), encoding="utf-8")
    shapes = {}
    for target in sorted(targets):
        shapes[f"base_model.model.model.{target}.lora_A.weight"] = (rank, 2)
        shapes[f"base_model.model.model.{target}.lora_B.weight"] = (2, rank)
    inputs.write_safetensors(output / "adapter_model.safetensors", shapes)


def write_engine_output(output, tokenizer, method="lora"):
    """What LLaMA-Factory leaves in ``output/`` after training: weights plus files that are not the model."""
    output.mkdir()
    if method == "full":
        (output / "config.json").write_bytes(canonical({**json.loads(inputs.CONFIG), "torch_dtype": "bfloat16"}))
        (output / "generation_config.json").write_text('{"do_sample": false}', encoding="utf-8")
        inputs.write_safetensors(output / "model.safetensors", {f"{name}.weight": (2, 2) for name in inputs.MODULES})
    else:
        write_adapter(output, 2, 4, base_model="/workspace/factory-desktop/model-cache/base/files")
    (output / "README.md").write_text("engine model card naming /workspace host paths", encoding="utf-8")
    (output / "training_args.bin").write_bytes(b"pickled trainer arguments")
    (output / "trainer_state.json").write_text('{"global_step": 1, "log_history": [{"loss": 0.5}]}',
                                               encoding="utf-8")
    (output / "all_results.json").write_text('{"train_loss": 0.5}', encoding="utf-8")
    checkpoint = output / "checkpoint-1"
    checkpoint.mkdir()
    weights = "model.safetensors" if method == "full" else "adapter_model.safetensors"
    shutil.copyfile(output / weights, checkpoint / weights)
    (checkpoint / "optimizer.pt").write_bytes(b"optimizer state")
    for name, data in tokenizer.items():
        (output / name).write_bytes(data)


def engine_fixture(calls, *, tokenizer=None, fail=None, method="lora"):
    class Engine(LlamaFactoryBackend):
        def run_config(self, directory, timeout, canceled, filename, log_name):
            calls.append(filename)
            directory = Path(directory)
            if fail:
                raise RuntimeError(fail)
            if filename == "predict.yaml":
                plan = yaml.safe_load((directory / filename).read_text(encoding="utf-8"))
                (directory / "prediction").mkdir()
                rows = [{"predict": f" {value} ", "label": "engine labels are never used"}
                        for value in PREDICTIONS[:plan["max_samples"]]]
                (directory / "prediction" / "generated_predictions.jsonl").write_text(
                    "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
                return
            write_engine_output(directory / "output", ENGINE_TOKENIZER if tokenizer is None else tokenizer, method)

    return Engine


class ModelOpsFixture:
    """Stands in for model_ops inside the worker: small outputs laid out exactly like the real operations."""

    def __init__(self, calls):
        self.calls = calls

    @staticmethod
    def _snapshot(base, output, *, config):
        for name in (*inputs.TOKENIZER, *(["config.json"] if config else [])):
            shutil.copyfile(Path(base.path) / name, output / name)

    @staticmethod
    def _files(output):
        return tuple(sorted(path.name for path in output.iterdir()))

    def merge_adapters(self, models, weights, output, *, base, policy, device, require_real, probe_ids=None):
        from tuning_factory.model_ops import MergeReceipt
        self.calls.append("merge")
        rank = sum(model.descriptor.adapter.rank for model in models)
        output.mkdir()
        write_adapter(output, rank, rank, base_model=inputs.ROOT.repo_id)   # PEFT cat: r = lora_alpha = sum(r)
        self._snapshot(base, output, config=True)
        return MergeReceipt(policy_id=policy, inputs=tuple(model.identity for model in models),
                            normalized_weights=tuple(weights), output_rank=rank, max_abs_delta_error=1e-9,
                            max_abs_logits_error=1e-8, finite_tensors_checked=8, output_files=self._files(output))

    def materialize_model(self, load, output, *, policy, device, probe_ids=None):
        from tuning_factory.model_ops import MaterializeReceipt
        self.calls.append("materialize")
        output.mkdir()
        (output / "config.json").write_bytes(inputs.CONFIG)
        (output / "generation_config.json").write_text('{"do_sample": false}\n', encoding="utf-8")
        inputs.write_safetensors(output / "model.safetensors", {f"{name}.weight": (2, 2) for name in inputs.MODULES})
        self._snapshot(load.base, output, config=False)
        return MaterializeReceipt(policy_id=policy, base=load.base.identity, adapter=load.adapter.identity,
                                  max_abs_delta_error=1e-9, max_abs_logits_error=1e-8,
                                  non_target_tensors_identical=True,
                                  tokenizer_fingerprint=load.base.tokenizer_fingerprint, finite_tensors_checked=8,
                                  output_files=self._files(output))

    def inspect_linear_modules(self, model_dir):
        from tuning_factory.graph_planner import TargetModuleEvidence
        return tuple(TargetModuleEvidence(name=name, input_dimension=2, output_dimension=2)
                     for name in inputs.MODULES)

    def inspect_target_modules(self, model_dir, base):
        return inputs.evidence(base)


# -- a submitted real plan, its claimed attempt, a fake GPU host and the runner ------------------

def revision(spec):
    return Revision(spec=spec, revision_sha256=digest(canonical(spec.model_dump(mode="json"))))


def producer(ref, marker):
    return ProducerEvidence(model=ref, job_id=marker, attempt_id=marker, manifest_sha256="b" * 64,
                            runtime_profile="primary", training_stage="sft", status="completed",
                            tracking_acknowledged=True)


def register(registry, tmp_path, name, rows, export_allowed):
    path = tmp_path / f"{name}.json"
    path.write_bytes(canonical(rows))
    record = registry.register_dataset(name, "1", path, export_allowed=export_allowed)
    return DatasetRef(dataset_id=name, version="1", sha256=record["sha256"])


def train_node(node_id, recipe, model_input, profile):
    return {"kind": "train", "id": node_id, "label": "Train", "recipe_revision": recipe.revision_sha256,
            "model_input": model_input.model_dump(mode="json"), "dataset_binding": "data",
            "executor_profile_ids": [profile]}


NODES = {
    "train": lambda r: train_node("train", r.recipe, ModelInput(policy=ModelLoadRef(base=r.base),
                                                                initialization="new_adapter"), r.profile),
    "continue": lambda r: train_node("continue", r.recipe, ModelInput(
        policy=ModelLoadRef(base=r.base, adapter=r.first), initialization="continue_adapter"), r.profile),
    "full_train": lambda r: train_node("full_train", r.full_recipe, ModelInput(
        policy=ModelLoadRef(base=r.base), initialization="full_weights"), r.profile),
    "eval": lambda r: {"kind": "evaluate", "id": "eval", "label": "Evaluate", "model": r.first.model_dump(mode="json"),
                       "benchmark_revision": r.benchmark.fingerprint(), "executor_profile_ids": [r.profile]},
    "select": lambda r: {"kind": "select", "id": "select", "label": "Select",
                         "evaluations": [{"node_id": "eval", "output_name": "evaluation"}],
                         "selection": {"metric": "accuracy", "direction": "max"}},
    "merge": lambda r: {"kind": "merge", "id": "merge", "label": "Merge",
                        "models": [r.first.model_dump(mode="json"), r.second.model_dump(mode="json")],
                        "weights": [0.25, 0.75], "executor_profile_ids": [r.profile]},
    "full": lambda r: {"kind": "materialize", "id": "full", "label": "Materialize",
                       "model": r.first.model_dump(mode="json"), "executor_profile_ids": [r.profile]},
}


def build_graph(tmp_path, kinds, *, simulation=False, export_allowed=True, executor=EXECUTOR, facts=FACTS):
    store = GraphStore(tmp_path / "state" / "jobs.db")
    registry, models = store.registry, store.artifacts
    base = models.freeze(*inputs.full_model(tmp_path, "base"))
    first = models.freeze(*inputs.peft_adapter(tmp_path, "first", base=base, parents=(base,)))
    second = models.freeze(*inputs.peft_adapter(tmp_path, "second", rank=3, alpha=9, base=base, parents=(base,)))
    data = register(registry, tmp_path, "data", TRAIN_ROWS, export_allowed)
    held = register(registry, tmp_path, "held", HELD_ROWS, export_allowed)
    recipe = revision(RecipeSpec(recipe_id="r", name="recipe",
                                 hyperparameters={"lora_rank": 2, "lora_target": "q_proj,v_proj"}))
    full_recipe = revision(RecipeSpec(recipe_id="full", name="full", training_method="full"))
    benchmark = BenchmarkSpec(dataset=held, evaluator_kind="classification", evaluator_version="1",
                              ordered_sample_ids=("row:1", "row:0"), max_new_tokens=8, cutoff_len=64, seed=1,
                              precision="bf16", quantization_bit=None,
                              tokenizer_fingerprint=inputs.ROOT.tokenizer_fingerprint, template="qwen",
                              semantic_model_kind="causal_lm")
    refs = SimpleNamespace(base=base, first=first, second=second, recipe=recipe, full_recipe=full_recipe,
                           benchmark=benchmark, profile=executor.profile_id)
    graph = GraphSpec.model_validate({"nodes": [NODES[kind](refs) for kind in kinds]})
    project = revision(ProjectSpec(project_id="project.1", name="Project", datasets=(
        DatasetBinding(binding_id="data", role="train", dataset=data),
        DatasetBinding(binding_id="held", role="validation", dataset=held)), graph=graph.model_dump(mode="json")))
    producers = tuple(producer(ref, marker) for marker, ref in (("base", base), ("first", first), ("second", second)))
    choices = ExecutionChoices(
        recipes=(recipe, full_recipe),
        benchmarks=(BenchmarkRevision(spec=benchmark, revision_sha256=benchmark.fingerprint()),),
        datasets=(snapshot_dataset(registry, data), snapshot_dataset(registry, held)),
        models=(ModelContext(root=inputs.ROOT, profile=inputs.PROFILE),),
        artifacts=tuple(snapshot_artifact(models, item.model, item) for item in producers), executors=(executor,),
        node_resources=tuple(NodeResources(node_id=node.id, timeout_seconds=900) for node in graph.nodes
                             if node.kind != "select"),
        base_targets=(inputs.evidence(base),), tracking_destination=TRACKING)
    plan = plan_graph(project, choices, (facts,), simulation=simulation)
    run = store.submit(plan, "run", ApprovalGrant.for_plan(plan))
    ctl = store.acquire_controller(run["id"], ttl_seconds=3600)
    store.prepare_ready(ctl)
    # A selection waits for its evaluations; every operation node must have compiled.
    errors = [node["error"] for node in store.get_run(run["id"])["nodes"]
              if node["error"] and node["node_id"] != "select"]
    assert not errors, errors
    return SimpleNamespace(store=store, plan=plan, run=run, ctl=ctl, refs=refs, producers=producers, facts=facts,
                           approval=ApprovalGrant.for_plan(plan))


def gpu_host(tmp_path, monkeypatch, calls, *, server=None, **engine):
    """A fake GPU host and a launch executor for it: an existing Pod, or ``server=(profile, facts)``."""
    host = GPUHost(tmp_path / "remote", monkeypatch)
    base = server[0].transport.remote_root if server else RUNPOD_ROOT
    lock = base + "/runtime/gpu-requirements.lock"
    host.sftp.path(lock).parent.mkdir(parents=True)
    host.sftp.path(lock).write_bytes(LOCK.read_bytes())
    destination = dict(server_profile=server[0], server_facts=server[1]) if server else dict(pod=dict(POD))
    executor = SSHExecutor("test-host", 22, "root", "unused", client=host, remote_root=base + "/jobs",
                           remote_python=base + "/runtime/venv/bin/python", worker_lock_file=lock,
                           hf_home=base + "/hf-cache", model_cache_root=base + "/model-cache", **destination)
    pinned_runtime(monkeypatch)
    monkeypatch.setattr(worker, "LlamaFactoryBackend", engine_fixture(calls, **engine))
    monkeypatch.setattr(worker, "runtime_preflight_bound",
                        lambda config, paths: {"bound_model": paths.path("policy_base").as_posix()})
    monkeypatch.setattr(worker, "evaluation_preflight_bound", lambda benchmark, paths, gpu_count: {"gpus": gpu_count})
    monkeypatch.setattr(operation_dispatch, "default_model_ops", ModelOpsFixture(calls))
    return host, executor


def world(tmp_path, monkeypatch, kinds=("train",), *, graph_options=None, server=None, **engine):
    options = dict(graph_options or {})
    if server:
        options.update(executor=server[0], facts=server[1])
    graph = build_graph(tmp_path, kinds, **options)
    calls = []
    host, executor = gpu_host(tmp_path, monkeypatch, calls, server=server, **engine)
    runner = api().OperationAttemptRunner(graph.store, root=tmp_path / "state")
    claimed = graph.store.claim_ready(graph.facts, graph.ctl)
    return SimpleNamespace(**vars(graph), calls=calls, host=host, executor=executor, runner=runner, claimed=claimed,
                           job_id=claimed["id"], lease=claimed["lease"], state=tmp_path / "state")


def run(w, claimed=None, executor=None):
    return w.runner.run_claimed(claimed or w.claimed, executor or w.executor, approval=w.approval)


def launch_dir(w, job_id=None):
    job_id = job_id or w.job_id
    return w.state / "jobs" / job_id / "launches" / w.claimed["launch_id"]


def artifact_entries(store):
    return sorted(path.parent.name + "/" + path.name for path in store.artifacts.root.glob("*/*")
                  if not path.parent.name.startswith("."))


def held_locks(w):
    return w.store.jobs.gpu_locked(w.lease.resource_id)


def recording_executor(calls, **changes):
    class Recording(SSHExecutor):
        def run_operation(self, *args, **kwargs):
            calls.append(args)
            raise AssertionError("a refused attempt must never reach the executor")

    options = dict(remote_root=RUNPOD_ROOT + "/jobs", model_cache_root=RUNPOD_ROOT + "/model-cache", pod=dict(POD))
    options.update(changes)
    return Recording("test-host", 22, "root", "unused", client=SimpleNamespace(), **options)


# -- 1. refusals before launch -------------------------------------------------------------------

@pytest.mark.parametrize("case,message", [
    ("simulation", "simulation manifest cannot authorize GPU execution"),
    ("unapproved", "approval does not match"),
    ("untyped_approval", "approval"),
    ("other_pod", "destination"),
    ("other_price", "price"),
    ("recovery_only", "recovery-only executor cannot launch"),
    ("local_executor", "remote executor"),
    ("export_refused", "dataset export has not been authorized"),
    ("forged_launch", "launch identity"),
])
def test_runner_refuses_select_simulation_unapproved_or_other_destination(tmp_path, monkeypatch, case, message):
    options = {"simulation": case == "simulation", "export_allowed": case != "export_refused"}
    w = world(tmp_path, monkeypatch, graph_options=options)
    executor_calls = []
    executor = recording_executor(executor_calls)
    approval, claimed = w.approval, w.claimed
    if case == "unapproved":
        approval = approval.model_copy(update={"plan_sha256": "0" * 64})
    elif case == "untyped_approval":
        approval = approval.model_dump(mode="json")
    elif case == "other_pod":
        executor = recording_executor(executor_calls, pod={**POD, "id": "pod2"})
    elif case == "other_price":
        executor = recording_executor(executor_calls, pod={**POD, "costPerHr": 3})
    elif case == "recovery_only":
        executor.recovery_only = True
    elif case == "local_executor":
        executor = LocalExecutor()
    elif case == "forged_launch":
        claimed = {**claimed, "launch_id": "f" * 32}
    outcome = w.runner.run_claimed(claimed, executor, approval=approval)
    assert (outcome.status, outcome.uncertain, outcome.outputs) == ("FAILED", False, None)
    assert message in outcome.error
    assert executor_calls == [] and w.host.commands == [] and w.calls == []
    job = w.store.jobs.get(w.job_id)
    assert job["status"] == "FAILED" and message in job["error"]
    assert message in (w.state / "jobs" / w.job_id / "error.txt").read_text(encoding="utf-8")
    assert not held_locks(w)   # abandon_unlaunched: nothing launched, so the lock is free
    assert not (w.state / "jobs" / w.job_id / "launches").exists()


def test_runner_refuses_a_local_selection_before_any_executor_call(tmp_path, monkeypatch):
    graph = build_graph(tmp_path, ("eval", "select"))
    store, refs = graph.store, graph.refs

    def resolver(plan, node_id, ledger):
        """A trusted Task 8-style resolver that supplies a completed evaluation, so the selection compiles."""
        record = EvaluationRecord(model=refs.first, benchmark_sha256=refs.benchmark.fingerprint(),
                                  metrics={"accuracy": 1.0}, status="completed", synthetic=False, simulation=False,
                                  producer_attempt_id="a" * 32)
        evidence = NodeExecutionEvidence(node_id="eval", plan_sha256=plan.sha256, job_id="j" * 32,
                                         attempt_id="a" * 32, manifest_sha256="d" * 64, status="completed",
                                         tracking_acknowledged=True)
        return VerifiedBindings(registry=store.registry, artifact_store=store.artifacts, destination_profile=EXECUTOR,
                                producers=tuple(item.producer for item in plan.execution_choices.artifacts),
                                base_targets=plan.execution_choices.base_targets, tracking_destination=TRACKING,
                                evaluations=(NodeEvaluationBinding(node_id="eval", evaluation=record,
                                                                   tracking_acknowledged=True, producer=evidence),))

    store.binding_resolver = resolver
    store.prepare_ready(graph.ctl)
    claimed = store.claim_ready(None, graph.ctl)   # only the local selection can be claimed without a GPU
    assert claimed["manifest"]["operation"]["kind"] == "select"
    executor_calls = []
    runner = api().OperationAttemptRunner(store, root=tmp_path / "state")
    outcome = runner.run_claimed(claimed, recording_executor(executor_calls), approval=graph.approval)
    assert (outcome.status, outcome.uncertain) == ("FAILED", False)
    assert "local selection never dispatches to a GPU worker" in outcome.error and executor_calls == []
    assert store.jobs.get(claimed["id"])["status"] == "FAILED"
    assert not store.jobs.gpu_locked(claimed["lease"].resource_id)


# -- 2. persisted launch identity ----------------------------------------------------------------

def test_runner_uses_persisted_launch_identity(tmp_path, monkeypatch):
    from tuning_factory.worker_protocol import WorkerRequest
    w = world(tmp_path, monkeypatch)
    outcome = run(w)
    assert outcome.status == "COMPLETED", outcome.error
    launch = launch_dir(w)
    request = WorkerRequest.model_validate_json((launch / "command.json").read_bytes())
    assert (launch / "command.json").read_bytes() == canonical(request.model_dump(mode="json"))
    job = w.store.jobs.get(w.job_id)
    assert (request.attempt_id, request.launch_id) == (w.lease.attempt_id, w.claimed["launch_id"])
    assert request.manifest_sha256 == job["manifest"]["sha256"] and request.timeout_seconds == 900
    assert request.operation_sha256 == digest(canonical(job["manifest"]["operation"]))
    assert (launch / "manifest.json").read_bytes() == canonical(job["manifest"])
    # One remote launch directory, named by the persisted launch ID (never a new uuid).
    remote = w.host.sftp.path(f"{RUNPOD_ROOT}/jobs/{w.job_id}")
    assert sorted(path.name for path in remote.iterdir()) == [w.claimed["launch_id"]]
    assert len(w.host.launches) == 1 and w.claimed["launch_id"] in w.host.launches[0]
    with pytest.raises(ValueError, match="launch already started or uncertain"):
        run(w)   # begin_launch is once per attempt
    assert len(w.host.launches) == 1 and w.calls == ["train.bound.yaml"]


# -- 3. lost start response: one training call, reconciled ---------------------------------------

def test_lost_start_response_never_trains_twice(tmp_path, monkeypatch):
    w = world(tmp_path, monkeypatch)
    w.host.mode = "lose_start_response"
    outcome = run(w)
    assert (outcome.status, outcome.uncertain, outcome.outputs) == ("FAILED", True, None)
    assert w.store.jobs.get(w.job_id)["status"] == "FAILED" and held_locks(w)   # the lock is retained
    taken = w.store.takeover_attempt(w.ctl, w.job_id)
    with pytest.raises(ValueError, match="launch already started or uncertain"):
        run(w, claimed=taken)
    assert w.runner.reconcile(w.job_id, w.executor) is None   # started, no result: still uncertain
    assert held_locks(w) and w.calls == []
    w.host.finish()
    outcome = w.runner.reconcile(w.job_id, w.executor)
    assert (outcome.status, outcome.uncertain) == ("COMPLETED", False)
    assert w.calls == ["train.bound.yaml"] and len(w.host.launches) == 1
    assert w.store.jobs.get(w.job_id)["status"] == "COMPLETED" and not held_locks(w)
    descriptor = w.store.artifacts.verify(outcome.outputs.model.model)
    assert descriptor.producer_attempt_id == w.lease.attempt_id
    w.store.record_outputs(taken["lease"], outcome.outputs)


# -- 4. foreign results are never frozen ---------------------------------------------------------

def test_result_from_other_attempt_is_rejected(tmp_path, monkeypatch):
    w = world(tmp_path, monkeypatch)
    before = artifact_entries(w.store)

    def foreign(directory):
        result = json.loads((directory / "result.json").read_bytes())
        (directory / "result.json").write_bytes(canonical({**result, "attempt_id": "e" * 32}))

    w.host.after_worker = foreign
    outcome = run(w)
    assert (outcome.status, outcome.uncertain, outcome.outputs) == ("FAILED", True, None)
    with pytest.raises(ValueError, match="remote result belongs to a different attempt"):
        w.runner.reconcile(w.job_id, w.executor)
    assert artifact_entries(w.store) == before and held_locks(w)


def test_output_builder_checks_the_result_identities(tmp_path, monkeypatch):
    from tuning_factory.operation_runner import ResolvedModelInputs
    from tuning_factory.operations import TrainOperation
    from tuning_factory.worker_protocol import OperationResult
    w = world(tmp_path, monkeypatch)
    outcome = run(w)
    assert outcome.status == "COMPLETED", outcome.error
    launch, job = launch_dir(w), w.store.jobs.get(w.job_id)
    operation = TrainOperation.model_validate(job["manifest"]["operation"])
    result = OperationResult.model_validate_json((launch.parent / (launch.name + ".result.json")).read_bytes())
    resolved = ResolvedModelInputs.model_validate_json((launch / "model-inputs.json").read_bytes())
    options = dict(job_id=w.job_id, attempt_id=w.lease.attempt_id, manifest_sha256=job["manifest"]["sha256"],
                   launch_dir=launch, resolved=resolved, approved_cache=w.store.artifacts)
    for changes in ({"attempt_id": "e" * 32}, {"manifest_sha256": "e" * 64}, {"launch_id": "e" * 32},
                    {"status": "FAILED", "error": "engine failed"}):
        forged = result.model_copy(update=changes)
        workspace = tmp_path / ("workspace-" + next(iter(changes)))
        workspace.mkdir()
        with pytest.raises(ValueError, match="different attempt|not completed"):
            outputs_api().build_output_descriptor(operation, forged, workspace=workspace, **options)


# -- 5./6. train outputs -------------------------------------------------------------------------

def test_train_output_frozen_with_effective_base_lineage_and_base_tokenizer(tmp_path, monkeypatch):
    w = world(tmp_path, monkeypatch)
    outcome = run(w)
    assert (outcome.status, outcome.uncertain, outcome.inspected_targets) == ("COMPLETED", False, ()), outcome.error
    binding = outcome.outputs.model
    assert binding.node_id == "train" and outcome.outputs.evaluation is None
    assert outcome.outputs.inspected_targets == ()
    descriptor = w.store.artifacts.verify(binding.model)
    job = w.store.jobs.get(w.job_id)
    assert (descriptor.producer_job_id, descriptor.producer_attempt_id, descriptor.producer_manifest_sha256) == (
        w.job_id, w.lease.attempt_id, job["manifest"]["sha256"])
    assert (descriptor.storage_kind, descriptor.semantic_kind, descriptor.has_value_head) == ("adapter", "causal_lm",
                                                                                              False)
    assert descriptor.root == inputs.ROOT and descriptor.effective_base == w.refs.base
    assert descriptor.parents == (w.refs.base,) and descriptor.quantization_bit is None
    assert (descriptor.synthetic, descriptor.simulation) == (False, False)
    adapter = descriptor.adapter
    assert (adapter.method, adapter.rank, adapter.alpha, adapter.target_modules) == ("lora", 2, 4, ("q_proj", "v_proj"))
    assert (adapter.task_type, adapter.inference_mode) == ("CAUSAL_LM", True)
    assert descriptor.config_sha256 == inputs.ROOT.config_sha256
    assert descriptor.tokenizer_fingerprint == inputs.ROOT.tokenizer_fingerprint
    files = w.store.artifacts.path(binding.model)
    assert sorted(path.name for path in files.rglob("*")) == ARTIFACT_FILES   # no README, checkpoint or optimizer
    for name, data in inputs.TOKENIZER.items():
        assert (files / name).read_bytes() == data   # the base's bytes, never the engine's re-saved tokenizer
    assert (files / "config.json").read_bytes() == inputs.CONFIG
    output = launch_dir(w) / "output"
    for name in ("adapter_config.json", "adapter_model.safetensors"):
        assert (files / name).read_bytes() == (output / name).read_bytes()   # engine weights and loader config
    result = json.loads((launch_dir(w).parent / (w.claimed["launch_id"] + ".result.json")).read_bytes())
    assert {"base-snapshot/config.json", "base-snapshot/tokenizer.json"} <= set(result["evidence"])
    assert job["status"] == "COMPLETED" and not held_locks(w)
    assert w.calls == ["train.bound.yaml"]


def test_continuation_stages_inspected_targets_and_records_both_parents(tmp_path, monkeypatch):
    w = world(tmp_path, monkeypatch, ("continue",))
    outcome = run(w)
    assert outcome.status == "COMPLETED", outcome.error
    descriptor = w.store.artifacts.verify(outcome.outputs.model.model)
    refs = w.refs
    assert descriptor.effective_base == refs.base and descriptor.storage_kind == "adapter"
    assert descriptor.parents == tuple(sorted((refs.base, refs.first),
                                              key=lambda item: (item.artifact_id, item.inventory_sha256)))
    bound = yaml.safe_load((launch_dir(w) / "train.bound.yaml").read_text(encoding="utf-8"))
    assert bound["create_new_adapter"] is False and refs.first.artifact_id in bound["adapter_name_or_path"]
    w.store.record_outputs(w.lease, outcome.outputs)


def test_full_training_output_binds_verified_target_inventory(tmp_path, monkeypatch):
    from tuning_factory.model_ops import verify_inspected_targets
    w = world(tmp_path, monkeypatch, ("full_train",), method="full")
    outcome = run(w)
    assert outcome.status == "COMPLETED", outcome.error
    ref = outcome.outputs.model.model
    descriptor = w.store.artifacts.verify(ref)
    assert (descriptor.storage_kind, descriptor.adapter, descriptor.effective_base) == ("full_model", None, w.refs.base)
    files = w.store.artifacts.path(ref)
    assert sorted(path.name for path in files.rglob("*")) == [
        "config.json", "generation_config.json", "model.safetensors", "tokenizer.json", "tokenizer_config.json"]
    assert (files / "config.json").read_bytes() == (launch_dir(w) / "output" / "config.json").read_bytes()
    assert (files / "tokenizer.json").read_bytes() == inputs.TOKENIZER["tokenizer.json"]
    (targets,) = outcome.inspected_targets
    assert targets.base == ref and verify_inspected_targets(targets, descriptor, ref) == targets


def test_engine_tokenizer_vocab_change_refuses_output(tmp_path, monkeypatch):
    grown = {"tokenizer.json": canonical({"version": "1.0", "added_tokens": [
        {"id": 32, "content": "<|new|>", "special": True}]}), "tokenizer_config.json": b"{}"}
    w = world(tmp_path, monkeypatch, tokenizer=grown)
    before = artifact_entries(w.store)
    outcome = run(w)
    assert (outcome.status, outcome.uncertain, outcome.outputs) == ("FAILED", False, None)
    assert "engine tokenizer output changes the vocabulary; refusing artifact" in outcome.error
    assert artifact_entries(w.store) == before
    assert w.store.jobs.get(w.job_id)["status"] == "FAILED" and not held_locks(w)


def test_remote_failure_is_final_and_releases_the_lock(tmp_path, monkeypatch):
    w = world(tmp_path, monkeypatch, fail="engine exited with status 1")
    before = artifact_entries(w.store)
    outcome = run(w)
    assert (outcome.status, outcome.uncertain, outcome.outputs) == ("FAILED", False, None)
    assert "engine exited with status 1" in outcome.error
    assert artifact_entries(w.store) == before and not held_locks(w)
    assert w.store.jobs.get(w.job_id)["status"] == "FAILED" and len(w.host.launches) == 1


def test_an_executor_without_a_valid_result_leaves_the_attempt_uncertain(tmp_path, monkeypatch):
    w = world(tmp_path, monkeypatch)

    class Silent(SSHExecutor):
        def run_operation(self, *args, **kwargs):
            return None   # a broken transport: whether the worker runs is unknown

    executor = Silent("test-host", 22, "root", "unused", client=SimpleNamespace(), remote_root=RUNPOD_ROOT + "/jobs",
                      model_cache_root=RUNPOD_ROOT + "/model-cache", pod=dict(POD))
    outcome = run(w, executor=executor)
    assert (outcome.status, outcome.uncertain, outcome.outputs) == ("FAILED", True, None)
    assert held_locks(w) and "no valid operation result" in outcome.error


def test_cancellation_requested_during_the_run_wins_over_a_late_completion(tmp_path, monkeypatch):
    w = world(tmp_path, monkeypatch)
    before = artifact_entries(w.store)
    w.host.after_worker = lambda directory: w.store.jobs.cancel(w.job_id)
    outcome = run(w)
    assert (outcome.status, outcome.uncertain, outcome.outputs) == ("CANCELED", False, None)
    assert artifact_entries(w.store) == before and not held_locks(w)
    assert w.store.jobs.get(w.job_id)["status"] == "CANCELED"


# -- 7./8. merge and materialize outputs ---------------------------------------------------------

def test_merge_output_descriptor_rank_and_parents(tmp_path, monkeypatch):
    w = world(tmp_path, monkeypatch, ("merge",))
    outcome = run(w)
    assert outcome.status == "COMPLETED", outcome.error
    ref = outcome.outputs.model.model
    descriptor = w.store.artifacts.verify(ref)
    refs = w.refs
    assert descriptor.storage_kind == "adapter" and descriptor.effective_base == refs.base
    assert descriptor.parents == tuple(sorted((refs.base, refs.first, refs.second),
                                              key=lambda item: (item.artifact_id, item.inventory_sha256)))
    assert (descriptor.adapter.rank, descriptor.adapter.alpha, descriptor.adapter.method) == (5, 5, "lora")
    receipt = json.loads((launch_dir(w) / "operation-receipt.json").read_bytes())
    assert ref.inventory_sha256 == receipt["output_inventory_sha256"]   # frozen bytes are the receipted output
    assert w.calls == ["merge"] and outcome.inspected_targets == ()
    w.store.record_outputs(w.lease, outcome.outputs)


def test_materialize_output_is_full_unquantized_with_verified_targets(tmp_path, monkeypatch):
    from tuning_factory.model_ops import verify_inspected_targets
    w = world(tmp_path, monkeypatch, ("full",))
    outcome = run(w)
    assert outcome.status == "COMPLETED", outcome.error
    ref = outcome.outputs.model.model
    descriptor = w.store.artifacts.verify(ref)
    refs = w.refs
    assert (descriptor.storage_kind, descriptor.quantization_bit, descriptor.adapter) == ("full_model", None, None)
    assert descriptor.effective_base == refs.base and descriptor.config_sha256 == inputs.ROOT.config_sha256
    assert descriptor.parents == tuple(sorted((refs.base, refs.first),
                                              key=lambda item: (item.artifact_id, item.inventory_sha256)))
    receipt = json.loads((launch_dir(w) / "operation-receipt.json").read_bytes())
    assert ref.inventory_sha256 == receipt["output_inventory_sha256"]
    (targets,) = outcome.inspected_targets
    assert targets.base == ref and [module.name for module in targets.modules] == list(inputs.MODULES)
    assert verify_inspected_targets(targets, descriptor, ref) == targets
    assert outcome.outputs.inspected_targets == ()   # Task 8 persists them through its own verifier
    w.store.record_outputs(w.lease, outcome.outputs)


@pytest.mark.parametrize("kinds,output", [(("merge",), "model-op/output"), (("train",), "output")])
def test_outputs_seed_the_remote_cache_from_their_launch(tmp_path, monkeypatch, kinds, output):
    """Relies on the 7.4 publish contract (7115a99): only same-size weights are copied on the host and the frozen
    config/tokenizer bytes are uploaded, so a training engine's re-saved tokenizer never fails verification."""
    from tuning_factory.operation_runner import verify_cached_artifact
    w = world(tmp_path, monkeypatch, kinds)
    outcome = run(w)
    assert outcome.status == "COMPLETED", outcome.error
    ref = outcome.outputs.model.model
    record = json.loads((launch_dir(w).parent / (w.claimed["launch_id"] + ".cache-seed.json")).read_bytes())
    assert (record["status"], record["error"], record["artifact"]) == ("published", None, ref.model_dump(mode="json"))
    assert record["source_remote_dir"] == f"{RUNPOD_ROOT}/jobs/{w.job_id}/{w.claimed['launch_id']}/{output}"
    cache = w.host.sftp.path(RUNPOD_ROOT + "/model-cache")
    verify_cached_artifact(cache, ref)
    files = cache / ref.artifact_id / ref.inventory_sha256 / "files"
    assert (files / "tokenizer_config.json").read_bytes() == inputs.TOKENIZER["tokenizer_config.json"]
    seeded = [path for path in w.host.sftp.puts if f"/{ref.artifact_id}/" in path]
    assert seeded and not any(path.endswith(".safetensors") for path in seeded)   # weights copied on the host


def test_runner_launches_on_an_ssh_server_destination(tmp_path, monkeypatch):
    from test_gpu_servers import observation, settings
    facts = observation()
    profile = settings().profile(facts)
    w = world(tmp_path, monkeypatch, ("merge",), server=(profile, facts))
    assert w.lease.resource_id == facts.exclusivity_key
    outcome = run(w)
    assert outcome.status == "COMPLETED", outcome.error
    assert len(w.host.launches) == 1 and "CUDA_VISIBLE_DEVICES=" in w.host.launches[0]
    # Another server profile revision (even for the same machine) is never this attempt's destination.
    other = world(tmp_path / "other", monkeypatch, ("merge",), server=(profile, facts))
    changed = profile.model_copy(update={"revision": "2"})
    executor_calls = []
    executor = recording_executor(executor_calls, remote_root=profile.transport.remote_root + "/jobs",
                                  pod=None, server_profile=changed, server_facts=facts)
    refused = other.runner.run_claimed(other.claimed, executor, approval=other.approval)
    assert refused.status == "FAILED" and "destination" in refused.error and executor_calls == []


# -- 9. evaluation receipts (F11) ----------------------------------------------------------------

def test_evaluation_receipt_uses_benchmark_fingerprint_and_attempt(tmp_path, monkeypatch):
    from tuning_factory.operations import resolve_local_selection
    w = world(tmp_path, monkeypatch, ("eval", "select"))
    outcome = run(w)
    assert outcome.status == "COMPLETED", outcome.error
    assert outcome.outputs.model is None and outcome.inspected_targets == ()
    binding = outcome.outputs.evaluation
    record = binding.evaluation
    references = ["negative", "positive"]   # registry rows 1, 0: never the engine's labels
    assert dict(record.metrics) == CommonEvaluator().evaluate(list(PREDICTIONS), references, "classification")
    assert record.benchmark_sha256 == w.refs.benchmark.fingerprint()
    assert (record.model, record.status, record.producer_attempt_id) == (w.refs.first, "completed",
                                                                         w.lease.attempt_id)
    assert (record.synthetic, record.simulation) == (False, False)
    assert (binding.node_id, binding.tracking_acknowledged, binding.producer) == ("eval", False, None)
    assert w.calls == ["predict.yaml"]
    w.store.record_outputs(w.lease, outcome.outputs)   # failed before the F11 fix
    w.store.acknowledge_tracking(w.lease)
    evaluations = tuple(NodeEvaluationBinding(node_id=evidence.node_id, evaluation=saved.evaluation.evaluation,
                                              tracking_acknowledged=evidence.tracking_acknowledged, producer=evidence)
                        for evidence, saved in w.store.ledger(w.run["id"]) if saved.evaluation)
    bindings = VerifiedBindings(registry=w.store.registry, artifact_store=w.store.artifacts,
                                producers=w.producers, evaluations=evaluations, tracking_destination=TRACKING)
    selection = compile_node(w.plan, "select", bindings)   # the select path accepts the same record
    assert selection.evaluations == (record,)
    receipt = resolve_local_selection(selection)
    assert (receipt.status, receipt.winner) == ("selected", w.refs.first)


# -- 10. crash-idempotent rebuild ----------------------------------------------------------------

def crash_once(monkeypatch, owner, name, when, *, after=True):
    """Make ``owner.name`` crash the first time ``when(args)`` holds, after (or instead of) the real call."""
    original, state = getattr(owner, name), {"crashed": False}

    def wrapper(*args, **kwargs):
        if state["crashed"] or not when(args):
            return original(*args, **kwargs)
        state["crashed"] = True
        if after:
            original(*args, **kwargs)
        raise Crash(name)

    monkeypatch.setattr(owner, name, wrapper)


@pytest.mark.parametrize("point", ["after_result", "after_freeze", "after_completed", "after_release"])
def test_outputs_rebuild_is_idempotent_after_crash(tmp_path, monkeypatch, point):
    w = world(tmp_path, monkeypatch)
    jobs, before = w.store.jobs, artifact_entries(w.store)
    if point == "after_result":
        crash_once(monkeypatch, jobs, "transition", lambda args: args[1] == "EVALUATING", after=False)
    elif point == "after_freeze":
        crash_once(monkeypatch, w.store.artifacts, "freeze", lambda args: True)
    elif point == "after_completed":
        crash_once(monkeypatch, jobs, "transition", lambda args: args[1] == "COMPLETED")
    else:
        crash_once(monkeypatch, jobs, "release_gpu", lambda args: True)
    with pytest.raises(Crash):
        run(w)
    jobs.recover(stale_after_seconds=-1)   # the restarted controller finds a stale heartbeat
    outcome = w.runner.reconcile(w.job_id, w.executor)
    assert (outcome.status, outcome.uncertain) == ("COMPLETED", False)
    assert w.calls == ["train.bound.yaml"] and len(w.host.launches) == 1   # never trained or launched again
    assert jobs.get(w.job_id)["status"] == "COMPLETED" and not held_locks(w)
    ref = outcome.outputs.model.model
    assert len(artifact_entries(w.store)) == len(before) + 1   # one frozen output, whatever the crash point
    rebuilt = [w.runner.outputs_for(w.job_id) for _ in range(2)]
    assert rebuilt[0] == rebuilt[1] == outcome
    assert canonical(rebuilt[0].outputs.model.model_dump(mode="json")) == canonical(
        outcome.outputs.model.model_dump(mode="json"))
    assert w.store.artifacts.verify(ref).producer_attempt_id == w.lease.attempt_id
    current = jobs.current_lease(w.job_id)
    w.store.record_outputs(current, outcome.outputs)
    w.store.record_outputs(current, rebuilt[1].outputs)   # the same immutable receipt


def test_crash_after_launch_reservation_stays_uncertain_and_never_relaunches(tmp_path, monkeypatch):
    w = world(tmp_path, monkeypatch)
    crash_once(monkeypatch, w.executor, "run_operation", lambda args: True, after=False)
    with pytest.raises(Crash):
        run(w)
    w.store.jobs.recover(stale_after_seconds=-1)
    assert w.runner.reconcile(w.job_id, w.executor) is None   # whether the host saw the launch is unprovable
    assert held_locks(w) and w.host.commands == []
    taken = w.store.takeover_attempt(w.ctl, w.job_id)
    with pytest.raises(ValueError, match="launch already started or uncertain"):
        run(w, claimed=taken)
    assert w.host.commands == [] and w.calls == []


def test_crash_before_launch_reservation_resumes_or_releases(tmp_path, monkeypatch):
    w = world(tmp_path, monkeypatch)
    crash_once(monkeypatch, w.store.jobs, "begin_launch", lambda args: True, after=False)
    with pytest.raises(Crash):
        run(w)
    # The same attempt resumes once under a new actor token: begin_launch never ran.
    taken = w.store.takeover_attempt(w.ctl, w.job_id)
    outcome = run(w, claimed=taken)
    assert outcome.status == "COMPLETED", outcome.error
    assert w.calls == ["train.bound.yaml"] and len(w.host.launches) == 1
    # A crashed, never-launched attempt that the controller marked stale is released by reconcile.
    other = world(tmp_path / "other", monkeypatch)
    crash_once(monkeypatch, other.store.jobs, "begin_launch", lambda args: True, after=False)
    with pytest.raises(Crash):
        run(other)
    other.store.jobs.recover(stale_after_seconds=-1)
    released = other.runner.reconcile(other.job_id, other.executor)
    assert (released.status, released.uncertain, released.outputs) == ("FAILED", False, None)
    assert not held_locks(other) and other.host.commands == []


# -- 11. outputs flow into record_outputs --------------------------------------------------------

def test_runner_outputs_flow_into_record_outputs(tmp_path, monkeypatch):
    w = world(tmp_path, monkeypatch)
    outcome = run(w)
    assert outcome.status == "COMPLETED", outcome.error
    w.store.record_outputs(w.lease, outcome.outputs)
    w.store.acknowledge_tracking(w.lease)
    (evidence, saved), = w.store.ledger(w.run["id"])
    assert (evidence.status, evidence.attempt_id, saved.model) == ("completed", w.lease.attempt_id,
                                                                    outcome.outputs.model)
    producer_evidence = w.store.producer_evidence(w.lease, outcome.outputs.model.model)
    assert (producer_evidence.job_id, producer_evidence.node_id, producer_evidence.training_stage) == (
        w.job_id, "train", "sft")


# -- 12. legacy paths ----------------------------------------------------------------------------

def test_factory_run_claimed_still_refuses_v2_and_outcome_shape(tmp_path, monkeypatch):
    from tuning_factory.runner import Factory
    graph = build_graph(tmp_path, ("train",))
    claimed = graph.store.claim_ready(FACTS, graph.ctl)
    with pytest.raises(NotImplementedError, match="operation worker"):
        Factory(tmp_path / "state").run_claimed(claimed)
    fields = [field.name for field in api().OperationOutcome.__dataclass_fields__.values()]
    assert fields == ["job_id", "attempt_id", "status", "uncertain", "outputs", "inspected_targets", "error"]


def test_runner_requires_the_ledger_state_directory(tmp_path):
    store = GraphStore(tmp_path / "state" / "jobs.db")
    with pytest.raises(ValueError, match="state directory"):
        api().OperationAttemptRunner(store, root=tmp_path / "elsewhere")
    with pytest.raises(TypeError):
        api().OperationAttemptRunner(store.jobs, root=tmp_path / "state")
