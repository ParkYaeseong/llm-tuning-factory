"""Task 11.2 (plan §4 "11.2"; D13, D14, D15): cross-task acceptance through the production wiring.

The Python layer of D14. Every case drives the app's own entry points, never a test-only wiring:

- the workflow channel ``desktop_workflows.dispatch_action`` (and ``handle``, the native envelope);
- ``controller_entry.build_controller`` (the ``GraphController`` with its ``DispatchRouter`` to the
  ``SimulatedOperationDispatcher``, the ``SimulatedExecutorProvider`` and the ``TrackingOutbox`` with its local sink)
  and ``controller_entry.serve`` on a thread;
- the synthetic seeded root of ``scripts/seed_acceptance_root.py`` (D15) for every offline plan.

Fakes only at true external boundaries: A5's real run uses ``test_operation_runner``'s fake GPU host (SSH) and the
fake MLflow client of ``test_tracking_outbox`` behind the real tracker. A3 times its stop and cancels with spies
that call through (the real ``JobStore.begin_launch`` and ``GraphStore.complete_local_selection`` still run), so the
stop or cancel lands at a chosen point instead of a race. Wall-clock waits are bounded by ``WAIT``.

A module-wide socket guard refuses every connection; a real MLflow client may never be constructed. No GPU, SSH,
network, model download, MLflow write or provider call.
"""
import builtins
import io
import json
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import pytest
from test_seed_acceptance_root import (
    MAPPING,
    WAIT,
    accepted,
    action,
    bindings,
    codes,
    controller_start,
    csv_bytes,
    drive,
    envelope,
    estimate,
    evaluate,
    from_root,
    merge,
    node_view,
    output,
    plan_request,
    planned,
    qa_rows,
    run_view,
    save_graph,
    save_recipe,
    scanned,
    seeder,
    select,
    submitted,
    train,
    workbench,
)

ROOT = Path(__file__).resolve().parents[1]
NOW = 1_800_000_000.0                       # the planning clock of A1: identical requests, identical plans
SIMULATED = "simulation:existing_runpod:"   # a simulation claim's resource key for an estimate destination
FINAL = ("COMPLETED", "FAILED", "CANCELED")
UNFINISHED = ("PENDING", "READY", "RUNNING")
CATALOG_TEMPLATES = (*sorted(path.stem for path in (ROOT / "configs" / "ai").glob("*.yaml")), "tutorial_sample")


@pytest.fixture(autouse=True)
def connects(monkeypatch):
    """Every socket connect is recorded and refused."""
    seen = []

    def refuse(*args, **kwargs):
        seen.append(args[1:])
        raise AssertionError("acceptance tests must not open a network connection")

    monkeypatch.setattr("socket.socket.connect", refuse)
    monkeypatch.setattr("socket.socket.connect_ex", refuse)
    return seen


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch):
    """``serve`` applies the controller's tracking scope to the process environment: every credential variable is
    restored afterwards, and no OIDC refresher thread starts."""
    from tuning_factory.controller_entry import CREDENTIAL_ENV
    for name in (*CREDENTIAL_ENV, "MLFLOW_HTTP_REQUEST_MAX_RETRIES", "MLFLOW_HTTP_REQUEST_TIMEOUT",
                 "MLFLOW_SUPPRESS_PRINTING_URL_TO_STDOUT"):
        monkeypatch.setenv(name, "x")   # registers the teardown even for variables that start absent
        monkeypatch.delenv(name)
    monkeypatch.setattr("tuning_factory.auth.configure_oidc", lambda: None)


@pytest.fixture(autouse=True)
def mlflow_clients(monkeypatch):
    """A blank tracking URI never builds an MLflow client: any construction is recorded and refused."""
    built = []

    def refuse(*args, **kwargs):
        built.append(kwargs.get("tracking_uri"))
        raise AssertionError("a real MLflow client was constructed")

    monkeypatch.setattr("mlflow.MlflowClient", refuse)
    return built


# -- helpers -------------------------------------------------------------------------------------

def diamond(bench, two_destinations):
    """D13's mock diamond without a merge: train-a -> (train-b continues a, train-c a new adapter on the root after
    a) -> (eval-b, eval-c) -> select. Two destinations: train-a, train-b and eval-b on estimate-a, train-c and eval-c
    on estimate-b; one destination: every GPU node on estimate-a."""
    second = "estimate-b" if two_destinations else "estimate-a"
    nodes = [train("train-a", bench.recipe, from_root(bench.root), ["estimate-a"]),
             train("train-b", bench.recipe, output("train-a"), ["estimate-a"]),
             train("train-c", bench.recipe, from_root(bench.root), [second]),
             evaluate("eval-b", output("train-b"), bench.benchmark, ["estimate-a"]),
             evaluate("eval-c", output("train-c"), bench.benchmark, [second]),
             select("select", ("eval-b", "eval-c"))]
    pinned = {node["id"]: node["executor_profile_ids"][0] for node in nodes if node["kind"] != "select"}
    estimates = [estimate("estimate-a"), *([estimate("estimate-b")] if two_destinations else [])]
    graph = {"depends_on": [{"node_id": "train-c", "depends_on": "train-a"}], "max_concurrent": 2}
    return nodes, estimates, pinned, graph


def planned_diamond(state, tmp_path, two_destinations):
    bench = workbench(state, tmp_path)
    nodes, estimates, pinned, graph = diamond(bench, two_destinations)
    plan = planned(bench, nodes, estimates, **graph)
    assert "warnings" not in plan and plan["simulation"] is True, plan
    return bench, submitted(state, plan["plan_sha256"]), pinned


def ledger(state):
    from tuning_factory.graph_store import GraphStore
    return GraphStore(Path(state) / "jobs.db")


def attempts(state, run_id):
    """Every attempt of the run's jobs, oldest first: node, attempt, resource key and whether it launched."""
    with ledger(state).jobs.connection() as db:
        rows = db.execute("SELECT j.graph_node_id,a.attempt_id,a.resource_id,a.launch_started FROM job_attempts a "
                          "JOIN jobs j ON j.id=a.job_id WHERE j.graph_run_id=? ORDER BY a.created,a.rowid",
                          (run_id,)).fetchall()
    return [dict(row) for row in rows]


def launched(state, run_id):
    return {row["attempt_id"] for row in attempts(state, run_id) if row["launch_started"]}


def selection_receipt(state, run_id, node_id="select"):
    with ledger(state).jobs.connection() as db:
        row = db.execute("SELECT outputs FROM node_outputs WHERE run_id=? AND node_id=?", (run_id, node_id)).fetchone()
    return None if row is None else json.loads(row[0])["selection"]


def tracking_events(state, run_id):
    with ledger(state).jobs.connection() as db:
        return [dict(row) for row in db.execute("SELECT kind,status,node_id FROM tracking_events WHERE run_id=?",
                                                (run_id,))]


def statuses(run):
    return {item["node_id"]: item["status"] for item in run["nodes"]}


def settled(run):
    return run["status"] in FINAL and run["tracking"]["events"].get("PENDING", 0) == 0 and not any(
        item["status"] in UNFINISHED for item in run["nodes"])


def wait_run(state, run_id, until=settled, served=None):
    deadline = time.monotonic() + WAIT
    while True:
        run = run_view(state, run_id)
        if until(run):
            return run
        assert time.monotonic() < deadline, f"the run did not reach the expected state in {WAIT} s: {run}"
        if served is not None:
            assert served.thread.is_alive(), f"serve returned {served.result} before the run settled: {run}"
        time.sleep(0.05)


def current_json(state):
    return json.loads((Path(state) / "controller" / "current.json").read_bytes())


class Served:
    """``controller_entry.serve`` on a thread with the production start (blank tracking, no profiles, the FAST
    settings), stopped by its stop event or a stop request."""

    def __init__(self, state, owner_id):
        from tuning_factory import controller_entry as ce
        self.state, self.owner_id, self.stop, self.result = Path(state), owner_id, threading.Event(), []
        start = controller_start(state, owner_id)
        self.thread = threading.Thread(target=lambda: self.result.append(ce.serve(start, stop_event=self.stop)),
                                       name=f"serve-{owner_id}", daemon=True)
        self.thread.start()

    def running(self):
        """``controller_status`` (channel) once this owner runs and wrote its ``current.json``."""
        deadline = time.monotonic() + WAIT
        while True:
            data = accepted(action(self.state, "controller_status"))
            current = data["current"] or {}
            if data["controller"]["state"] == "running" and current.get("owner_id") == self.owner_id:
                return data
            assert self.thread.is_alive(), f"serve returned {self.result}"
            assert time.monotonic() < deadline, f"{self.owner_id} did not start in {WAIT} s: {data}"
            time.sleep(0.05)

    def join(self):
        self.thread.join(WAIT)
        assert not self.thread.is_alive(), f"serve {self.owner_id} did not return in {WAIT} s"
        return self.result[0]

    def close(self):
        self.stop.set()
        self.thread.join(WAIT)


@contextmanager
def spy(monkeypatch, owner, name, before):
    """Wrap ``owner.name`` so ``before(self, *args)`` runs first, once, when it returns true; then the real one."""
    real, lock, fired = getattr(owner, name), threading.Lock(), []

    def wrapper(self, *args, **kwargs):
        with lock:
            if not fired and before(self, *args):
                fired.append(True)
        return real(self, *args, **kwargs)

    monkeypatch.setattr(owner, name, wrapper)
    yield fired


def frozen_artifacts(state):
    root = ledger(state).artifacts.root
    return sorted(f"{entry.parent.name}/{entry.name}" for entry in root.glob("*/*")
                  if entry.is_dir() and not entry.parent.name.startswith("."))


# -- A1 ------------------------------------------------------------------------------------------

@pytest.mark.parametrize("encoding", ["utf-8", "cp949"], ids=["utf8", "cp949"])
def test_a_blank_purpose_and_csv_reach_an_immutable_dry_plan_without_examples(tmp_path, monkeypatch, connects,
                                                                               encoding):
    blocked = [(ROOT / "configs" / "ai").resolve(), (ROOT / "examples").resolve()]
    opened, real_open = [], io.open

    def guarded(file, *args, **kwargs):
        if isinstance(file, (str, os.PathLike)):
            path = Path(os.fspath(file)).resolve()
            if any(path == root or path.is_relative_to(root) for root in blocked):
                opened.append(path)
                raise AssertionError(f"a blank project must not read bundled examples: {path}")
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(io, "open", guarded)
    monkeypatch.setattr(builtins, "open", guarded)
    state, sent = tmp_path / "state", []

    def call(name, data=None, clock=None):
        sent.append({"action": name, "data": data})
        return action(state, name, data, clock=clock)

    created = accepted(call("project_create", {"request_id": f"blank-{encoding}", "name": "질의응답 실험 01",
                                               "purpose": "문맥으로 답하기", "task_type": "qa"}))
    project = created["project"]
    assert (project["name"], project["purpose"], project["task_type"]) == ("질의응답 실험 01", "문맥으로 답하기", "qa")
    assert project["legacy_origin"] is None and project["datasets"] == []
    rows = qa_rows(12)
    raw = csv_bytes(rows, encoding)
    assert (raw == csv_bytes(rows)) is (encoding == "utf-8")
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "qa.csv").write_bytes(raw)
    source = accepted(call("dataset_import_source", {"path": str(inbox / "qa.csv")}))
    assert source["size"] == len(raw)
    if encoding == "cp949":   # the encoding is taken as written, never guessed
        misread = call("dataset_import_preview", {"source_id": source["source_id"], "mapping": MAPPING})
        assert misread["status"] == "invalid" and "invalid_source" in codes(misread), misread
    mapping = {**MAPPING, "encoding": encoding}
    preview = accepted(call("dataset_import_preview", {"source_id": source["source_id"], "mapping": mapping}))
    assert preview["row_count"] == 12 and preview["mapping"]["encoding"] == encoding and preview["issues"] == []
    assert preview["source_samples"][0] == {"question": "질문 1", "context": "문맥 1", "answer": "답 1"}
    assert preview["samples"] == [{"instruction": q, "input": c, "output": a} for q, c, a in rows[:5]]
    registered = accepted(call("dataset_import_register", {
        "source_id": source["source_id"], "mapping": mapping, "preview_sha256": preview["preview_sha256"],
        "dataset_id": "qa-source", "version": "1", "synthetic": True, "export_allowed": False}))
    plan = {"dataset": registered["dataset"], "seed": 42, "train_basis_points": 8000,
            "validation_basis_points": 1000, "test_basis_points": 1000,
            "outputs": {role: {"dataset_id": f"qa-{role}", "version": "1"} for role in ("train", "validation", "test")}}
    split = accepted(call("dataset_split_preview", {"plan": plan}))
    assert sum(split["counts"].values()) == 12 and min(split["counts"].values()) >= 1, split["counts"]
    outputs = accepted(call("dataset_split_commit", {"plan_sha256": split["plan_sha256"]}))["outputs"]
    assert outputs == split["outputs"]
    saved = accepted(call("project_save", {"spec": {**project, "datasets": bindings(outputs)},
                                           "expected_revision": created["revision_sha256"]}))
    recipe = accepted(call("recipe_save", {"spec": {
        "recipe_id": "qa-sft-lora", "name": "질의응답 SFT LoRA", "training_stage": "sft", "training_method": "lora",
        "hyperparameters": {"cutoff_len": 64}}, "expected_revision": None}))["revision_sha256"]
    root = seeder().seed(state)["root"]
    benchmark = accepted(call("benchmark_save", {"spec": {
        "dataset": outputs["validation"], "evaluator_kind": "text", "evaluator_version": "1",
        "ordered_sample_ids": [f"row:{index}" for index in range(split["counts"]["validation"])],
        "max_new_tokens": 16, "cutoff_len": 64, "seed": 42, "precision": "bf16", "quantization_bit": None,
        "tokenizer_fingerprint": root["tokenizer_fingerprint"], "template": "qwen",
        "semantic_model_kind": "causal_lm"}}))["revision_sha256"]
    nodes = [train("train", recipe, from_root(root), ["estimate-local"]),
             evaluate("evaluate", output("train"), benchmark, ["estimate-local"])]
    saved = accepted(call("project_save", {"spec": {**saved["project"], "graph": {"nodes": nodes}},
                                           "expected_revision": saved["revision_sha256"]}))
    project_id, revision = project["project_id"], saved["revision_sha256"]
    assert accepted(call("graph_validate", {"project_id": project_id})) == {
        "project_id": project_id, "project_revision": revision, "node_count": 2}
    request = plan_request(project_id, nodes, [estimate("estimate-local")])
    first = accepted(call("graph_plan", request, clock=lambda: NOW))
    assert "warnings" not in first, first   # the seeded module evidence: no simulated_execution_needs_targets
    assert first["simulation"] is True and first["project_revision"] == revision
    assert sorted((item["dataset_id"], item["synthetic"], item["export_allowed"]) for item in first["datasets"]) == [
        (f"qa-{role}", True, False) for role in ("test", "train", "validation")]
    from tuning_factory.workflow_planning import plans_dir
    stored = scanned(plans_dir(state))
    # Content-addressed and immutable: the same request plans the same bytes; graph_plan_get reads them back.
    assert accepted(call("graph_plan", request, clock=lambda: NOW)) == first
    assert scanned(plans_dir(state)) == stored and f"{first['plan_sha256']}.json" in stored
    assert accepted(call("graph_plan_get", {"plan_sha256": first["plan_sha256"]})) == first
    assert accepted(call("graph_runs_list"))["runs"] == [] and not (state / "jobs.db").exists()   # a dry plan only
    renamed = accepted(call("project_save", {"spec": {**saved["project"], "name": "질의응답 실험 02"},
                                             "expected_revision": revision}))
    later = accepted(call("graph_plan", request, clock=lambda: NOW))
    assert later["plan_sha256"] != first["plan_sha256"] and later["project_revision"] == renamed["revision_sha256"]
    assert accepted(call("graph_plan_get", {"plan_sha256": first["plan_sha256"]})) == first   # never rewritten
    [listed] = accepted(call("projects_list"))["projects"]
    assert listed["legacy_origin"] is None and listed["graph_nodes"] == 2
    requests = json.dumps(sent, ensure_ascii=False)
    assert not [name for name in CATALOG_TEMPLATES if name in requests]   # no catalog template was named
    assert opened == [] and connects == []


# -- A2 ------------------------------------------------------------------------------------------

@pytest.mark.parametrize("destinations", ["two_destinations", "one_destination"])
def test_the_mock_diamond_completes_through_the_production_controller(tmp_path, connects, mlflow_clients,
                                                                      destinations):
    state, two = tmp_path / "state", destinations == "two_destinations"
    _, run_id, pinned = planned_diamond(state, tmp_path, two)
    waits = set()

    def each(run):   # graph_status never shows a GPU node on another destination than its pinned one
        for item in run["nodes"]:
            if item["node_id"] not in pinned:
                continue
            assert item["destination_profile_id"] in (None, pinned[item["node_id"]]), item
            assert item["resource_id"] in (None, SIMULATED + pinned[item["node_id"]]), item
            if (item["wait_reason"] or "").startswith("waiting for destination"):
                waits.add((item["node_id"], item["wait_reason"]))

    controller, run = drive(state, run_id, each=each)
    assert run["status"] == "COMPLETED" and set(statuses(run).values()) == {"COMPLETED"}, run
    assert node_view(run, "select")["selection"] == "no_winner"
    rows = attempts(state, run_id)
    gpu = [row for row in rows if row["graph_node_id"] in pinned]
    assert sorted(row["graph_node_id"] for row in gpu) == sorted(pinned)   # exactly one attempt per GPU node
    assert all(node_view(run, node_id)["attempts"] == 1 for node_id in pinned)
    keys = {row["resource_id"] for row in gpu}
    assert keys == ({SIMULATED + "estimate-a", SIMULATED + "estimate-b"} if two else {SIMULATED + "estimate-a"})
    if two:   # the branches had their own destinations
        assert waits == set()
    else:     # one shared key: the second branch waited for it
        assert {reason for _, reason in waits} == {f"waiting for destination {SIMULATED}estimate-a"}
        assert {node_id for node_id, _ in waits} & {"train-b", "train-c"}
    assert controller.tracker is None and mlflow_clients == [] and connects == []


# -- A3 ------------------------------------------------------------------------------------------

def launch_records(state, run):
    """``{node: (launch directories, result files)}`` of every GPU node's ``jobs/<job>/launches/``."""
    found = {}
    for item in run["nodes"]:
        if item["kind"] == "select" or item["job_id"] is None:
            continue
        entries = list((Path(state) / "jobs" / item["job_id"] / "launches").iterdir())
        found[item["node_id"]] = (sorted(entry.name for entry in entries if entry.is_dir()),
                                  sorted(entry.name for entry in entries if entry.name.endswith(".result.json")))
    return found


@pytest.mark.parametrize("case", ["restart", "cancel", "cancel_at_join"])
def test_the_mock_diamond_survives_a_controller_restart_and_honours_cancel(tmp_path, monkeypatch, connects, case):
    from tuning_factory import controller_entry as ce
    from tuning_factory.graph_store import GraphStore
    from tuning_factory.jobs import JobStore
    state = tmp_path / "state"
    # cancel: one destination, so exactly one branch is claimed when the cancel lands at its launch gate.
    _, run_id, pinned = planned_diamond(state, tmp_path, two_destinations=case != "cancel")
    served, seen = {}, {}

    def at_branch_launch(store, lease):   # the D12 point of no return of the first branch, after train-a COMPLETED
        if store.get(lease.job_id)["graph_node_id"] not in ("train-b", "train-c"):
            return False
        seen["node"] = store.get(lease.job_id)["graph_node_id"]
        if case == "restart":
            served["first"].stop.set()
        else:
            seen["canceled"] = accepted(action(state, "graph_cancel", {"run_id": run_id}))["run"]["status"]
            seen["launched"] = launched(state, run_id)
        return True

    def at_join(store, lease, claimed):    # the selection is claimed: both evaluations completed
        seen["canceled"] = accepted(action(state, "graph_cancel", {"run_id": run_id}))["run"]["status"]
        seen["statuses"] = statuses(run_view(state, run_id))
        return True

    hook = (spy(monkeypatch, GraphStore, "complete_local_selection", at_join) if case == "cancel_at_join"
            else spy(monkeypatch, JobStore, "begin_launch", at_branch_launch))
    with hook as fired:
        served["first"] = Served(state, "acceptance.3.1")
        try:
            assert served["first"].running()["current"]["owner_id"] == "acceptance.3.1"
            if case == "restart":
                assert served["first"].join() == ce.EXIT_OK   # drained: the claimed branch finished, nothing new
                halfway = run_view(state, run_id)
                assert halfway["status"] == "RUNNING" and statuses(halfway)["train-a"] == "COMPLETED"
                assert statuses(halfway)["select"] != "COMPLETED" and statuses(halfway)[seen["node"]] == "COMPLETED"
                assert accepted(action(state, "controller_status"))["controller"]["state"] == "absent"
                served["second"] = Served(state, "acceptance.3.2")   # a new owner adopts the same run
                run = wait_run(state, run_id, served=served["second"])
                assert accepted(action(state, "controller_stop"))["requested"] is True
                assert served["second"].join() == ce.EXIT_OK
            else:
                run = wait_run(state, run_id, served=served["first"])
                stopped = accepted(action(state, "controller_stop"))
                assert stopped == {"requested": True, "owner_id": "acceptance.3.1"}
                assert served["first"].join() == ce.EXIT_OK
        finally:
            for item in served.values():
                item.close()
    assert fired == [True]
    rows = attempts(state, run_id)
    if case == "restart":
        assert run["status"] == "COMPLETED" and set(statuses(run).values()) == {"COMPLETED"}, run
        assert node_view(run, "select")["selection"] == "no_winner"
        assert sorted(row["graph_node_id"] for row in rows if row["graph_node_id"] in pinned) == sorted(pinned)
        records = launch_records(state, run)
        assert set(records) == set(pinned)
        for node_id, (launches, results) in records.items():   # one launch, one published result, per node
            assert len(launches) == 1 and results == [launches[0] + ".result.json"], (node_id, launches, results)
    elif case == "cancel":
        assert seen["canceled"] == "CANCELED" and run["status"] == "CANCELED"
        assert statuses(run) == {node_id: "COMPLETED" if node_id == "train-a" else "CANCELED"
                                 for node_id in statuses(run)}, run
        # The branch claimed before the cancel never launched; nothing launched after the cancel time.
        branch = [row for row in rows if row["graph_node_id"] == seen["node"]]
        assert len(branch) == 1 and not branch[0]["launch_started"]
        assert launched(state, run_id) == seen["launched"] == {
            row["attempt_id"] for row in rows if row["graph_node_id"] == "train-a"}
    else:
        assert seen["canceled"] == "CANCELED" and run["status"] == "CANCELED"
        assert {node_id for node_id, status in seen["statuses"].items() if status != "COMPLETED"} == {"select"}
        assert statuses(run) == {**{node_id: "COMPLETED" for node_id in pinned}, "select": "CANCELED"}, run
        assert selection_receipt(state, run_id) is None and node_view(run, "select")["selection"] is None
    assert accepted(action(state, "controller_status"))["controller"]["state"] == "absent"
    assert connects == []


# -- A4 ------------------------------------------------------------------------------------------

def test_invalid_merges_are_refused_before_any_run(tmp_path, connects):
    """The issue codes observed today (Task 11.2 records them) and no run: graph_validate and graph_plan agree,
    except the full-method parent, which only the planner's static routes refuse."""
    state = tmp_path / "state"
    bench = workbench(state, tmp_path)
    full = save_recipe(state, "qa-sft-full", training_method="full")
    second = seeder().write_root(state, "factory-acceptance/tiny-qwen2-b", "b" * 40, seeder().CONFIG)["root"]
    profiles = ["estimate-a"]

    def lora(node_id, root=bench.root, recipe=bench.recipe):
        return train(node_id, recipe, from_root(root), profiles)

    # name: (graph, code, pointer prefix, message, refused by graph_validate too)
    cases = {
        "five_models": ([*(lora(f"t{index}") for index in range(5)),
                         merge("m", [output(f"t{index}") for index in range(5)], profiles, (0.2,) * 5)],
                        "invalid_value", "/graph/nodes/5/models", "at most 4 items", True),
        "weights": ([lora("a"), lora("b"), merge("m", [output("a"), output("b")], profiles, (0.5, 0.6))],
                    "invalid_value", "/graph/nodes/2", "weights must sum to one", True),
        "duplicate_model": ([lora("a"), merge("m", [output("a"), output("a")], profiles)],
                            "invalid_value", "/graph/nodes/1", "merge models must be distinct", True),
        "full_parent": ([lora("a"), lora("f", recipe=full), merge("m", [output("a"), output("f")], profiles)],
                        "plan_refused", "", "merge requires plain causal LoRA/QLoRA parents", False),
        "evaluation_output": ([lora("a"), evaluate("e", output("a"), bench.benchmark, profiles),
                               merge("m", [output("a"), output("e", "evaluation")], profiles)],
                              "typed_port", "/graph/nodes/2", "input requires model output", True),
        "two_roots": ([lora("a"), lora("x", root=second), merge("m", [output("a"), output("x")], profiles)],
                      "merge_bases_differ", "/graph/nodes/2",
                      "merge input effective bases/tokenizers/targets differ", True),
    }
    for name, (nodes, code, path, message, validated) in cases.items():
        save_graph(bench, nodes)
        checked = action(state, "graph_validate", {"project_id": bench.project["project_id"]})
        refused = action(state, "graph_plan", plan_request(bench.project["project_id"], nodes,
                                                           [estimate("estimate-a")]))
        assert refused["status"] == "invalid" and codes(refused) == [code], (name, refused)
        [found] = refused["issues"]
        # Prefix only: a node-level union validator error currently ends in the union tag ("/merge", noted for 11.1).
        assert found["path"].startswith(path) and message in found["message"], (name, found)
        if validated:
            assert checked["status"] == "invalid" and checked["issues"] == refused["issues"], (name, checked)
        else:
            assert checked["status"] == "ok", (name, checked)
    assert accepted(action(state, "graph_runs_list"))["runs"] == [] and not (state / "jobs.db").exists()
    assert connects == []


def test_a_simulated_merge_stops_at_the_merge_node(tmp_path, connects):
    """F14, pinned so a change is deliberate: compilation is real-only for merges, so a simulation plan whose merge
    takes two simulated adapters plans, then the run ends FAILED at the merge node and freezes no merged artifact."""
    state = tmp_path / "state"
    bench = workbench(state, tmp_path)
    profiles = ["estimate-a"]
    nodes = [train("train-a", bench.recipe, from_root(bench.root), profiles),
             train("train-b", bench.recipe, from_root(bench.root), profiles),
             merge("merge", [output("train-a"), output("train-b")], profiles),
             evaluate("eval-merged", output("merge"), bench.benchmark, profiles),
             {"kind": "materialize", "id": "full", "label": "확정", "model": output("merge"),
              "executor_profile_ids": profiles}]
    plan = planned(bench, nodes, [estimate("estimate-a")])
    assert "warnings" not in plan and plan["simulation"] is True
    run_id = submitted(state, plan["plan_sha256"])
    _, run = drive(state, run_id)
    assert run["status"] == "FAILED", run
    assert statuses(run) == {"train-a": "COMPLETED", "train-b": "COMPLETED", "merge": "FAILED",
                             "eval-merged": "BLOCKED", "full": "BLOCKED"}, run
    assert "merge requires real completed artifacts" in node_view(run, "merge")["error"]
    assert node_view(run, "merge")["output"] is None and selection_receipt(state, run_id, "merge") is None
    assert len(frozen_artifacts(state)) == 2   # the two simulated adapters, no merged one
    assert not [row for row in attempts(state, run_id) if row["graph_node_id"] in ("merge", "eval-merged", "full")]
    assert connects == []


# -- A5 ------------------------------------------------------------------------------------------

def real_synthetic_world(tmp_path, monkeypatch):
    """A real plan over synthetic, exportable data (``test_graph_readiness.build(synthetic=True)``) driven by the
    real ``OperationAttemptRunner`` against the fake GPU host (SSH boundary) and the fake MLflow client (MLflow
    boundary) behind the real tracker. Train ``a`` uses the synthetic training data; ``e1``/``e2`` evaluate two
    real adapters on the synthetic held-out data; ``s`` selects between them."""
    from contextlib import contextmanager as manager
    from datetime import datetime, timezone

    import test_graph_controller as gct
    import test_graph_readiness as readiness
    from test_operation_runner import gpu_host

    from tuning_factory.attempt_runner import OperationAttemptRunner
    from tuning_factory.executor_profiles import ResolvedDestination
    w = gct.world(tmp_path, monkeypatch, lambda r: [
        readiness.train(r, "a"), readiness.evaluate(r, "e1", r.first), readiness.evaluate(r, "e2", r.second),
        readiness.select("s", ("e1", "e2"))], synthetic=True)
    _, executor = gpu_host(tmp_path, monkeypatch, [])

    class HostProvider(gct.FakeProvider):
        @manager
        def resolve(self, profile_id, approved_revision, *, pinned, runtime_profile=None, model_ops=False):
            self.resolves.append(profile_id)
            _, facts = self.destinations[profile_id]
            observed = facts.model_copy(update={"observed_at": datetime.fromtimestamp(self.clock(), tz=timezone.utc)})
            yield ResolvedDestination(profile=pinned, observation=observed, executor=executor)

    runner = OperationAttemptRunner(w.store, root=w.store.state_dir)
    controller, owner = gct.start(w, dispatcher=runner, provider=HostProvider(readiness.pods("pod1"), w.clock))
    gct.drive(w, controller, owner)
    return w, gct


def test_synthetic_and_simulated_results_are_never_promoted_or_registered(tmp_path, monkeypatch, connects,
                                                                          mlflow_clients):
    # (a) The A2 diamond (simulation over synthetic data): every candidate is excluded for being simulated, and the
    # run's tracking is local receipts only.
    state = tmp_path / "state"
    _, run_id, pinned = planned_diamond(state, tmp_path / "simulated", two_destinations=False)
    _, run = drive(state, run_id)
    assert run["status"] == "COMPLETED" and node_view(run, "select")["selection"] == "no_winner"
    receipt = selection_receipt(state, run_id)
    assert receipt["status"] == "no_winner" and receipt["winner"] is None and receipt["eligible"] == []
    assert len(receipt["excluded"]) == 2
    for excluded in receipt["excluded"]:
        assert "simulation" in excluded["reasons"] and excluded["record"]["simulation"] is True, excluded
    events = tracking_events(state, run_id)
    assert events and {row["status"] for row in events} == {"ACKNOWLEDGED"}
    assert "model_registration" not in {row["kind"] for row in events}
    assert sorted(path.name for path in (state / "tracking").iterdir()) == [run_id]   # only this run's receipts
    assert len(list((state / "tracking" / run_id).glob("*.json"))) == len(events)
    assert all(node_view(run, node_id)["registration_status"] == "none" for node_id in pinned)
    assert mlflow_clients == []
    # (b) A real run over synthetic data: no winner for being synthetic, no registration, no model version.
    import test_tracking_outbox as outbox_tests

    from tuning_factory.model_artifacts import ModelArtifactRef
    w, gct = real_synthetic_world(tmp_path / "real", monkeypatch)
    assert gct.run_row(w)["status"] == "COMPLETED" and set(gct.statuses(w).values()) == {"COMPLETED"}
    assert gct.status_of(w, "s")["selection"] == "no_winner"
    with w.store.jobs.connection() as db:
        receipt = json.loads(db.execute("SELECT outputs FROM node_outputs WHERE run_id=? AND node_id='s'",
                                        (w.run_id,)).fetchone()[0])["selection"]
    assert receipt["status"] == "no_winner" and receipt["winner"] is None and len(receipt["excluded"]) == 2
    for excluded in receipt["excluded"]:
        assert "synthetic" in excluded["reasons"] and "simulation" not in excluded["reasons"], excluded
    with w.store.jobs.connection() as db:
        model = json.loads(db.execute("SELECT outputs FROM node_outputs WHERE run_id=? AND node_id='a'",
                                      (w.run_id,)).fetchone()[0])["model"]["model"]
    descriptor = w.store.artifacts.verify(ModelArtifactRef.model_validate(model))
    assert descriptor.synthetic and not descriptor.simulation and not descriptor.eligible_for_real_use
    kinds = {row["kind"] for row in gct.events(w)}
    assert "attempt_evidence" in kinds and "model_registration" not in kinds   # real tracking, never registration
    assert outbox_tests.count(w.fake, "create_model_version") == 0 and not w.fake.versions
    assert gct.status_of(w, "a")["registration_status"] == "none"
    assert gct.status_of(w, "a")["tracking_status"] == "saved"
    assert mlflow_clients == [] and connects == []


# -- A6 ------------------------------------------------------------------------------------------

def test_the_headless_lifecycle_through_the_channel(tmp_path, connects):
    from tuning_factory import controller_entry as ce
    state = tmp_path / "state"
    bench = workbench(state, tmp_path)
    plan = planned(bench, [train("train", bench.recipe, from_root(bench.root), ["estimate-a"])],
                   [estimate("estimate-a")])
    absent = accepted(envelope(state, "controller_status"))
    assert absent["controller"]["state"] == "absent" and absent["current"] is None
    served = Served(state, "acceptance.6.1")
    try:
        status = served.running()
        current = status["current"]
        assert current == current_json(state) and current["ok"] is True and current["pid"] == os.getpid()
        assert current["profiles"] == {} and current["skipped_profiles"] == {}
        assert current["tracking_settings_sha256"] is None   # blank tracking: no tracker
        assert status["restart_required"] == {"profiles": [], "tracking": False} and status["stop_requested"] is False
        before = scanned(state / "controller")
        # A second controller for the same state directory exits at once with owner_held and writes nothing.
        assert ce.serve(controller_start(state, "acceptance.6.2")) == ce.EXIT_OWNER_HELD == 3
        assert scanned(state / "controller") == before
        assert accepted(envelope(state, "controller_status"))["controller"]["owner_id"] == "acceptance.6.1"
        run_id = submitted(state, plan["plan_sha256"], "acceptance-headless")
        run = wait_run(state, run_id, served=served)
        assert run["status"] == "COMPLETED" and statuses(run) == {"train": "COMPLETED"}
        assert accepted(envelope(state, "controller_stop")) == {"requested": True, "owner_id": "acceptance.6.1"}
        assert served.join() == ce.EXIT_OK == 0
    finally:
        served.close()
    status = accepted(envelope(state, "controller_status"))
    assert status["controller"] == {"owner_id": "acceptance.6.1", "state": "absent", "heartbeat_age_seconds": None}
    current = current_json(state)
    assert current["owner_id"] == "acceptance.6.1" and current["code"] == "stopped"
    assert current["stopped_at"] >= current["started_at"]
    assert not (state / "controller" / "stop-request.json").exists()
    assert accepted(envelope(state, "controller_stop")) == {"requested": False, "reason": "not_running"}
    assert connects == []
