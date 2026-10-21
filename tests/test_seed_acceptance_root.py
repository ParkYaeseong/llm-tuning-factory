"""Task 11.2 (plan §4 "11.2", D15): the synthetic seeded root ``factory-acceptance/tiny-qwen2`` for offline acceptance.

``scripts/seed_acceptance_root.py`` writes a deterministic tiny Qwen2 root (config, a WordLevel tokenizer and the
module evidence of a synthetic safetensors header) into a state directory through the public functions that
``model_root_snapshot`` and ``model_root_targets`` use, without any download. These tests drive it through the
workflow channel (``desktop_workflows.dispatch_action``/``handle``) and the production controller wiring
(``controller_entry.build_controller``), as the app does. The channel helpers here are shared with
``tests/test_acceptance_workflows.py``.

Main env only: no GPU, SSH, network, model download, MLflow write or provider call. A module-wide socket guard
records and refuses every connection; subprocesses get a ``sitecustomize`` guard.
"""
import hashlib
import importlib.util
import json
import os
import socket
import subprocess
import sys
import textwrap
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import test_model_inputs as inputs

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "seed_acceptance_root.py"
SRC = ROOT / "src"
WAIT = 120          # seconds: the wall-clock bound of every controller drive
# The test_controller_entry FAST settings: short leases and ticks keep a drive within a few seconds.
FAST = {"lease_ttl_seconds": 6, "tick_seconds": 0.2, "lease_heartbeat_seconds": 1.5}
HEAVY = ("torch", "peft", "transformers", "safetensors", "huggingface_hub", "keyring")
# Written by hand (not computed by the script): the 14 linear modules of a 2-layer Qwen2 with hidden size 16,
# intermediate size 32 and 2 query/2 key-value heads of dimension 8, as {module: (out, in)}.
LINEAR = {"self_attn.q_proj": (16, 16), "self_attn.k_proj": (16, 16), "self_attn.v_proj": (16, 16),
          "self_attn.o_proj": (16, 16), "mlp.gate_proj": (32, 16), "mlp.up_proj": (32, 16),
          "mlp.down_proj": (16, 32)}
EXPECTED_MODULES = {f"model.layers.{index}.{name}": shape for index in (0, 1) for name, shape in LINEAR.items()}
GUARD = textwrap.dedent('''\
    """Seed script test guard (sitecustomize): this child process may not open a network connection."""
    import socket


    def _refuse(*args, **kwargs):
        raise OSError("network disabled in seed script tests")


    socket.socket.connect = _refuse
    socket.socket.connect_ex = _refuse
''')


def seeder():
    """The seed script as a module. A missing script is a test failure, rather than a collection error."""
    if not SCRIPT.is_file():
        pytest.fail("scripts/seed_acceptance_root.py (Task 11.2) is missing")
    specification = importlib.util.spec_from_file_location("seed_acceptance_root", SCRIPT)
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    for name in ("REPO_ID", "REVISION", "CONFIG", "TOKENIZER", "seed", "write_root", "main"):
        if not hasattr(module, name):
            pytest.fail(f"seed_acceptance_root.{name} (Task 11.2) is missing")
    return module


@pytest.fixture(autouse=True)
def connects(monkeypatch):
    """Every socket connect is recorded and refused: seeding, planning and simulated runs are offline."""
    seen = []

    def refuse(*args, **kwargs):
        seen.append(args[1:])
        raise AssertionError("acceptance tests must not open a network connection")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    return seen


# -- the workflow channel ------------------------------------------------------------------------

def action(state, name, data=None, *, settings=None, profiles=(), clock=None):
    """One workflow action exactly as the channel dispatches it (``test_controller_entry.action``)."""
    from tuning_factory import desktop_workflows as wf
    context = wf.WorkflowContext(
        state_dir=Path(state), settings=dict(settings or {}),
        profiles={item["profile_id"]: wf.NativeProfile.model_validate(item) for item in profiles},
        clock=clock or time.time)
    return wf.dispatch_action(context, name, {} if data is None else data).model_dump(mode="json")


def envelope(state, name, data=None):
    """One request through ``desktop_workflows.handle``, the envelope the native side sends (empty settings)."""
    from tuning_factory import desktop_workflows as wf
    return wf.handle({"channel": "workflow", "root": str(state), "settings": {}, "profiles": [], "secrets": {},
                      "action": name, "data": {} if data is None else data})


def accepted(result):
    assert result["status"] == "ok", result
    return result["data"]


def codes(result):
    return [item["code"] for item in result["issues"]]


def scanned(directory):
    directory = Path(directory)
    return {path.relative_to(directory).as_posix(): path.read_bytes()
            for path in sorted(directory.rglob("*")) if path.is_file()}


def csv_bytes(rows, encoding="utf-8"):
    return ("question,context,answer\n" + "".join(f"{q},{c},{a}\n" for q, c, a in rows)).encode(encoding)


def qa_rows(count=12, prefix="질문"):
    return [(f"{prefix} {index}", f"문맥 {index}", f"답 {index}") for index in range(1, count + 1)]


MAPPING = {"source_format": "csv", "fields": {"instruction": ["question"], "input": ["context"],
                                              "output": ["answer"]}}


def import_dataset(state, tmp_path, raw, *, encoding="utf-8", dataset_id="qa-source", name="qa.csv"):
    """``dataset_import_source`` -> ``dataset_import_preview`` -> ``dataset_import_register`` (synthetic, never
    exported); the preview and the registered reference."""
    inbox = Path(tmp_path) / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / name).write_bytes(raw)
    source = accepted(action(state, "dataset_import_source", {"path": str(inbox / name)}))
    mapping = {**MAPPING, "encoding": encoding}
    preview = accepted(action(state, "dataset_import_preview", {"source_id": source["source_id"],
                                                                "mapping": mapping}))
    registered = accepted(action(state, "dataset_import_register", {
        "source_id": source["source_id"], "mapping": mapping, "preview_sha256": preview["preview_sha256"],
        "dataset_id": dataset_id, "version": "1", "synthetic": True, "export_allowed": False}))
    return preview, registered["dataset"]


def split_dataset(state, dataset, prefix="qa"):
    """An 80/10/10 split with seed 42 (``dataset_split_preview`` then ``dataset_split_commit``)."""
    plan = {"dataset": dataset, "seed": 42, "train_basis_points": 8000, "validation_basis_points": 1000,
            "test_basis_points": 1000,
            "outputs": {role: {"dataset_id": f"{prefix}-{role}", "version": "1"}
                        for role in ("train", "validation", "test")}}
    preview = accepted(action(state, "dataset_split_preview", {"plan": plan}))
    committed = accepted(action(state, "dataset_split_commit", {"plan_sha256": preview["plan_sha256"]}))
    assert committed["outputs"] == preview["outputs"]
    return preview, committed["outputs"]


def bindings(outputs):
    return [{"binding_id": role, "role": role, "dataset": outputs[role]} for role in ("train", "validation", "test")]


def save_recipe(state, recipe_id="qa-sft-lora", *, hyperparameters=None, **spec):
    """An SFT LoRA recipe. ``cutoff_len`` is 64: the seeded root's reviewed limit (its ``max_position_embeddings``)."""
    body = {"recipe_id": recipe_id, "name": "질의응답 SFT LoRA", "training_stage": "sft", "training_method": "lora",
            "hyperparameters": {"cutoff_len": 64, **(hyperparameters or {})}, **spec}
    return accepted(action(state, "recipe_save", {"spec": body, "expected_revision": None}))["revision_sha256"]


def save_benchmark(state, dataset, root, rows):
    """A text benchmark over every row of ``dataset`` (the validation split), on the root's tokenizer."""
    spec = {"dataset": dataset, "evaluator_kind": "text", "evaluator_version": "1",
            "ordered_sample_ids": [f"row:{index}" for index in range(rows)], "max_new_tokens": 16, "cutoff_len": 64,
            "seed": 42, "precision": "bf16", "quantization_bit": None,
            "tokenizer_fingerprint": root["tokenizer_fingerprint"], "template": "qwen",
            "semantic_model_kind": "causal_lm"}
    return accepted(action(state, "benchmark_save", {"spec": spec}))["revision_sha256"]


def from_root(root, initialization="new_adapter"):
    return {"policy": {"base": root}, "initialization": initialization}


def output(node_id, name="model"):
    return {"node_id": node_id, "output_name": name}


def train(node_id, recipe, model_input, profiles):
    return {"kind": "train", "id": node_id, "label": f"{node_id} 학습", "recipe_revision": recipe,
            "model_input": model_input, "dataset_binding": "train", "executor_profile_ids": list(profiles)}


def evaluate(node_id, model, benchmark, profiles):
    return {"kind": "evaluate", "id": node_id, "label": f"{node_id} 평가", "model": model,
            "benchmark_revision": benchmark, "executor_profile_ids": list(profiles)}


def select(node_id, evaluations):
    return {"kind": "select", "id": node_id, "label": "선택",
            "evaluations": [output(item, "evaluation") for item in evaluations],
            "selection": {"metric": "exact_match", "direction": "max"}}


def merge(node_id, models, profiles, weights=(0.5, 0.5)):
    return {"kind": "merge", "id": node_id, "label": "병합", "models": list(models), "weights": list(weights),
            "executor_profile_ids": list(profiles)}


def estimate(profile_id, price=0.5):
    return {"profile_id": profile_id, "gpu_type": "L4", "gpu_count": 1, "total_hourly_cost_usd": price}


def plan_request(project_id, nodes, estimates, simulation=True):
    return {"project_id": project_id, "simulation": simulation,
            "node_resources": [{"node_id": node["id"], "timeout_seconds": 600} for node in nodes
                               if node["kind"] != "select"],
            "estimates": list(estimates)}


def workbench(state, tmp_path, *, name="질의응답 실험 01", raw=None, encoding="utf-8", recipe=None):
    """A blank project with an imported synthetic CSV split 80/10/10 and bound, an SFT LoRA recipe, the seeded root
    and a validation text benchmark, all through the channel. ``project`` is the latest saved project body."""
    created = accepted(action(state, "project_create", {"request_id": "acceptance-" + hashlib.sha1(
        str(state).encode()).hexdigest()[:12], "name": name, "purpose": "문맥으로 답하기", "task_type": "qa"}))
    _, dataset = import_dataset(state, tmp_path, raw or csv_bytes(qa_rows()), encoding=encoding)
    split, outputs = split_dataset(state, dataset)
    saved = accepted(action(state, "project_save", {"spec": {**created["project"], "datasets": bindings(outputs)},
                                                    "expected_revision": created["revision_sha256"]}))
    recipe_revision = save_recipe(state, **(recipe or {}))
    root = seeder().seed(state)["root"]
    benchmark = save_benchmark(state, outputs["validation"], root, split["counts"]["validation"])
    return SimpleNamespace(state=Path(state), project=saved["project"], revision=saved["revision_sha256"],
                           recipe=recipe_revision, root=root, benchmark=benchmark, outputs=outputs,
                           counts=split["counts"])


def save_graph(bench, nodes, **graph):
    """``project_save`` of the workbench project with this graph; updates the workbench's revision."""
    body = {**bench.project, "graph": {"nodes": list(nodes), **graph}}
    saved = accepted(action(bench.state, "project_save", {"spec": body, "expected_revision": bench.revision}))
    bench.project, bench.revision = saved["project"], saved["revision_sha256"]
    return saved


def planned(bench, nodes, estimates, *, clock=None, **graph):
    """Save the graph, validate it and make a simulation plan; the plan summary."""
    save_graph(bench, nodes, **graph)
    accepted(action(bench.state, "graph_validate", {"project_id": bench.project["project_id"]}))
    return accepted(action(bench.state, "graph_plan", plan_request(bench.project["project_id"], nodes, estimates),
                           clock=clock))


def submitted(state, plan_sha256, request_id="acceptance-run"):
    data = accepted(action(state, "graph_submit", {"plan_sha256": plan_sha256, "request_id": request_id}))
    assert data["simulation"] is True and data["status"] == "RUNNING", data
    return data["run_id"]


# -- the production controller ------------------------------------------------------------------

def start_message(state, owner_id, settings=None):
    return {"schema": "factory-controller/1", "state_root": str(state), "owner_id": owner_id, "tracking": {},
            "profiles": [], "settings": dict(FAST if settings is None else settings)}


def controller_start(state, owner_id="acceptance.1", settings=None):
    from tuning_factory import controller_entry as ce
    start = ce.ControllerStart.model_validate(start_message(state, owner_id, settings))
    assert start.tracking == ce.TrackingScope() and start.profiles == ()
    return start


def run_view(state, run_id):
    return accepted(action(state, "graph_run_get", {"run_id": run_id}))["run"]


def node_view(run, node_id):
    return next(item for item in run["nodes"] if item["node_id"] == node_id)


def drive(state, run_id, *, owner_id="acceptance.1", each=None, until=None):
    """``build_controller`` (blank tracking, no profiles, FAST settings), the owner lease, then ticks until the run
    is final or ``until(run)`` holds, within ``WAIT`` wall seconds. ``each(run)`` sees every polled view."""
    from tuning_factory import controller_entry as ce
    controller, info = ce.build_controller(controller_start(state, owner_id))
    assert controller.tracker is None and info["tracking_settings_sha256"] is None and info["profiles"] == {}
    owner = controller.acquire_controller(owner_id)
    deadline = time.monotonic() + WAIT
    try:
        while True:
            controller.tick(owner)
            run = run_view(state, run_id)
            if each is not None:
                each(run)
            if (until(run) if until else run["status"] in ("COMPLETED", "FAILED", "CANCELED")
                    and run["tracking"]["events"].get("PENDING", 0) == 0):
                return controller, run
            assert time.monotonic() < deadline, f"the run did not settle in {WAIT} s: {run}"
            time.sleep(0.05)
    finally:
        controller.pool.shutdown(wait=True)   # every dispatch thread has returned before the files are read
        controller.store.release_owner(owner)


# -- 1 -------------------------------------------------------------------------------------------

def test_the_seeded_root_is_indexed_with_targets_and_is_idempotent(tmp_path, connects):
    sr = seeder()
    from tuning_factory.model_artifacts import tokenizer_fingerprint
    from tuning_factory.workflow_planning import load_root, load_root_targets, root_snapshots_dir
    state = tmp_path / "state"
    assert sr.REPO_ID == "factory-acceptance/tiny-qwen2"
    assert sr.REVISION == hashlib.sha1(b"factory-acceptance/tiny-qwen2@1").hexdigest()
    assert sr.CONFIG == {"architectures": ["Qwen2ForCausalLM"], "model_type": "qwen2", "hidden_size": 16,
                         "intermediate_size": 32, "num_hidden_layers": 2, "num_attention_heads": 2,
                         "num_key_value_heads": 2, "vocab_size": 32, "max_position_embeddings": 64,
                         "torch_dtype": "bfloat16", "tie_word_embeddings": False}
    first = sr.seed(state)
    written = scanned(state)
    assert sr.seed(state) == first and scanned(state) == written   # identical bytes: idempotent
    assert first == {"root": first["root"], "modules": 14}
    root = first["root"]
    assert (root["repo_id"], root["revision"]) == (sr.REPO_ID, sr.REVISION)
    snapshot = root_snapshots_dir(state) / "factory-acceptance" / "tiny-qwen2" / sr.REVISION
    files = scanned(snapshot)
    # config.json and the WordLevel tokenizer of test_model_inputs.write_snapshot, as canonical LF-free JSON bytes.
    assert files == {"config.json": files["config.json"], **inputs.TOKENIZER}
    assert json.loads(files["config.json"]) == sr.CONFIG and b"\r" not in b"".join(files.values())
    assert root["config_sha256"] == hashlib.sha256(files["config.json"]).hexdigest()
    # The fingerprint is recomputed here from the written files (independently of the script).
    assert root["tokenizer_fingerprint"] == inputs.fingerprint() == tokenizer_fingerprint(snapshot)
    assert not any(name.endswith((".safetensors", ".bin")) for name in written)   # no weights, never downloaded
    entry = load_root(state, root)
    assert entry.profile.architecture == "qwen2" and entry.profile.template == "qwen"
    assert (entry.profile.repo_id, entry.profile.revision, entry.profile.config_sha256) == (
        sr.REPO_ID, sr.REVISION, root["config_sha256"])
    evidence = load_root_targets(state, root)
    assert {module.name: (module.output_dimension, module.input_dimension)
            for module in evidence.modules} == EXPECTED_MODULES
    # Through the channel, as the app lists roots (and once through the native envelope).
    for listed in (action(state, "model_roots_list"), envelope(state, "model_roots_list")):
        [item] = accepted(listed)["roots"]
        assert item["root"] == root and item["snapshot_ready"] is True and item["targets_ready"] is True
        assert item["profile"] == entry.profile.model_dump(mode="json")
    assert connects == []


# -- 2 -------------------------------------------------------------------------------------------

def test_the_seeded_root_plans_and_runs_a_simulated_train(tmp_path, connects):
    state = tmp_path / "state"
    bench = workbench(state, tmp_path, recipe={"hyperparameters": {"lora_target": "all"}})
    nodes = [train("train", bench.recipe, from_root(bench.root), ["estimate-a"]),
             evaluate("evaluate", output("train"), bench.benchmark, ["estimate-a"])]
    plan = planned(bench, nodes, [estimate("estimate-a")])
    assert "warnings" not in plan   # no simulated_execution_needs_targets: the seeded evidence lifts it
    assert plan["simulation"] is True and {item["synthetic"] for item in plan["datasets"]} == {True}
    run_id = submitted(state, plan["plan_sha256"])
    _, run = drive(state, run_id)
    assert run["status"] == "COMPLETED", run
    assert {item["node_id"]: item["status"] for item in run["nodes"]} == {"train": "COMPLETED",
                                                                          "evaluate": "COMPLETED"}
    from tuning_factory.graph_store import GraphStore
    store = GraphStore(state / "jobs.db")
    job = store.jobs.get(node_view(run, "train")["job_id"])
    with store.jobs.connection() as db:
        row = db.execute("SELECT outputs FROM node_outputs WHERE attempt_id=?", (job["active_attempt_id"],)).fetchone()
    model = json.loads(row[0])["model"]
    from tuning_factory.model_artifacts import ModelArtifactRef
    descriptor = store.artifacts.verify(ModelArtifactRef.model_validate(model["model"]))
    assert descriptor.simulation and descriptor.synthetic and not descriptor.eligible_for_real_use
    assert descriptor.effective_base.model_dump(mode="json") == bench.root
    assert sorted(descriptor.adapter.target_modules) == sorted({name.rsplit(".", 1)[-1] for name in LINEAR})
    assert connects == []


# -- 3 -------------------------------------------------------------------------------------------

def test_the_cli_prints_one_json_line_offline_and_imports_nothing_heavy(tmp_path):
    """The stable interface 11.3's frozen verifier calls: ``--state-dir`` only, one JSON line, exit 0."""
    sr = seeder()
    guard = tmp_path / "guard"
    guard.mkdir()
    (guard / "sitecustomize.py").write_text(GUARD, encoding="utf-8")
    env = {key: value for key, value in os.environ.items() if not key.startswith(("PYTHON", "MLFLOW_", "HF_"))}
    env["PYTHONPATH"] = os.pathsep.join([str(guard), str(SRC)])
    state = tmp_path / "state"
    process = subprocess.run([sys.executable, str(SCRIPT), "--state-dir", str(state)], capture_output=True,
                             env=env, cwd=tmp_path, timeout=WAIT)
    assert process.returncode == 0, process.stderr
    [line] = process.stdout.decode("utf-8").splitlines()
    assert json.loads(line) == sr.seed(tmp_path / "in-process")
    assert b"network disabled" not in process.stderr
    probe = textwrap.dedent("""
        import json, runpy, sys
        sys.argv = [sys.argv[1], "--state-dir", sys.argv[2]]
        try:
            runpy.run_path(sys.argv[0], run_name="__main__")
        except SystemExit as exc:
            assert exc.code in (0, None), exc.code
        print(json.dumps(sorted(name for name in %r if name in sys.modules)))
    """ % (HEAVY,))
    again = subprocess.run([sys.executable, "-c", probe, str(SCRIPT), str(state)], capture_output=True, env=env,
                           cwd=tmp_path, timeout=WAIT)
    assert again.returncode == 0, again.stderr
    output_lines = again.stdout.decode("utf-8").splitlines()
    assert json.loads(output_lines[0]) == json.loads(line)   # a second run over the same state: the same line
    assert json.loads(output_lines[-1]) == []
    for argv in ([], ["--state-dir"], ["--bogus", str(state)]):
        refused = subprocess.run([sys.executable, str(SCRIPT), *argv], capture_output=True, env=env, cwd=tmp_path,
                                 timeout=WAIT)
        assert refused.returncode == 2 and refused.stdout == b"", argv
