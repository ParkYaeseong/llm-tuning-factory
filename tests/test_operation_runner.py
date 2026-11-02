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
from tuning_factory.graph_schema import GraphSpec, validate_graph
from tuning_factory.graph_store import GraphStore
from tuning_factory.jobs import AttemptLease
from tuning_factory.model_artifacts import ModelInput, ModelLoadRef
from tuning_factory.projects import DatasetBinding, DatasetRef, ProjectSpec, Revision
from tuning_factory.recipes import RecipeSpec
from tuning_factory.registry import canonical, digest
from tuning_factory.training import OFT_UNFREEZABLE, LlamaFactoryBackend


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
BASE_TOKENIZER = {name: json.loads(data) for name, data in inputs.TOKENIZER.items()}
# LLaMA-Factory re-saves the tokenizer: other bytes and a pad-token assignment, the same vocabulary and special ids.
ENGINE_TOKENIZER = {
    "tokenizer.json": json.dumps(BASE_TOKENIZER["tokenizer.json"], indent=2).encode("utf-8"),
    "tokenizer_config.json": json.dumps({**BASE_TOKENIZER["tokenizer_config.json"], "model_max_length": 64,
                                         "pad_token": "<unk>", "padding_side": "right"}, indent=2).encode("utf-8"),
}


def changed_tokenizer(case):
    """The engine tokenizer of one vocabulary change (the base vocabulary is TOKENIZER's)."""
    tokenizer, config = json.loads(json.dumps(BASE_TOKENIZER["tokenizer.json"])), dict(
        BASE_TOKENIZER["tokenizer_config.json"])
    if case == "added_token":
        tokenizer["added_tokens"].append({"id": 3, "content": "<|new|>", "special": True})
    elif case == "renumbered":
        tokenizer["model"]["vocab"] = {"<unk>": 0, "q": 2, "v": 1}
    elif case == "grown":
        tokenizer["model"]["vocab"]["w"] = 3
    elif case == "special_id":
        config["added_tokens_decoder"] = {"1": {"content": "<unk>", "special": True}}
    return {"tokenizer.json": canonical(tokenizer), "tokenizer_config.json": canonical(config)}
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
    "train_hf": lambda r: train_node("train_hf", r.recipe, ModelInput(policy=ModelLoadRef(base=inputs.ROOT),
                                                                      initialization="new_adapter"), r.profile),
    "oft_train": lambda r: train_node("oft_train", r.oft_recipe, ModelInput(
        policy=ModelLoadRef(base=r.base), initialization="new_adapter"), r.profile),
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


def graph_definition(tmp_path, kinds, *, export_allowed=True, executor=EXECUTOR):
    """A project revision and execution choices over frozen base/adapter artifacts in the GraphStore's store."""
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
    oft_recipe = revision(RecipeSpec(recipe_id="oft", name="oft", training_method="oft"))
    benchmark = BenchmarkSpec(dataset=held, evaluator_kind="classification", evaluator_version="1",
                              ordered_sample_ids=("row:1", "row:0"), max_new_tokens=8, cutoff_len=64, seed=1,
                              precision="bf16", quantization_bit=None,
                              tokenizer_fingerprint=inputs.ROOT.tokenizer_fingerprint, template="qwen",
                              semantic_model_kind="causal_lm")
    refs = SimpleNamespace(base=base, first=first, second=second, recipe=recipe, full_recipe=full_recipe,
                           oft_recipe=oft_recipe, benchmark=benchmark, profile=executor.profile_id)
    graph = GraphSpec.model_validate({"nodes": [NODES[kind](refs) for kind in kinds]})
    project = revision(ProjectSpec(project_id="project.1", name="Project", datasets=(
        DatasetBinding(binding_id="data", role="train", dataset=data),
        DatasetBinding(binding_id="held", role="validation", dataset=held)), graph=graph.model_dump(mode="json")))
    producers = tuple(producer(ref, marker) for marker, ref in (("base", base), ("first", first), ("second", second)))
    choices = ExecutionChoices(
        recipes=(recipe, full_recipe, oft_recipe),
        benchmarks=(BenchmarkRevision(spec=benchmark, revision_sha256=benchmark.fingerprint()),),
        datasets=(snapshot_dataset(registry, data), snapshot_dataset(registry, held)),
        models=(ModelContext(root=inputs.ROOT, profile=inputs.PROFILE),),
        artifacts=tuple(snapshot_artifact(models, item.model, item) for item in producers), executors=(executor,),
        node_resources=tuple(NodeResources(node_id=node.id, timeout_seconds=900) for node in graph.nodes
                             if node.kind != "select"),
        base_targets=(inputs.evidence(base),), tracking_destination=TRACKING)
    return SimpleNamespace(store=store, project=project, choices=choices, refs=refs, producers=producers)


def build_graph(tmp_path, kinds, *, simulation=False, export_allowed=True, executor=EXECUTOR, facts=FACTS):
    definition = graph_definition(tmp_path, kinds, export_allowed=export_allowed, executor=executor)
    store, refs, producers = definition.store, definition.refs, definition.producers
    plan = plan_graph(definition.project, definition.choices, (facts,), simulation=simulation)
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
                           hf_home=base + "/hf-cache", model_cache_root=base + "/model-cache",
                           runtime_lock_file=base + "/runtime/in-use.lock", **destination)
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


def forge_manifest(w, mutate):
    """Re-sign the job's ledger manifest after ``mutate``: it passes verify_manifest but breaks another rule."""
    body = {key: value for key, value in w.store.jobs.get(w.job_id)["manifest"].items() if key != "sha256"}
    mutate(body)
    body["sha256"] = digest(canonical(body))
    with w.store.jobs.connection() as db:
        db.execute("UPDATE jobs SET manifest=? WHERE id=?", (canonical(body).decode(), w.job_id))


def move_attempt(w, resource):
    """Rebind the claimed attempt and its lock to another resource; returns a valid lease for it."""
    lease = w.lease
    with w.store.jobs.connection() as db:
        db.execute("UPDATE job_attempts SET resource_id=? WHERE attempt_id=?", (resource, lease.attempt_id))
        db.execute("UPDATE gpu_locks SET gpu=? WHERE job_id=?", (resource, lease.job_id))
        db.execute("UPDATE resource_leases SET resource_id=? WHERE attempt_id=?", (resource, lease.attempt_id))
    return AttemptLease(lease.job_id, lease.attempt_id, lease.lease_token, resource)


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
    ("cacheless_executor", "configured remote model cache"),
    ("export_refused", "dataset export has not been authorized"),
    ("dataset_changed", "dataset registration differs from manifest"),
    ("dataset_missing", "manifest datasets differ from the operation"),
    ("source_hash", "source hash differs from immutable manifest"),
    ("resolved_form", "manifest operation is not in its resolved form"),
    ("other_resources", "operation resources differ from the approved plan"),
    ("destination_revision", "operation destination is not an approved destination revision"),
    ("resource_mismatch", "attempt resource differs from the operation's pinned destination"),
    ("forged_launch", "launch identity"),
])
def test_runner_refuses_select_simulation_unapproved_or_other_destination(tmp_path, monkeypatch, case, message):
    from tuning_factory import attempt_runner
    options = {"simulation": case == "simulation", "export_allowed": case != "export_refused"}
    merge_cases = {"resolved_form", "other_resources"}   # an operation without a training config to keep valid
    w = world(tmp_path, monkeypatch, ("merge",) if case in merge_cases else ("train",), graph_options=options)
    executor_calls = []
    executor = recording_executor(executor_calls)
    approval, claimed, lease = w.approval, w.claimed, w.lease
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
    elif case == "cacheless_executor":
        executor = recording_executor(executor_calls, model_cache_root=None)
    elif case == "dataset_changed":
        forge_manifest(w, lambda body: body["datasets"][0].update(synthetic=True))
    elif case == "dataset_missing":
        forge_manifest(w, lambda body: body.update(datasets=[]))
    elif case == "source_hash":
        monkeypatch.setattr(attempt_runner, "source_hash", lambda: "0" * 64)
    elif case == "resolved_form":
        forge_manifest(w, lambda body: body["operation"].pop("method"))   # a default the model fills back in
    elif case == "other_resources":
        forge_manifest(w, lambda body: body["operation"]["resources"].update(timeout_seconds=901))
    elif case == "destination_revision":
        monkeypatch.setattr(attempt_runner, "approval_destination", lambda profile: {"profile_id": "unapproved"})
    elif case == "resource_mismatch":
        lease = move_attempt(w, "existing_runpod:pod9")
        claimed = {**claimed, "lease": lease}
    elif case == "forged_launch":
        claimed = {**claimed, "launch_id": "f" * 32}
    outcome = w.runner.run_claimed(claimed, executor, approval=approval)
    assert (outcome.status, outcome.uncertain, outcome.outputs) == ("FAILED", False, None)
    assert message in outcome.error
    assert executor_calls == [] and w.host.commands == [] and w.calls == []
    job = w.store.jobs.get(w.job_id)
    assert job["status"] == "FAILED" and message in job["error"]
    assert message in (w.state / "jobs" / w.job_id / "error.txt").read_text(encoding="utf-8")
    assert not w.store.jobs.gpu_locked(lease.resource_id)   # abandon_unlaunched: nothing launched, lock freed
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
    error = (tmp_path / "state" / "jobs" / claimed["id"] / "error.txt").read_text(encoding="utf-8")
    assert "local selection never dispatches to a GPU worker" in error


def test_planning_refuses_an_oft_train_node_whose_output_must_be_frozen(tmp_path):
    definition = graph_definition(tmp_path, ("oft_train",))
    issues = validate_graph(definition.project.spec, definition.choices.recipes, definition.store.registry)
    assert [issue.message for issue in issues if issue.node_id == "oft_train"] == [OFT_UNFREEZABLE]
    with pytest.raises(ValueError, match="OFT training is unsupported"):
        plan_graph(definition.project, definition.choices, (FACTS,), simulation=False)


def test_runner_refuses_oft_training_before_launch(tmp_path, monkeypatch):
    from tuning_factory import graph_planner
    original = graph_planner._definition_issues

    def before_the_rule(*args):   # a plan approved before planning refused OFT train nodes
        return [issue for issue in original(*args) if issue.message != OFT_UNFREEZABLE]

    monkeypatch.setattr(graph_planner, "_definition_issues", before_the_rule)
    w = world(tmp_path, monkeypatch, ("oft_train",))
    assert w.claimed["manifest"]["operation"]["config"]["training_method"] == "oft"
    outcome = run(w)
    assert (outcome.status, outcome.uncertain, outcome.outputs) == ("FAILED", False, None)
    assert OFT_UNFREEZABLE in outcome.error
    assert w.host.commands == [] and w.calls == [] and not held_locks(w)   # never launched on the GPU host
    assert not (w.state / "jobs" / w.job_id / "launches").exists()


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


def test_reconcile_rebuilds_the_request_from_the_ledger(tmp_path, monkeypatch):
    from tuning_factory.worker_protocol import WorkerRequest
    w = world(tmp_path, monkeypatch)
    w.host.mode = "lose_start_response"
    assert run(w).uncertain
    w.host.finish()
    command = launch_dir(w) / "command.json"
    original = command.read_bytes()
    forged = WorkerRequest.model_validate_json(original).model_copy(update={"timeout_seconds": 60})
    command.write_bytes(canonical(forged.model_dump(mode="json")))   # a local file must not redefine the attempt
    with pytest.raises(ValueError, match="command.json differs from the worker request"):
        w.runner.reconcile(w.job_id, w.executor)
    assert w.store.jobs.get(w.job_id)["status"] == "FAILED" and held_locks(w)
    command.write_bytes(original)
    assert w.runner.reconcile(w.job_id, w.executor).status == "COMPLETED"
    assert w.calls == ["train.bound.yaml"] and len(w.host.launches) == 1


@pytest.mark.parametrize("engine", [{"fail": "engine exited with status 1"},
                                    {"tokenizer": changed_tokenizer("added_token")}])
def test_reconcile_never_reopens_a_final_attempt(tmp_path, monkeypatch, engine):
    w = world(tmp_path, monkeypatch, **engine)
    before = artifact_entries(w.store)
    outcome = run(w)
    assert (outcome.status, outcome.uncertain) == ("FAILED", False) and not held_locks(w)
    with pytest.raises(ValueError, match="attempt is final"):
        w.runner.reconcile(w.job_id, w.executor)   # even though the remote result may say COMPLETED
    assert w.store.jobs.get(w.job_id)["status"] == "FAILED" and artifact_entries(w.store) == before


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


def test_train_on_an_hf_root_base_freezes_the_worker_verified_snapshot_bytes(tmp_path, monkeypatch):
    hub = inputs.fake_hub(monkeypatch, inputs.tiny_snapshot)   # the GPU host downloads the root, as in v1
    w = world(tmp_path, monkeypatch, ("train_hf",))
    outcome = run(w)
    assert outcome.status == "COMPLETED", outcome.error
    assert [(call["repo_id"], call["revision"]) for call in hub] == [(inputs.ROOT.repo_id, inputs.ROOT.revision)]
    ref = outcome.outputs.model.model
    descriptor = w.store.artifacts.verify(ref)
    assert (descriptor.root, descriptor.effective_base, descriptor.parents) == (inputs.ROOT, inputs.ROOT, ())
    files = w.store.artifacts.path(ref)
    assert sorted(path.name for path in files.rglob("*")) == ARTIFACT_FILES
    assert (files / "config.json").read_bytes() == inputs.CONFIG   # from base-snapshot/, checked against ROOT
    for name, data in inputs.TOKENIZER.items():
        assert (files / name).read_bytes() == data
    result = json.loads((launch_dir(w).parent / (w.claimed["launch_id"] + ".result.json")).read_bytes())
    assert {"base-snapshot/config.json", "base-snapshot/tokenizer.json"} <= set(result["evidence"])
    w.store.record_outputs(w.lease, outcome.outputs)


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


@pytest.mark.parametrize("case", ["added_token", "renumbered", "grown", "special_id"])
def test_engine_tokenizer_vocab_change_refuses_output(tmp_path, monkeypatch, case):
    w = world(tmp_path, monkeypatch, tokenizer=changed_tokenizer(case))
    before = artifact_entries(w.store)
    outcome = run(w)
    assert (outcome.status, outcome.uncertain, outcome.outputs) == ("FAILED", False, None)
    assert "engine tokenizer output changes the vocabulary; refusing artifact" in outcome.error
    assert artifact_entries(w.store) == before
    assert w.store.jobs.get(w.job_id)["status"] == "FAILED" and not held_locks(w)


def test_full_training_output_without_target_inventory_is_refused(tmp_path, monkeypatch):
    w = world(tmp_path, monkeypatch, ("full_train",), method="full")
    before = artifact_entries(w.store)

    def drop_inventory(directory):   # a worker result that lacks the inventory the primary runtime must write
        (directory / "target-inventory.json").unlink()
        for name, key in (("artifacts.json", None), ("result.json", "evidence")):
            data = json.loads((directory / name).read_bytes())
            (data[key] if key else data).pop("target-inventory.json")
            (directory / name).write_bytes(canonical(data))

    w.host.after_worker = drop_inventory
    outcome = run(w)
    assert (outcome.status, outcome.uncertain, outcome.outputs) == ("FAILED", False, None)
    assert "requires its inspected target inventory" in outcome.error
    assert artifact_entries(w.store) == before and not held_locks(w)


def test_engine_tokenizer_check_compares_real_vocabulary_facts(tmp_path):
    for name, files in (("base", inputs.TOKENIZER), ("engine", ENGINE_TOKENIZER)):
        (tmp_path / name).mkdir()
        for file, data in files.items():
            (tmp_path / name / file).write_bytes(data)
    original, resaved = (outputs_api().vocabulary(tmp_path / name) for name in ("base", "engine"))
    assert original == resaved   # other bytes and a pad-token assignment: the same ids
    assert original["tokenizer.json"] == {"vocab": {"<unk>": 0, "q": 1, "v": 2}, "added": [(0, "<unk>", True)]}
    assert original["tokenizer_config.json"] == [("0", "<unk>", True)]


def test_vocabulary_size_is_read_from_text_config_for_composite_configs():
    from tuning_factory.operation_outputs import _vocab_size
    assert _vocab_size({"text_config": {"vocab_size": 248320}}) == 248320
    assert _vocab_size({"vocab_size": 151936}) == 151936
    assert _vocab_size({"text_config": {"vocab_size": 1}, "vocab_size": 2}) == 1
    assert _vocab_size({"text_config": {}}) is None


COMPOSITE_CONFIG = {"model_type": "qwen3_5", "text_config": {"model_type": "qwen3_5_text", "hidden_size": 2,
                                                             "vocab_size": 248320}}


@pytest.mark.parametrize("vocabulary,status", [(248320, "COMPLETED"), (248321, "FAILED")])
def test_full_training_of_a_composite_compares_its_text_config_vocabulary(tmp_path, monkeypatch, vocabulary, status):
    """Composite configs keep vocab_size only in text_config: a changed text vocabulary is refused, an equal one
    (beside a top level without vocab_size) is accepted."""
    full_model, peft_adapter, engine_output = inputs.full_model, inputs.peft_adapter, write_engine_output

    def composite_output(output, tokenizer, method="lora"):
        engine_output(output, tokenizer, method)
        text = {**COMPOSITE_CONFIG["text_config"], "vocab_size": vocabulary}
        (output / "config.json").write_bytes(canonical({**COMPOSITE_CONFIG, "text_config": text,
                                                        "torch_dtype": "bfloat16"}))

    # The plan's base artifact (and the adapters frozen on it) carries the composite config.
    monkeypatch.setattr(inputs, "full_model", lambda *a, **kw: full_model(*a, config=canonical(COMPOSITE_CONFIG), **kw))
    monkeypatch.setattr(inputs, "peft_adapter",
                        lambda *a, **kw: peft_adapter(*a, config=canonical(COMPOSITE_CONFIG), **kw))
    monkeypatch.setitem(globals(), "write_engine_output", composite_output)
    w = world(tmp_path, monkeypatch, ("full_train",), method="full")
    outcome = run(w)
    assert outcome.status == status, outcome.error
    if status == "FAILED":
        assert outcome.error.endswith(outputs_api().VOCABULARY_CHANGED) and outcome.outputs is None
    else:
        files = w.store.artifacts.path(outcome.outputs.model.model)
        assert json.loads((files / "config.json").read_bytes())["text_config"]["vocab_size"] == 248320


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
    assert receipt["receipt"]["policy_id"] == "model-ops-f32-v2"
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
    if point == "after_release":   # nothing to reconcile: the attempt is final, its outputs are rebuilt
        with pytest.raises(ValueError, match="attempt is final"):
            w.runner.reconcile(w.job_id, w.executor)
        outcome = w.runner.outputs_for(w.job_id)
    else:
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


@pytest.mark.parametrize("path", ["completed", "crash_after_result", "lost_start"])
def test_heartbeats_during_a_slow_run_never_disturb_the_attempt(tmp_path, monkeypatch, path):
    """Under load a run outlasts the 10 s heartbeat; here heartbeats fire throughout a slowed run (and around a
    crash or a lost start response) and must change nothing but the attempt's liveness timestamp."""
    import threading
    import time
    w = world(tmp_path, monkeypatch)
    jobs, beats = w.store.jobs, []
    original = jobs.heartbeat

    def counted(lease):
        beats.append(threading.current_thread().name)
        return original(lease)

    monkeypatch.setattr(jobs, "heartbeat", counted)
    monkeypatch.setattr(api().OperationAttemptRunner, "heartbeat_seconds", 0.01)
    w.host.after_worker = lambda directory: time.sleep(0.3)   # a slow worker: many heartbeats during the run
    if path == "crash_after_result":
        crash_once(monkeypatch, jobs, "transition", lambda args: args[1] == "EVALUATING", after=False)
        with pytest.raises(Crash):
            run(w)
        jobs.recover(stale_after_seconds=-1)
        outcome = w.runner.reconcile(w.job_id, w.executor)
    elif path == "lost_start":
        w.host.mode = "lose_start_response"
        assert run(w).uncertain
        w.host.finish()
        outcome = w.runner.reconcile(w.job_id, w.executor)
    else:
        outcome = run(w)
    assert (outcome.status, outcome.uncertain) == ("COMPLETED", False), outcome.error
    assert w.calls == ["train.bound.yaml"] and len(w.host.launches) == 1
    assert jobs.get(w.job_id)["status"] == "COMPLETED" and not held_locks(w)
    if path != "lost_start":
        assert beats and all(name != threading.main_thread().name for name in beats)   # the heartbeat ran
    settled = len(beats)
    time.sleep(0.1)
    assert len(beats) == settled   # every heartbeat thread stopped with its run


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
    with pytest.raises(ValueError, match="attempt is final"):
        other.runner.reconcile(other.job_id, other.executor)


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


# -- 13. Task 8.1: public ledger boundaries and refusals that leave no artifact ----------------------

def test_runner_uses_public_bindings_for(tmp_path, monkeypatch):
    w = world(tmp_path, monkeypatch, ("continue",))
    original = getattr(GraphStore, "bindings_for", None)
    assert original is not None, "GraphStore.bindings_for is the public trusted-resolver hook"
    seen = []

    def spy(self, run_id, node_id):
        seen.append((run_id, node_id))
        return original(self, run_id, node_id)

    monkeypatch.setattr(GraphStore, "bindings_for", spy)
    outcome = run(w)
    assert outcome.status == "COMPLETED", outcome.error
    assert seen == [(w.run["id"], "continue")]   # the continuation evidence comes from the same hook compile used
    assert not hasattr(GraphStore, "_bindings")


def test_attempt_runner_uses_only_public_ledger_members():
    import ast
    source = Path(api().__file__).read_text(encoding="utf-8")
    private = sorted({node.attr for node in ast.walk(ast.parse(source))
                      if isinstance(node, ast.Attribute) and node.attr.startswith("_")
                      and not node.attr.startswith("__") and isinstance(node.value, ast.Attribute)
                      and node.value.attr in {"jobs", "graph", "graph_store"}})
    assert private == []
    assert "resource_leases" not in source and ".connection(" not in source   # no JobStore tables or SQL here


def forge_evidence(directory, name, data):
    """A worker that rewrites one evidence file and keeps the records consistent (what a forger would do)."""
    (directory / name).write_bytes(data)
    for record, key in (("artifacts.json", None), ("result.json", "evidence")):
        content = json.loads((directory / record).read_bytes())
        (content[key] if key else content)[name] = digest(data)
        (directory / record).write_bytes(canonical(content))


@pytest.mark.parametrize("kinds", [("merge",), ("full",)])
def test_model_op_output_differing_from_its_receipt_leaves_no_artifact(tmp_path, monkeypatch, kinds):
    w = world(tmp_path, monkeypatch, kinds)
    before = artifact_entries(w.store)

    def forge_receipt(directory):
        receipt = json.loads((directory / "operation-receipt.json").read_bytes())
        forge_evidence(directory, "operation-receipt.json", canonical({**receipt, "output_inventory_sha256": "f" * 64}))

    w.host.after_worker = forge_receipt
    outcome = run(w)
    assert (outcome.status, outcome.uncertain, outcome.outputs) == ("FAILED", False, None)
    assert "output inventory differs from operation-receipt.json" in outcome.error
    assert artifact_entries(w.store) == before and not held_locks(w)


@pytest.mark.parametrize("kinds", [("merge",), ("full",)])
def test_model_op_receipt_under_an_undefined_policy_leaves_no_artifact(tmp_path, monkeypatch, kinds):
    """Collection accepts the two defined policies (v1 receipts of an operation launched by an older build)."""
    w = world(tmp_path, monkeypatch, kinds)
    before = artifact_entries(w.store)

    def forge_policy(directory):
        receipt = json.loads((directory / "operation-receipt.json").read_bytes())
        assert receipt["receipt"]["policy_id"] == "model-ops-f32-v2"
        receipt["receipt"]["policy_id"] = "model-ops-f32-v0"
        forge_evidence(directory, "operation-receipt.json", canonical(receipt))

    w.host.after_worker = forge_policy
    outcome = run(w)
    assert (outcome.status, outcome.uncertain, outcome.outputs) == ("FAILED", False, None)
    assert artifact_entries(w.store) == before and not held_locks(w)


@pytest.mark.parametrize("kinds", [("merge",), ("full",)])
def test_model_op_receipt_under_the_v1_policy_is_collected(tmp_path, monkeypatch, kinds):
    w = world(tmp_path, monkeypatch, kinds)

    def older_build(directory):
        receipt = json.loads((directory / "operation-receipt.json").read_bytes())
        receipt["receipt"]["policy_id"] = "model-ops-f32-v1"
        forge_evidence(directory, "operation-receipt.json", canonical(receipt))

    w.host.after_worker = older_build
    outcome = run(w)
    assert (outcome.status, outcome.uncertain) == ("COMPLETED", False) and outcome.outputs


@pytest.mark.parametrize("kinds,engine", [(("full_train",), {"method": "full"}), (("full",), {})])
def test_forged_target_inventory_of_a_full_model_leaves_no_artifact(tmp_path, monkeypatch, kinds, engine):
    w = world(tmp_path, monkeypatch, kinds, **engine)
    before = artifact_entries(w.store)

    def forge_targets(directory):
        inventory = json.loads((directory / "target-inventory.json").read_bytes())
        inventory["modules"][0]["output_dimension"] += 1
        forge_evidence(directory, "target-inventory.json", canonical(inventory))

    w.host.after_worker = forge_targets
    outcome = run(w)
    assert (outcome.status, outcome.uncertain, outcome.outputs) == ("FAILED", False, None)
    assert "inspected base dimensions differ from verified tensor inventory" in outcome.error
    assert artifact_entries(w.store) == before and not held_locks(w)


# -- 14. Task 8.2: failure kinds from persisted records and the safe release of a canceled attempt -------

def node_status(w):
    return next(node["status"] for node in w.store.get_run(w.run["id"])["nodes"] if node["node_id"] == "train")


@pytest.mark.parametrize("case,kind", [
    ("completed", None), ("remote_failed", "remote_failed"), ("freeze_refused", "output_invalid"),
    ("refused", "refused_before_launch"), ("before_submit", "failed_before_submit"), ("uncertain", "uncertain"),
    ("canceled", "canceled"), ("gate_then_recover", "refused_before_launch")])
def test_runner_failure_kind_classifies_from_persisted_records(tmp_path, monkeypatch, case, kind):
    from tuning_factory.jobs import GRAPH_LAUNCH_REFUSED
    engine = ({"fail": "engine exited with status 1"} if case == "remote_failed" else
              {"tokenizer": changed_tokenizer("added_token")} if case == "freeze_refused" else {})
    w = world(tmp_path, monkeypatch, graph_options={"export_allowed": case != "refused"}, **engine)
    if case == "uncertain":
        w.host.mode = "lose_start_response"
    elif case == "canceled":
        w.host.after_worker = lambda directory: w.store.jobs.cancel(w.job_id)
    elif case == "before_submit":   # fails after begin_launch, before the executor is called
        def unreadable(ref):
            raise OSError("dataset bytes unreadable")
        monkeypatch.setattr(w.runner, "_dataset_bytes", unreadable)
    if case == "gate_then_recover":   # paused at the gate, then a stale recover fails it: it never launched
        w.store.pause(w.run["id"])
        with pytest.raises(ValueError, match=GRAPH_LAUNCH_REFUSED):
            run(w)
        w.store.resume(w.run["id"])
        w.store.jobs.recover(stale_after_seconds=-1)
        assert held_locks(w)
    else:
        run(w)
    assert w.runner.failure_kind(w.job_id) == kind
    assert w.host.launches == ([] if case in {"refused", "before_submit", "gate_then_recover"} else w.host.launches)
    if kind in (None, "uncertain"):
        with pytest.raises(ValueError):
            w.store.classify_attempt(w.ctl, w.job_id, "remote_failed")   # completed, or still holding its lock
        return
    assert w.store.classify_attempt(w.ctl, w.job_id, kind) == kind   # the store agrees with the runner
    counted = 0 if case == "gate_then_recover" else 1
    assert w.store.attempt_disposition(w.job_id)["counted"] == counted and not held_locks(w)
    expected = "CANCELED" if kind == "canceled" else "RUNNING" if case == "gate_then_recover" else "FAILED"
    assert node_status(w) == expected   # max_attempts=1: only the uncounted attempt can be retried


@pytest.mark.parametrize("release", ["reconcile", "classify"])
def test_crash_between_cancel_and_release_is_released_on_its_accepted_result(tmp_path, monkeypatch, release):
    """7.5 re-review Minor B: CANCELED always follows an accepted result in ``_launch``, so execution stopped."""
    w = world(tmp_path, monkeypatch)
    w.host.after_worker = lambda directory: w.store.jobs.cancel(w.job_id)
    crash_once(monkeypatch, w.store.jobs, "release_gpu", lambda args: True, after=False)
    with pytest.raises(Crash):
        run(w)
    assert w.store.jobs.get(w.job_id)["status"] == "CANCELED" and held_locks(w)
    assert (launch_dir(w).parent / (w.claimed["launch_id"] + ".result.json")).is_file()
    assert w.runner.failure_kind(w.job_id) == "canceled"
    if release == "reconcile":
        outcome = w.runner.reconcile(w.job_id, w.executor)
        assert (outcome.status, outcome.uncertain, outcome.outputs) == ("CANCELED", False, None)
        assert not held_locks(w)
    assert w.store.classify_attempt(w.ctl, w.job_id, "canceled") == "canceled"
    assert not held_locks(w) and node_status(w) == "CANCELED"
    assert w.runner.failure_kind(w.job_id) == "canceled" and len(w.host.launches) == 1
    with pytest.raises(ValueError, match="attempt is final"):
        w.runner.reconcile(w.job_id, w.executor)


@pytest.mark.parametrize("evidence", ["missing", "other_launch"])
def test_a_canceled_attempt_without_an_accepted_result_keeps_its_lock(tmp_path, monkeypatch, evidence):
    w = world(tmp_path, monkeypatch)
    w.host.after_worker = lambda directory: w.store.jobs.cancel(w.job_id)
    crash_once(monkeypatch, w.store.jobs, "release_gpu", lambda args: True, after=False)
    with pytest.raises(Crash):
        run(w)
    record = launch_dir(w).parent / (w.claimed["launch_id"] + ".result.json")
    if evidence == "missing":
        record.unlink()
    else:   # a result naming another launch proves nothing about this one
        record.write_bytes(canonical({**json.loads(record.read_bytes()), "launch_id": "e" * 32}))
    assert w.runner.failure_kind(w.job_id) == "uncertain"
    assert w.runner.reconcile(w.job_id, w.executor) is None and held_locks(w)
    with pytest.raises(ValueError, match="holds its lock"):
        w.store.classify_attempt(w.ctl, w.job_id, "canceled")
    assert held_locks(w) and w.store.attempt_disposition(w.job_id) is None   # only the operator can release it


def test_release_uncontacted_after_a_crash_before_running_launches_once(tmp_path, monkeypatch):
    w = world(tmp_path, monkeypatch)
    crash_once(monkeypatch, w.store.jobs, "transition", lambda args: args[1] == "RUNNING", after=False)
    with pytest.raises(Crash):
        run(w)
    attempt = w.store.jobs.validated_attempt(w.lease)
    assert (attempt["status"], attempt["launch_started"]) == ("PROVISIONING", 1)
    assert (launch_dir(w) / "command.json").is_file() and not (launch_dir(w) / "remote-attempt.json").exists()
    assert w.store.release_uncontacted(w.ctl, w.job_id)
    with pytest.raises(ValueError, match="stale attempt lease"):
        w.store.jobs.transition(w.lease, "RUNNING")   # the old actor can never reach run_operation
    assert not held_locks(w) and w.host.commands == [] and w.calls == []
    assert w.store.requeue_retry(w.ctl, w.job_id)
    again = w.store.claim_ready(w.facts, w.ctl)   # max_attempts=1: the released attempt was not counted
    outcome = run(w, claimed=again)
    assert outcome.status == "COMPLETED", outcome.error
    assert len(w.host.launches) == 1 and w.calls == ["train.bound.yaml"]


# -- 15. Task 11.5 (R1 I-1, I-3, I-4): a cancel through reconcile, a transient collection failure, totals --------

def remote_launch(w):
    return w.host.sftp.path(f"{RUNPOD_ROOT}/jobs/{w.job_id}/{w.claimed['launch_id']}")


@pytest.mark.parametrize("cancel", ["run", "job"])
def test_a_cancel_wins_over_a_late_completion_collected_by_reconcile(tmp_path, monkeypatch, cancel):
    """R1 I-1: a cancel of the run or of the job while the attempt is FAILED-uncertain flags it, and the late
    COMPLETED result that reconcile collects is CANCELED, as in ``_launch``: nothing is frozen, the lock is released."""
    w = world(tmp_path, monkeypatch)
    before = artifact_entries(w.store)
    w.host.mode = "lose_start_response"
    assert run(w).uncertain and held_locks(w)
    if cancel == "run":
        w.store.cancel(w.run["id"])
    else:
        w.store.jobs.cancel(w.job_id)   # the CLI/desktop job-level cancel
    assert w.store.jobs.get(w.job_id)["cancel_requested"] == 1   # flagged although the attempt is FAILED
    w.host.finish()   # the remote ran on to a COMPLETED result
    assert json.loads((remote_launch(w) / "result.json").read_bytes())["status"] == "COMPLETED"
    outcome = w.runner.reconcile(w.job_id, w.executor)
    assert (outcome.status, outcome.uncertain, outcome.outputs) == ("CANCELED", False, None)
    assert w.store.jobs.get(w.job_id)["status"] == "CANCELED" and not held_locks(w)
    assert artifact_entries(w.store) == before   # no output of a canceled attempt is ever frozen
    assert w.calls == ["train.bound.yaml"] and len(w.host.launches) == 1
    assert w.runner.failure_kind(w.job_id) == "canceled"
    assert w.store.classify_attempt(w.ctl, w.job_id, "canceled") == "canceled" and node_status(w) == "CANCELED"


def test_a_job_cancel_leaves_a_final_attempt_unchanged(tmp_path, monkeypatch):
    """R1 I-1, re-review M3: ``JobStore.cancel`` flags a FAILED attempt while it holds its lock or while its node is
    unfinished (not yet classified, or in a retry backoff); the final attempt of a finished node is untouched."""
    w = world(tmp_path, monkeypatch, fail="engine exited with status 1")
    assert run(w).status == "FAILED" and not held_locks(w)
    assert w.store.classify_attempt(w.ctl, w.job_id, "remote_failed") == "remote_failed"
    assert node_status(w) == "FAILED"   # max_attempts=1: no retry, the node is finished
    w.store.jobs.cancel(w.job_id)
    job = w.store.jobs.get(w.job_id)
    assert (job["status"], job["cancel_requested"]) == ("FAILED", 0)


def test_reconcile_sends_the_cancel_to_a_worker_that_may_still_run(tmp_path, monkeypatch):
    """R1 I-1: only ``_await_result`` wrote the remote ``cancel`` marker, so a canceled attempt whose response was
    lost trained on to its own deadline. While there is no result, reconcile of a canceled attempt writes it."""
    w = world(tmp_path, monkeypatch)
    w.host.mode = "lose_start_response"
    assert run(w).uncertain
    marker = remote_launch(w) / "cancel"
    assert w.runner.reconcile(w.job_id, w.executor) is None and not marker.exists()   # no cancel: no marker
    w.store.cancel(w.run["id"])
    assert w.runner.reconcile(w.job_id, w.executor) is None and marker.is_file()
    assert w.runner.reconcile(w.job_id, w.executor) is None and held_locks(w)   # an existing marker is the same signal
    w.host.finish()   # the worker sees the marker before it trains
    outcome = w.runner.reconcile(w.job_id, w.executor)
    assert (outcome.status, outcome.outputs) == ("CANCELED", None) and not held_locks(w)
    assert w.calls == [] and len(w.host.launches) == 1
    assert json.loads((remote_launch(w) / "result.json").read_bytes())["status"] == "CANCELED"


def test_a_transient_download_failure_after_completion_stays_uncertain(tmp_path, monkeypatch):
    """R1 I-3: one ``sftp.get`` OSError after the host reported COMPLETED made the paid attempt a terminal
    ``failed_before_submit``. It is uncertain now: the lock is kept and reconcile collects and freezes the same
    launch, with one launch and one training call."""
    w = world(tmp_path, monkeypatch)
    original, failures = w.host.sftp.get, []

    def get(source, target):
        if f"/jobs/{w.job_id}/" in source and not failures:
            failures.append(source)
            raise OSError("synthetic: connection reset during the download")
        return original(source, target)

    monkeypatch.setattr(w.host.sftp, "get", get)
    outcome = run(w)
    assert (outcome.status, outcome.uncertain, outcome.outputs) == ("FAILED", True, None) and failures
    assert w.store.jobs.get(w.job_id)["status"] == "FAILED" and held_locks(w)
    assert w.runner.failure_kind(w.job_id) == "uncertain"
    outcome = w.runner.reconcile(w.job_id, w.executor)
    assert (outcome.status, outcome.uncertain) == ("COMPLETED", False), outcome.error
    assert w.calls == ["train.bound.yaml"] and len(w.host.launches) == 1 and not held_locks(w)
    assert w.store.artifacts.verify(outcome.outputs.model.model).producer_attempt_id == w.lease.attempt_id


def test_a_lasting_sharing_violation_while_freezing_stays_uncertain(tmp_path, monkeypatch):
    """R1 I-3: an antivirus holding the ``.freeze-*`` directory past the bounded rename retry (WinError 32, 11 times)
    made the accepted COMPLETED result a terminal ``output_invalid``. It is uncertain now, and reconcile re-freezes
    it (content-addressed: one artifact)."""
    from test_model_artifacts import sharing_violation

    from tuning_factory import model_artifacts
    w = world(tmp_path, monkeypatch)
    before, rename, refused = artifact_entries(w.store), Path.rename, []

    def busy(self, target):
        if self.name.startswith(".freeze-") and len(refused) < 11:
            refused.append(self.name)
            raise sharing_violation(32)
        return rename(self, target)

    monkeypatch.setattr(Path, "rename", busy)
    monkeypatch.setattr(model_artifacts, "RENAME_RETRY_SECONDS", 0)
    outcome = run(w)
    assert (outcome.status, outcome.uncertain, outcome.outputs) == ("FAILED", True, None)
    assert len(refused) == model_artifacts.RENAME_TRIES and held_locks(w)
    assert w.runner.failure_kind(w.job_id) == "uncertain" and artifact_entries(w.store) == before
    outcome = w.runner.reconcile(w.job_id, w.executor)
    assert (outcome.status, outcome.uncertain) == ("COMPLETED", False), outcome.error
    assert len(refused) == 11 and len(artifact_entries(w.store)) == len(before) + 1 and not held_locks(w)
    assert w.calls == ["train.bound.yaml"] and len(w.host.launches) == 1


def test_a_content_error_after_completion_stays_terminal(tmp_path, monkeypatch):
    """R1 I-3: only transient I/O is uncertain; a refused output (here an engine vocabulary change) stays final."""
    w = world(tmp_path, monkeypatch, tokenizer=changed_tokenizer("added_token"))
    outcome = run(w)
    assert (outcome.status, outcome.uncertain) == ("FAILED", False) and not held_locks(w)
    assert w.runner.failure_kind(w.job_id) == "output_invalid"


@pytest.mark.parametrize("price,gpus", [(0.21, 3), (0.11, 5)])
def test_a_pinned_total_is_compared_with_the_live_total(tmp_path, monkeypatch, price, gpus):
    """R1 I-4: the pinned total split per GPU and multiplied back rounds below itself (0.21/3*3 < 0.21), so the
    unchanged live price of an approved destination was refused before launch. Totals are compared directly."""
    assert price / gpus * gpus < price   # the rounding the split check tripped on
    facts = FACTS.model_copy(update={"gpu_count": gpus, "total_hourly_cost_usd": price})
    w = world(tmp_path, monkeypatch, graph_options={"facts": facts})
    w.executor.pod = {**POD, "gpu": {**POD["gpu"], "count": gpus}, "costPerHr": price}
    outcome = run(w)
    assert outcome.status == "COMPLETED", outcome.error
    assert w.calls == ["train.bound.yaml"] and len(w.host.launches) == 1


@pytest.mark.parametrize("change,message", [("price", "price exceeds approved resource budget"),
                                            ("count", "GPU count differs from live Pod"),
                                            ("type", "GPU type differs from live Pod")])
def test_a_live_pod_above_the_pinned_total_or_another_gpu_is_still_refused(tmp_path, monkeypatch, change, message):
    import math
    facts = FACTS.model_copy(update={"gpu_count": 3, "total_hourly_cost_usd": 0.21})
    w = world(tmp_path, monkeypatch, graph_options={"facts": facts})
    pod = {**POD, "gpu": {**POD["gpu"], "count": 3}, "costPerHr": 0.21}
    if change == "price":
        pod["costPerHr"] = math.nextafter(0.21, 1)   # the smallest real increase
    elif change == "count":
        pod["gpu"] = {**pod["gpu"], "count": 2}
    else:
        pod["gpu"] = {**pod["gpu"], "id": "H100", "displayName": "H100"}
    w.executor.pod = pod
    outcome = run(w)
    assert (outcome.status, outcome.uncertain) == ("FAILED", False) and message in outcome.error
    assert w.host.commands == [] and w.calls == [] and not held_locks(w)


# -- 16. Task 11.5 re-review: a cancel during the freeze, deterministic file errors -------------------------------

def cancel_scope(w, scope):
    if scope == "run":
        w.store.cancel(w.run["id"])
    else:
        w.store.jobs.cancel(w.job_id)


@pytest.mark.parametrize("path", ["launch", "reconcile"])
@pytest.mark.parametrize("scope", ["run", "job"])
def test_a_cancel_that_lands_during_the_freeze_is_never_lost(tmp_path, monkeypatch, path, scope):
    """Re-review M1: the cancel check ran before the freeze and the result was recorded after it, so a cancel landing
    in between ended COMPLETED. It is decided in the transaction that records the result (``transition`` from
    ``_launch``, ``reconcile_result`` from reconcile): CANCELED, no output to record, nothing seeded."""
    from tuning_factory import attempt_runner
    w = world(tmp_path, monkeypatch)
    original, fired = attempt_runner.freeze_operation_output, []

    def freeze(*args, **kwargs):
        if not fired:
            fired.append(scope)
            cancel_scope(w, scope)   # the cancel lands while the outputs are being frozen
        return original(*args, **kwargs)

    monkeypatch.setattr(attempt_runner, "freeze_operation_output", freeze)
    if path == "launch":
        outcome = run(w)
    else:
        w.host.mode = "lose_start_response"
        assert run(w).uncertain and not fired
        w.host.finish()
        outcome = w.runner.reconcile(w.job_id, w.executor)
    assert fired == [scope]
    assert (outcome.status, outcome.uncertain, outcome.outputs) == ("CANCELED", False, None)
    assert w.store.jobs.get(w.job_id)["status"] == "CANCELED" and not held_locks(w)
    with pytest.raises(ValueError, match="only for a completed attempt"):
        w.runner.outputs_for(w.job_id)   # so record_outputs can never mark the node COMPLETED
    assert not (launch_dir(w).parent / (w.claimed["launch_id"] + ".cache-seed.json")).exists()
    assert w.runner.failure_kind(w.job_id) == "canceled"
    assert w.store.classify_attempt(w.ctl, w.job_id, "canceled") == "canceled" and node_status(w) == "CANCELED"
    assert w.calls == ["train.bound.yaml"] and len(w.host.launches) == 1


def drop_from_inventory(directory, name):
    """A worker whose result lacks one output file (removed with its inventory entry, so the transfer verifies)."""
    (directory / name).unlink()
    inventory = json.loads((directory / "artifacts.json").read_bytes())
    del inventory[name]
    (directory / "artifacts.json").write_bytes(canonical(inventory))


@pytest.mark.parametrize("path,missing,kind", [("launch", "output/adapter_config.json", "output_invalid"),
                                               ("reconcile", "output/adapter_config.json", "output_invalid"),
                                               ("launch", "artifacts.json", "failed_before_submit")])
def test_a_missing_file_after_completion_is_final_not_uncertain(tmp_path, monkeypatch, path, missing, kind):
    """Re-review M2 (a): FileNotFoundError and its kin after the host finished are deterministic content errors, not
    transient ones. A missing output file (read by the freeze, from the launch or from reconcile) or a missing
    inventory (read by the collection) is final at once, as before 11.5, instead of holding the lock forever."""
    w = world(tmp_path, monkeypatch)
    if missing == "artifacts.json":
        w.host.after_worker = lambda directory: (directory / missing).unlink()
    else:
        w.host.after_worker = lambda directory: drop_from_inventory(directory, missing)
    if path == "launch":
        outcome = run(w)
    else:
        w.host.mode = "lose_start_response"
        assert run(w).uncertain
        w.host.finish()
        outcome = w.runner.reconcile(w.job_id, w.executor)
    assert (outcome.status, outcome.uncertain, outcome.outputs) == ("FAILED", False, None)
    assert "FileNotFoundError" in outcome.error and not held_locks(w)
    assert w.runner.failure_kind(w.job_id) == kind
    assert w.calls == ["train.bound.yaml"] and len(w.host.launches) == 1


def os_error(kind, winerror=None, filename=None):
    error = kind(2, "synthetic", filename) if filename else kind(2, "synthetic")
    if winerror is not None:
        error.winerror = winerror   # as Windows sets it; portable to Linux
    return error


@pytest.mark.parametrize("error,transient", [
    (os_error(FileNotFoundError), False), (os_error(NotADirectoryError), False), (os_error(IsADirectoryError), False),
    (os_error(FileExistsError), False), (os_error(FileNotFoundError, 2), False),
    (os_error(FileNotFoundError, 206), True), (os_error(FileNotFoundError, 53), True),
    (os_error(FileNotFoundError, 67), True), (os_error(FileNotFoundError, 3, "C:/" + "d" * 300), True),
    (os_error(FileNotFoundError, 3, "C:/short"), False), (os_error(PermissionError, 32), True),
    (OSError(28, "no space left on device"), True)])
def test_windows_path_errors_raised_as_file_not_found_stay_transient(monkeypatch, error, transient):
    """Final re-review item 1: Windows raises WinError 206 (path too long) and 53/67 (network path or name gone), and
    WinError 3 (path not found) for a path of MAX_PATH or more, as FileNotFoundError. They are local and fixable
    (OUTPUTS_NOT_SAVED names the path length), so both the freeze and the collection rule keep them uncertain. The
    length rule holds only while Windows enforces MAX_PATH (long paths off)."""
    from tuning_factory import execution
    monkeypatch.setattr(execution, "LONG_PATHS", False)
    assert api().local_transient(error) is transient
    assert execution.transient_error(error) is transient


@pytest.mark.parametrize("filename,parent_winerror,windows,transient", [
    ("C:/" + "d" * 300, None, True, True),      # open() of a path of MAX_PATH or more
    ("C:/share/f", 53, True, True),             # open() under a network share that is gone
    ("C:/share/f", 67, True, True),
    ("C:/state/f", None, True, False),          # the folder is there: the file itself is missing (content)
    ("C:/state/f", 3, True, False),             # the folder is missing on this PC (content)
    (None, None, True, False),                  # no path to judge
    ("C:/" + "d" * 300, None, False, False)])   # not Windows: a missing file stays content
def test_windows_open_errors_without_a_winerror_are_judged_by_their_path(monkeypatch, filename, parent_winerror,
                                                                          windows, transient):
    """Last re-review item 1: only ``os.*`` functions set ``winerror``. ``open()`` (paramiko's download, the freeze
    copy) raises a path too long or a gone network share as a bare FileNotFoundError, so on Windows the path decides:
    MAX_PATH or more, or a parent folder whose stat fails with WinError 53/67, is local and fixable."""
    from tuning_factory import execution

    def stat(path):
        if parent_winerror is not None:
            raise os_error(FileNotFoundError, parent_winerror, path)
        return None

    monkeypatch.setattr(execution, "WINDOWS", windows)
    monkeypatch.setattr(execution, "LONG_PATHS", False)
    monkeypatch.setattr(execution, "_stat", stat)
    error = os_error(FileNotFoundError, filename=filename)
    assert getattr(error, "winerror", None) is None
    assert api().local_transient(error) is transient
    assert execution.transient_error(error) is transient


LONG = "C:/" + "d" * 300


def unstattable(path):
    raise ValueError("embedded null character in path")


@pytest.mark.parametrize("error,long_paths,stat", [
    (os_error(FileNotFoundError, filename=LONG), True, None),       # long paths on: open() could reach that path
    (os_error(FileNotFoundError, 3, LONG), True, None),
    (os_error(FileNotFoundError, filename="\\\\?\\" + LONG), False, None),   # \\?\ is never held to MAX_PATH
    (os_error(FileExistsError, filename=LONG), False, None),        # the path was usable: the name exists
    (os_error(FileNotFoundError, filename=b"C:/\xff\xfe/f"), False, None),   # undecodable bytes: judged, not raised
    (os_error(FileNotFoundError, filename="C:/state/f"), False, unstattable)])
def test_a_long_or_odd_path_that_open_could_reach_stays_content(monkeypatch, error, long_paths, stat):
    """Last review: with long paths enabled (python.exe opts in, and the "Disable path length limit" setting turns them
    on machine-wide) a bare FileNotFoundError at 260+ characters means the file is really missing. A ``\\\\?\\`` name
    and a FileExistsError show the path was usable. The judgement never raises inside the runner's handlers."""
    from tuning_factory import execution
    monkeypatch.setattr(execution, "WINDOWS", True)
    monkeypatch.setattr(execution, "LONG_PATHS", long_paths)
    monkeypatch.setattr(execution, "_stat", stat or (lambda path: None))
    assert api().local_transient(error) is False
    assert execution.transient_error(error) is False


def test_base_snapshot_carries_processor_files_beside_config_and_tokenizer(tmp_path):
    from tuning_factory.operation_dispatch import BASE_SNAPSHOT
    base, directory = tmp_path / "base", tmp_path / "launch"
    base.mkdir()
    directory.mkdir()
    for name in ("config.json", "tokenizer.json", "tokenizer_config.json", "preprocessor_config.json",
                 "model.safetensors"):
        (base / name).write_text("{}")
    names = operation_dispatch._write_base_snapshot(directory, base)
    assert sorted(names) == [f"{BASE_SNAPSHOT}/{name}" for name in (
        "config.json", "preprocessor_config.json", "tokenizer.json", "tokenizer_config.json")]
