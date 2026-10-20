"""Task 9.3: graph submission with explicit approvals, DB-only run status, steering and the profile edit check."""
import importlib
import json
import os
import socket
import sqlite3
import subprocess
import sys
import textwrap
from contextlib import closing
from pathlib import Path

import httpx
import pytest
import test_desktop_projects as desk
import test_workflow_planning as wpt

from tuning_factory.registry import canonical

# Built at runtime: the history-walking secret scan must never see a credential-shaped literal.
SECRET = "synthetic-" + "runpod-" + "k" * 12
MLFLOW_SECRET = "synthetic-" + "mlflow-" + "s" * 12
NOW = wpt.NOW
PLANNED_AT = NOW + 60          # wpt.real_plan's clock
SUBMIT_AT = NOW + 120
TOKEN_SETTINGS = wpt.TOKEN_SETTINGS
ACTIONS_93 = desk.ACTIONS_93
ABSENT = {"owner_id": None, "state": "absent", "heartbeat_age_seconds": None}
REMOTE = "/home/researcher/factory/jobs/abc/out.bin"


def runs():
    # A missing implementation is a test failure, rather than a collection error.
    try:
        return importlib.import_module("tuning_factory.workflow_runs")
    except ModuleNotFoundError as exc:
        if exc.name != "tuning_factory.workflow_runs":
            raise
        pytest.fail("the workflow runs module (Task 9.3) is missing")


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Runs are submitted, read and steered from the local ledger only: no provider, SSH, HF or MLflow."""
    def refuse(*args, **kwargs):
        raise AssertionError("workflow run tests must not open a network connection")
    monkeypatch.setattr(socket.socket, "connect", refuse)


def run(state, action, data=None, *, settings=None, profiles=(), secrets=None, clock=SUBMIT_AT):
    """One workflow action with an injected clock, exactly as the channel dispatches it."""
    runs()
    return wpt.run(state, action, data, settings=settings, profiles=profiles, secrets=secrets, clock=clock)


def run_rows(state):
    database = Path(state) / "jobs.db"
    if not database.exists():
        return []
    with closing(sqlite3.connect(database)) as db:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "graph_runs" not in tables:
            return []
        return db.execute("SELECT id,request_id,status FROM graph_runs ORDER BY created,id").fetchall()


def execute(state, statement, *args):
    with closing(sqlite3.connect(Path(state) / "jobs.db")) as db:
        db.execute(statement, args)
        db.commit()


def ledger(state):
    from tuning_factory.graph_store import GraphStore
    return GraphStore(Path(state) / "jobs.db")


# -- scenario builders (9.2's helpers) -------------------------------------------------------------

def simulation_world(state, tmp_path, monkeypatch, **options):
    found = wpt.scenario(state, tmp_path, monkeypatch, **options)
    planned = wpt.run(state, "graph_plan", wpt.plan_request(found.project_id))
    assert planned["status"] == "ok", planned
    found.plan_sha256 = planned["data"]["plan_sha256"]
    return found


def real_world(state, tmp_path, monkeypatch, **options):
    found = wpt.real_scenario(state, tmp_path, monkeypatch, **options)
    planned = wpt.real_plan(state, found)
    assert planned["status"] == "ok", planned
    found.plan_sha256 = planned["data"]["plan_sha256"]
    return found


def submit(state, found, *, request_id="submit-1", approve=True, settings=TOKEN_SETTINGS, profiles=None, sha=None,
           clock=SUBMIT_AT):
    data = {"plan_sha256": sha or found.plan_sha256, "request_id": request_id}
    if approve is True:
        data.update(approve_execution=True, approve_data_transfer=True)
    elif isinstance(approve, dict):
        data.update(approve)
    if profiles is None:
        profiles = [found.profile] if hasattr(found, "profile") else []
    return run(state, "graph_submit", data, settings=settings, profiles=profiles, clock=clock)


def submitted(state, found, **options):
    result = submit(state, found, **options)
    assert result["status"] == "ok", result
    return result["data"]["run_id"]


def provider_spies(monkeypatch):
    """Every way to reach a provider, SSH host, HF or MLflow records the call and fails."""
    import paramiko

    from tuning_factory import desktop_runtime, execution, tracking
    calls = []

    def spy(name):
        def record(*args, **kwargs):
            calls.append(name)
            raise AssertionError(f"{name} must not be reached from submission, status or steering")
        return record

    monkeypatch.setattr(execution, "RunPodController", wpt.REAL_RUNPOD_CONTROLLER)   # the inspection fake is gone
    for method in ("__init__", "request", "status", "create", "start", "stop", "delete"):
        monkeypatch.setattr(execution.RunPodController, method, spy(f"RunPodController.{method}"))
    monkeypatch.setattr(desktop_runtime, "connection", spy("desktop_runtime.connection"))
    monkeypatch.setattr(desktop_runtime, "executor_for", spy("desktop_runtime.executor_for"))
    monkeypatch.setattr(paramiko.SSHClient, "connect", spy("paramiko.connect"))
    monkeypatch.setattr(httpx.Client, "send", spy("httpx.send"))
    monkeypatch.setattr(tracking.MLflowTracker, "__init__", spy("MLflowTracker"))
    monkeypatch.setattr(tracking.LazyMLflowTracker, "__init__", spy("LazyMLflowTracker"))
    return calls


def direct_real_plan(state, found, dataset):
    """A real plan stored the way older code could have: ``plan_graph`` called directly, without 9.2's data checks.

    It reuses the normally planned plan's choices (recipe, root, pinned profile, tracking) for a project of the same
    graph whose train binding is ``dataset``.
    """
    wp = wpt.planning()
    from tuning_factory.graph_planner import plan_graph, snapshot_dataset
    from tuning_factory.projects import DatasetRef, ProjectStore
    from tuning_factory.registry import Registry, immutable_write
    base = wp.stored_plan(state, found.plan_sha256)
    project_id, _ = wpt.make_project(state, [wpt.train("train", found.recipe, wpt.from_root(found.root), ("pod-a",))],
                                     [wpt.binding("train", "train", dataset)], retry_policy={"max_attempts": 2})
    project = ProjectStore(state / "workflows").get(project_id)
    snapshot = snapshot_dataset(Registry(state / "registry"), DatasetRef.model_validate(dataset))
    choices = base.execution_choices.model_copy(update={"datasets": (snapshot,)})
    plan = plan_graph(project, choices, base.resource_snapshots, simulation=False)
    immutable_write(wp.plans_dir(state) / f"{plan.sha256}.json", canonical(plan.model_dump(mode="json")))
    return plan.sha256


# 1
def test_real_submit_requires_the_plan_hash_and_both_approvals(tmp_path, monkeypatch):
    wr = runs()
    wp = wpt.planning()
    from tuning_factory.graph_planner import ApprovalGrant
    state = tmp_path / "state"
    found = real_world(state, tmp_path, monkeypatch)
    refused = submit(state, found, approve=None)
    assert refused["status"] == "invalid", refused
    assert [(item["code"], item["path"], item["message"]) for item in refused["issues"]] == [
        ("approval_required", "/approve_execution", wr.APPROVAL_REQUIRED),
        ("approval_required", "/approve_data_transfer", wr.APPROVAL_REQUIRED)]
    for given, missing in (({"approve_execution": True}, "/approve_data_transfer"),
                           ({"approve_data_transfer": True}, "/approve_execution"),
                           ({"approve_execution": True, "approve_data_transfer": False}, "/approve_data_transfer")):
        desk.only_issue(submit(state, found, approve=given), "invalid", "approval_required", missing)
    # Approvals are strict booleans: a truthy string or number is not an approval.
    for value in ("true", 1):
        desk.only_issue(submit(state, found, approve={"approve_execution": value, "approve_data_transfer": True}),
                        "invalid", "invalid_value", "/approve_execution")
    desk.only_issue(submit(state, found, sha="0" * 64), "invalid", "plan_unknown", "/plan_sha256")
    assert run_rows(state) == []
    result = submit(state, found)
    assert result["status"] == "ok", result
    run_id = result["data"]["run_id"]
    assert len(run_id) == 32 and int(run_id, 16) >= 0
    assert result["data"] == {"run_id": run_id, "status": "RUNNING", "simulation": False,
                              "plan_sha256": found.plan_sha256, "controller": ABSENT}
    plan = wp.stored_plan(state, found.plan_sha256)
    stored = ledger(state).get_run(run_id)
    assert stored["status"] == "RUNNING" and stored["request_id"] == "submit-1"
    assert stored["plan"] == plan and stored["approval"] == ApprovalGrant.for_plan(plan)
    assert stored["created"] == SUBMIT_AT
    receipt = (state / "workflows" / "submissions" / f"{run_id}.json").read_bytes()
    assert receipt == canonical({"run_id": run_id, "plan_sha256": found.plan_sha256, "request_id": "submit-1",
                                 "simulation": False, "approve_execution": True, "approve_data_transfer": True,
                                 "created": SUBMIT_AT})
    assert SECRET.encode() not in receipt and found.profile["fields"]["ssh_key_file"].encode() not in receipt
    # A stored plan that no longer verifies is refused, never submitted.
    path = wp.plans_dir(state) / f"{found.plan_sha256}.json"
    path.write_bytes(path.read_bytes().replace(b'"max_attempts":2', b'"max_attempts":1'))
    desk.only_issue(submit(state, found, request_id="submit-2"), "invalid", "plan_invalid", "/plan_sha256")
    assert [row[1] for row in run_rows(state)] == ["submit-1"]


# 2
def test_simulation_submit_needs_no_transfer_approval_and_contacts_nothing(tmp_path, monkeypatch):
    wp = wpt.planning()
    from tuning_factory.graph_planner import ApprovalGrant
    state = tmp_path / "state"
    found = simulation_world(state, tmp_path, monkeypatch)
    calls = provider_spies(monkeypatch)
    environment = dict(os.environ)
    result = submit(state, found, approve=None, settings={})
    assert result["status"] == "ok", result
    run_id = result["data"]["run_id"]
    assert result["data"] == {"run_id": run_id, "status": "RUNNING", "simulation": True,
                              "plan_sha256": found.plan_sha256, "controller": ABSENT}
    plan = wp.stored_plan(state, found.plan_sha256)
    assert ledger(state).get_run(run_id)["approval"] == ApprovalGrant.for_plan(plan)
    receipt = json.loads((state / "workflows" / "submissions" / f"{run_id}.json").read_bytes())
    assert (receipt["simulation"], receipt["approve_execution"], receipt["approve_data_transfer"]) == (True, False, False)
    # Explicit approvals on a simulation are accepted too; the retry is the same run and the first receipt stands.
    again = submit(state, found, approve=True, settings={})
    assert again["data"]["run_id"] == run_id
    assert json.loads((state / "workflows" / "submissions" / f"{run_id}.json").read_bytes()) == receipt
    for action, body in (("graph_run_get", {"run_id": run_id}), ("graph_runs_list", {}),
                         ("graph_pause", {"run_id": run_id}), ("graph_resume", {"run_id": run_id}),
                         ("executor_profile_edit_check", {"profile_id": "estimate-a"})):
        assert run(state, action, body)["status"] == "ok", action
    assert calls == [] and dict(os.environ) == environment
    assert len(run_rows(state)) == 1


# 3
@pytest.mark.parametrize("case", ["project_saved", "profile_changed", "profile_removed", "tracking_uri",
                                  "auth_mode", "tracking_removed", "artifact_location"])
def test_submit_refuses_a_stale_plan_as_a_conflict(tmp_path, monkeypatch, case):
    wr = runs()
    state = tmp_path / "state"
    found = real_world(state, tmp_path, monkeypatch)
    settings, profiles = dict(TOKEN_SETTINGS), [found.profile]
    path = {"project_saved": "/project", "profile_changed": "/profiles/pod-a", "profile_removed": "/profiles/pod-a"}.get(
        case, "/tracking")
    if case == "project_saved":
        current = desk.call(state, "project_get", {"project_id": found.project_id})["data"]
        saved = desk.call(state, "project_save", {"spec": {**current["project"], "purpose": "바뀐 목적"},
                                                  "expected_revision": current["revision_sha256"]})
        assert saved["status"] == "ok", saved
    elif case == "profile_changed":
        profiles = [{**found.profile, "fields": {**found.profile["fields"], "name": "Renamed"}}]
    elif case == "profile_removed":
        profiles = []
    elif case == "tracking_uri":
        settings["MLFLOW_TRACKING_URI"] = "https://other.invalid"
    elif case == "auth_mode":
        settings["auth_mode"] = "none"
    elif case == "tracking_removed":
        del settings["MLFLOW_TRACKING_URI"]
    else:
        settings["FACTORY_MLFLOW_ARTIFACT_LOCATION"] = "s3://bucket/mlflow"
    result = submit(state, found, settings=settings, profiles=profiles)
    issue = desk.only_issue(result, "conflict", "plan_stale", path)
    assert issue["message"] == wr.PLAN_STALE and result["data"] is None
    assert run_rows(state) == [] and not (state / "workflows" / "submissions").exists()
    # Freshness is checked before the approvals: a stale plan is a conflict even without them.
    desk.only_issue(submit(state, found, settings=settings, profiles=profiles, approve=None), "conflict", "plan_stale",
                    path)


def test_simulation_submit_skips_profile_freshness_but_not_the_project(tmp_path, monkeypatch):
    """A simulation pins a profile as observed and never contacts it (9.2 review Minor 4): only the project counts."""
    wr = runs()
    state = tmp_path / "state"
    found = wpt.real_scenario(state, tmp_path, monkeypatch)
    planned = wpt.run(state, "graph_plan", wpt.plan_request(found.project_id, estimates=()), profiles=[found.profile])
    assert planned["status"] == "ok", planned
    sha = planned["data"]["plan_sha256"]
    renamed = {**found.profile, "fields": {**found.profile["fields"], "name": "Renamed"}}
    for index, (profiles, settings) in enumerate((([renamed], TOKEN_SETTINGS), ([], {}),
                                                  ([found.profile], {"MLFLOW_TRACKING_URI": "https://other.invalid"}))):
        result = submit(state, found, sha=sha, request_id=f"sim-{index}", approve=None, profiles=profiles,
                        settings=settings)
        assert result["status"] == "ok" and result["data"]["simulation"] is True, result
    current = desk.call(state, "project_get", {"project_id": found.project_id})["data"]
    assert desk.call(state, "project_save", {"spec": {**current["project"], "purpose": "바뀐 목적"},
                                             "expected_revision": current["revision_sha256"]})["status"] == "ok"
    stale = submit(state, found, sha=sha, request_id="sim-stale", approve=None)
    assert desk.only_issue(stale, "conflict", "plan_stale", "/project")["message"] == wr.PLAN_STALE
    assert len(run_rows(state)) == 3


# 4
def test_real_submit_refuses_practice_and_unexported_data(tmp_path, monkeypatch):
    from tuning_factory import desktop
    wp = wpt.planning()
    state = tmp_path / "state"
    found = real_world(state, tmp_path, monkeypatch)
    practice = wpt.practice_dataset(state, tmp_path)
    private = wpt.register_data(state, tmp_path, "private", export_allowed=False)
    for dataset, code in ((practice, "practice_data"), (private, "export_not_allowed")):
        sha = direct_real_plan(state, found, dataset)
        assert wp.stored_plan(state, sha).simulation is False
        result = submit(state, found, sha=sha, request_id=f"submit-{code}")
        issue = desk.only_issue(result, "invalid", code, "/plan_sha256")
        assert issue["node_id"] == "train"
        if code == "practice_data":
            assert issue["message"] == desktop.PRACTICE_SUBMIT_REFUSAL
    assert run_rows(state) == []
    # A simulation with the practice dataset never leaves the PC: it is accepted.
    project_id, _ = wpt.make_project(state, [wpt.train("train", found.recipe, wpt.from_root(found.root))],
                                     [wpt.binding("train", "train", practice)])
    planned = wpt.run(state, "graph_plan", wpt.plan_request(project_id))
    assert planned["status"] == "ok", planned
    result = submit(state, found, sha=planned["data"]["plan_sha256"], request_id="practice-sim", approve=None)
    assert result["status"] == "ok" and result["data"]["simulation"] is True, result


def test_real_submit_refuses_unexported_evaluation_data(tmp_path, monkeypatch):
    """The benchmark of an evaluate node is data too; a plan stored without 9.2's data check is refused at submit."""
    wp = wpt.planning()
    state = tmp_path / "state"
    found = wpt.real_scenario(state, tmp_path, monkeypatch)
    heldout = wpt.register_data(state, tmp_path, "heldout", export_allowed=False)
    spec = {"dataset": heldout, "evaluator_kind": "classification", "evaluator_version": "1",
            "ordered_sample_ids": ["row:0", "row:1"], "max_new_tokens": 32, "cutoff_len": 256, "seed": 7,
            "precision": "bf16", "quantization_bit": None, "tokenizer_fingerprint": found.root["tokenizer_fingerprint"],
            "template": "qwen", "semantic_model_kind": "causal_lm"}
    saved = wpt.run(state, "benchmark_save", {"spec": spec})
    assert saved["status"] == "ok", saved
    nodes = [wpt.train("train", found.recipe, wpt.from_root(found.root), ("pod-a",)),
             {"kind": "evaluate", "id": "eval", "label": "평가", "model": wpt.output("train"),
              "benchmark_revision": saved["data"]["revision_sha256"], "executor_profile_ids": ["pod-a"]}]
    project_id, _ = wpt.make_project(state, nodes, [wpt.binding("train", "train", found.dataset),
                                                    wpt.binding("val", "validation", heldout)])
    request = wpt.plan_request(project_id, simulation=False, estimates=(), resources=(("train", 1800), ("eval", 600)))

    def plan():
        return wpt.run(state, "graph_plan", request, settings=TOKEN_SETTINGS, profiles=[found.profile],
                       clock=PLANNED_AT)

    desk.only_issue(plan(), "invalid", "export_not_allowed", "/graph/nodes/1/benchmark_revision")
    monkeypatch.setattr(wp, "_data_checks", lambda analysis, snapshots: None)   # older code
    planned = plan()
    assert planned["status"] == "ok", planned
    result = submit(state, found, sha=planned["data"]["plan_sha256"])
    issue = desk.only_issue(result, "invalid", "export_not_allowed", "/plan_sha256")
    assert issue["node_id"] == "eval"
    assert run_rows(state) == []


# 5
def test_submit_is_idempotent_by_request_id(tmp_path, monkeypatch):
    state = tmp_path / "state"
    found = real_world(state, tmp_path, monkeypatch)
    first = submit(state, found)
    assert first["status"] == "ok", first
    again = submit(state, found, clock=SUBMIT_AT + 30)
    assert again == first
    assert [row[1] for row in run_rows(state)] == ["submit-1"]
    # A simulation of the same project is another plan.
    other = wpt.run(state, "graph_plan", wpt.plan_request(found.project_id, estimates=()), profiles=[found.profile])
    assert other["status"] == "ok" and other["data"]["plan_sha256"] != found.plan_sha256, other
    reused = submit(state, found, sha=other["data"]["plan_sha256"])
    issue = desk.only_issue(reused, "conflict", "request_reused", "/request_id")
    assert issue["message"] == "request ID reused with different plan"
    assert [row[1] for row in run_rows(state)] == ["submit-1"]
    assert sorted(path.name for path in (state / "workflows" / "submissions").iterdir()) == [
        f"{first['data']['run_id']}.json"]


# 6
def test_pause_resume_cancel_map_refusals_to_conflicts(tmp_path, monkeypatch):
    state = tmp_path / "state"
    found = simulation_world(state, tmp_path, monkeypatch)
    run_id = submitted(state, found, approve=None, settings={})
    finished = submitted(state, found, approve=None, settings={}, request_id="submit-2")

    def steer(action, target=run_id):
        return run(state, action, {"run_id": target})

    for action, expected in (("graph_pause", "PAUSED"), ("graph_pause", "PAUSED"), ("graph_resume", "RUNNING"),
                             ("graph_resume", "RUNNING"), ("graph_cancel", "CANCELED"), ("graph_cancel", "CANCELED")):
        result = steer(action)
        assert result["status"] == "ok", (action, result)
        assert set(result["data"]) == {"run", "controller"} and result["data"]["controller"] == ABSENT
        assert (result["data"]["run"]["run_id"], result["data"]["run"]["status"]) == (run_id, expected)
    assert [node["status"] for node in steer("graph_cancel")["data"]["run"]["nodes"]] == ["CANCELED"]
    for action, message in (("graph_resume", "canceled graph cannot resume"),
                            ("graph_pause", "only a running graph can pause")):
        issue = desk.only_issue(steer(action), "conflict", "run_state", "/run_id")
        assert issue["message"] == message
    execute(state, "UPDATE graph_runs SET status='COMPLETED' WHERE id=?", finished)
    for action, message in (("graph_cancel", "a finished graph cannot be canceled"),
                            ("graph_resume", "only a paused graph can resume"),
                            ("graph_pause", "only a running graph can pause")):
        issue = desk.only_issue(steer(action, finished), "conflict", "run_state", "/run_id")
        assert issue["message"] == message
    for action in ("graph_pause", "graph_resume", "graph_cancel", "graph_run_get"):
        desk.only_issue(steer(action, "0" * 32), "invalid", "run_unknown", "/run_id")
    assert sorted(row[2] for row in run_rows(state)) == ["CANCELED", "COMPLETED"]


# 7
def test_run_status_is_db_only_and_secret_free(tmp_path, monkeypatch):
    wp = wpt.planning()
    from tuning_factory.graph_controller import OPERATOR_CONFIRMATION
    from tuning_factory.tracking_outbox import tracking_event_id
    state = tmp_path / "state"
    found = real_world(state, tmp_path, monkeypatch)
    run_id = submitted(state, found)
    broken = submitted(state, found, request_id="submit-2")
    calls = provider_spies(monkeypatch)
    monkeypatch.setenv("MLFLOW_TRACKING_TOKEN", MLFLOW_SECRET)
    execute(state, "UPDATE nodes SET wait_reason=? WHERE run_id=?",
            f"copy failed under {REMOTE} with Bearer {SECRET} and {MLFLOW_SECRET}", run_id)
    event_id = tracking_event_id("graph_run", f"graph:{run_id}")
    execute(state, "UPDATE tracking_events SET status='FAILED',tries=9,last_error=? WHERE event_id=?",
            f"upload of {REMOTE} failed; token={SECRET}", event_id)
    execute(state, "UPDATE graph_runs SET plan='{' WHERE id=?", broken)
    result = run(state, "graph_run_get", {"run_id": run_id})
    assert result["status"] == "ok", result
    data = result["data"]
    assert set(data) == {"run", "controller", "plan", "tracking_failures"}
    assert data["controller"] == ABSENT
    assert [node["node_id"] for node in data["run"]["nodes"]] == ["train"]
    assert (data["run"]["run_id"], data["run"]["status"], data["run"]["simulation"]) == (run_id, "RUNNING", False)
    [node] = data["run"]["nodes"]
    assert node["status"] == "PENDING" and node["needs_operator"] is False and "[path]" in node["wait_reason"]
    assert data["plan"] == wp.plan_summary(wp.stored_plan(state, found.plan_sha256), state)
    assert data["plan"]["tracking"]["settings"] is not None       # the hash-verified public tracking settings
    [failure] = data["tracking_failures"]
    assert (failure["event_id"], failure["kind"], failure["node_id"], failure["status"], failure["tries"]) == (
        event_id, "graph_run", None, "FAILED", 9)
    assert failure["action"] is None and "[path]" in failure["last_error"]
    listed = run(state, "graph_runs_list")
    assert listed["status"] == "ok" and listed["data"]["controller"] == ABSENT
    entries = {entry["run_id"]: entry for entry in listed["data"]["runs"]}
    assert entries[run_id] == {"run_id": run_id, "status": "RUNNING", "simulation": False,
                               "plan_sha256": found.plan_sha256, "created": SUBMIT_AT,
                               "nodes_by_status": {"PENDING": 1}}
    assert set(entries[broken]) == {"run_id", "error"}
    unreadable = run(state, "graph_run_get", {"run_id": broken})
    assert unreadable["status"] == "ok" and set(unreadable["data"]["run"]) == {"run_id", "error"}
    assert unreadable["data"]["plan"] is None and unreadable["data"]["tracking_failures"] == []
    text = json.dumps([result, listed, unreadable])
    assert SECRET not in text and MLFLOW_SECRET not in text and "/home/researcher" not in text
    assert found.profile["fields"]["ssh_key_file"] not in text and "pod-key" not in text
    # D21: an uncertain lock is shown with its operator reason; no desktop action releases it.
    execute(state, "UPDATE nodes SET wait_reason=? WHERE run_id=?", OPERATOR_CONFIRMATION, run_id)
    [node] = run(state, "graph_run_get", {"run_id": run_id})["data"]["run"]["nodes"]
    assert node["needs_operator"] is True and node["wait_reason"] == OPERATOR_CONFIRMATION
    names = desk.workflows().WORKFLOW_ACTIONS
    assert not any("release" in name or "lock" in name for name in names)
    assert calls == []


def test_run_reads_on_a_fresh_state_create_no_ledger(tmp_path):
    from tuning_factory.graph_controller import graph_status
    state = tmp_path / "state"
    assert graph_status(ledger(tmp_path / "other"), now=NOW)["controller"] == ABSENT   # the parity of ABSENT
    assert run(state, "graph_runs_list") == {"status": "ok", "issues": [], "data": {"runs": [], "controller": ABSENT}}
    for action, body, code, path in (("graph_run_get", {"run_id": "0" * 32}, "run_unknown", "/run_id"),
                                     ("graph_pause", {"run_id": "0" * 32}, "run_unknown", "/run_id"),
                                     ("graph_resume", {"run_id": "0" * 32}, "run_unknown", "/run_id"),
                                     ("graph_cancel", {"run_id": "0" * 32}, "run_unknown", "/run_id"),
                                     ("tracking_rearm", {"event_id": "0" * 64}, "tracking_event_unknown", "/event_id"),
                                     ("graph_submit", {"plan_sha256": "0" * 64, "request_id": "r"}, "plan_unknown",
                                      "/plan_sha256")):
        desk.only_issue(run(state, action, body), "invalid", code, path)
    assert run(state, "executor_profile_edit_check", {"profile_id": "pod-a"}) == {
        "status": "ok", "issues": [], "data": {"affected_runs": []}}
    assert not state.exists()


# 8
def test_profile_edit_check_refuses_profiles_with_lock_holding_attempts(tmp_path, monkeypatch):
    wr = runs()
    wp = wpt.planning()
    from tuning_factory.jobs import ACTIVE
    state = tmp_path / "state"
    found = real_world(state, tmp_path, monkeypatch)
    run_id = submitted(state, found)
    store = ledger(state)
    lease = store.acquire_controller(run_id)
    store.prepare_ready(lease)
    claimed = store.claim_ready(wp.stored_plan(state, found.plan_sha256).resource_snapshots[0], lease)
    assert claimed is not None and claimed["status"] in ACTIVE

    def check(profile_id):
        return run(state, "executor_profile_edit_check", {"profile_id": profile_id})

    for alias in ("pod-a", "POD-A", "Pod-A.."):
        result = check(alias)
        issue = desk.only_issue(result, "conflict", "profile_in_use", "/profile_id")
        assert issue["message"] == wr.PROFILE_IN_USE
        assert result["data"] == {"jobs": [{"run_id": run_id, "node_id": "train", "job_id": claimed["id"],
                                            "status": claimed["status"], "locked": True}]}
    assert check("q") == {"status": "ok", "issues": [], "data": {"affected_runs": []}}
    # A lock-holding graph job whose pinned profile cannot be read holds every profile (fail closed).
    with closing(sqlite3.connect(state / "jobs.db")) as db:
        manifest = db.execute("SELECT manifest FROM jobs WHERE id=?", (claimed["id"],)).fetchone()[0]
    execute(state, "UPDATE jobs SET manifest='{' WHERE id=?", claimed["id"])
    unreadable = check("q")
    desk.only_issue(unreadable, "conflict", "profile_in_use", "/profile_id")
    assert [job["job_id"] for job in unreadable["data"]["jobs"]] == [claimed["id"]]
    execute(state, "UPDATE jobs SET manifest=? WHERE id=?", manifest, claimed["id"])
    assert check("q")["status"] == "ok"
    # An uncertain attempt (final status, lock kept for reconciliation) still holds the profile.
    store.jobs.transition(claimed["lease"], "FAILED")
    result = check("pod-a")
    desk.only_issue(result, "conflict", "profile_in_use", "/profile_id")
    assert result["data"]["jobs"][0]["status"] == "FAILED" and result["data"]["jobs"][0]["locked"] is True
    store.jobs.release_gpu(claimed["lease"], execution_stopped=True)
    assert check("pod-a") == {"status": "ok", "issues": [], "data": {"affected_runs": [run_id]}}
    assert check("POD-A.")["data"] == {"affected_runs": [run_id]}
    assert check("q")["data"] == {"affected_runs": []}
    run(state, "graph_pause", {"run_id": run_id})
    assert check("pod-a")["data"] == {"affected_runs": [run_id]}
    run(state, "graph_cancel", {"run_id": run_id})
    assert check("pod-a")["data"] == {"affected_runs": []}
    # A simulation pins the profile as observed but never uses it: no re-plan is needed after an edit.
    planned = wpt.run(state, "graph_plan", wpt.plan_request(found.project_id, estimates=()), profiles=[found.profile])
    assert planned["status"] == "ok", planned
    simulated = submitted(state, found, sha=planned["data"]["plan_sha256"], request_id="sim", approve=None)
    assert ledger(state).get_run(simulated)["approval"].destination_profile_revisions[0].profile_id == "pod-a"
    assert check("pod-a")["data"] == {"affected_runs": []}


# 9
def test_tracking_rearm_rearms_failed_events_only(tmp_path, monkeypatch):
    from tuning_factory.tracking_outbox import EVIDENCE_CHANGED, EVIDENCE_CHANGED_ACTION, tracking_event_id
    state = tmp_path / "state"
    found = simulation_world(state, tmp_path, monkeypatch)
    run_id = submitted(state, found, approve=None, settings={})
    event_id = tracking_event_id("graph_run", f"graph:{run_id}")

    def rearm():
        return run(state, "tracking_rearm", {"event_id": event_id})

    def stored():
        with closing(sqlite3.connect(state / "jobs.db")) as db:
            return db.execute("SELECT status,tries FROM tracking_events WHERE event_id=?", (event_id,)).fetchone()

    assert stored() == ("PENDING", 0)
    issue = desk.only_issue(rearm(), "conflict", "tracking_state", "/event_id")
    assert issue["message"] == "only a failed or uncertain tracking event can be re-armed"
    for status in ("FAILED", "UNCERTAIN"):
        execute(state, "UPDATE tracking_events SET status=?,tries=9,last_error='MLflow refused' WHERE event_id=?",
                status, event_id)
        [failure] = run(state, "graph_run_get", {"run_id": run_id})["data"]["tracking_failures"]
        assert (failure["event_id"], failure["status"], failure["action"]) == (event_id, status, None)
        result = rearm()
        assert result == {"status": "ok", "issues": [], "data": {
            "event_id": event_id, "run_id": run_id, "node_id": None, "kind": "graph_run", "status": "PENDING"}}
        assert stored() == ("PENDING", 0)
        assert run(state, "graph_run_get", {"run_id": run_id})["data"]["tracking_failures"] == []
    # Changed evidence: a re-arm re-reads the same bytes, so the distinct action text is shown with it.
    execute(state, "UPDATE tracking_events SET status='FAILED',last_error=? WHERE event_id=?",
            EVIDENCE_CHANGED + ": train.log", event_id)
    [failure] = run(state, "graph_run_get", {"run_id": run_id})["data"]["tracking_failures"]
    assert failure["action"] == EVIDENCE_CHANGED_ACTION
    result = rearm()
    assert result["status"] == "ok" and result["data"]["status"] == "PENDING"
    [warning] = result["data"]["warnings"]
    assert (warning["code"], warning["path"], warning["message"]) == (
        "tracking_evidence_changed", "/event_id", EVIDENCE_CHANGED_ACTION)
    desk.only_issue(run(state, "tracking_rearm", {"event_id": "0" * 64}), "invalid", "tracking_event_unknown",
                    "/event_id")


def minimal_bodies():
    return {"graph_submit": {"plan_sha256": "0" * 64, "request_id": "r1"}, "graph_runs_list": {},
            "graph_run_get": {"run_id": "0" * 32}, "graph_pause": {"run_id": "0" * 32},
            "graph_resume": {"run_id": "0" * 32}, "graph_cancel": {"run_id": "0" * 32},
            "tracking_rearm": {"event_id": "0" * 64}, "executor_profile_edit_check": {"profile_id": "pod-a"}}


# 10
@pytest.mark.parametrize("action", ACTIONS_93)
def test_run_actions_refuse_extra_fields(tmp_path, monkeypatch, action):
    state = tmp_path / "state"
    found = simulation_world(state, tmp_path, monkeypatch)
    run_id = submitted(state, found, approve=None, settings={})
    body = dict(minimal_bodies()[action])
    if "run_id" in body:
        body["run_id"] = run_id
    before = desk.snapshot(state)
    result = run(state, action, {**body, "command": "calc.exe"})
    issue = desk.only_issue(result, "invalid", "extra_field", "/command")
    assert issue["node_id"] is None and "calc.exe" not in json.dumps(result)
    assert desk.snapshot(state) == before


def test_run_actions_accept_no_credentials(tmp_path):
    wf = desk.workflows()
    runs()
    context = wf.WorkflowContext(state_dir=tmp_path / "state", secrets={"RUNPOD_API_KEY": SECRET})
    for action in ACTIONS_93:
        assert wf.ACTION_MODULES[action] == "tuning_factory.workflow_runs"
        assert wf.registered_requests()[action].model_config["extra"] == "forbid"
        with pytest.raises(ValueError) as refused:
            wf.dispatch_action(context, action, minimal_bodies()[action])
        assert str(refused.value) == wf.SCOPE_REFUSED and SECRET not in str(refused.value)
    assert not (tmp_path / "state").exists()


def test_synthetic_secrets_never_reach_run_responses_files_or_ledger(tmp_path, monkeypatch):
    state = tmp_path / "state"
    for name, value in (("MLFLOW_TRACKING_TOKEN", MLFLOW_SECRET), ("MLFLOW_OIDC_CLIENT_SECRET", MLFLOW_SECRET),
                        ("RUNPOD_API_KEY", SECRET)):
        monkeypatch.setenv(name, value)
    environment = dict(os.environ)
    found = real_world(state, tmp_path, monkeypatch)
    settings = {**TOKEN_SETTINGS, "MLFLOW_OIDC_CLIENT_SECRET_saved": True, "RUNPOD_API_KEY_saved": True,
                "HF_TOKEN_saved": True}
    responses = [submit(state, found, settings=settings)]
    assert responses[0]["status"] == "ok", responses[0]
    run_id = responses[0]["data"]["run_id"]
    for action, body in (("graph_run_get", {"run_id": run_id}), ("graph_runs_list", {}),
                         ("graph_pause", {"run_id": run_id}), ("graph_resume", {"run_id": run_id}),
                         ("executor_profile_edit_check", {"profile_id": "pod-a"}),
                         ("tracking_rearm", {"event_id": "0" * 64}),
                         ("graph_submit", {"plan_sha256": found.plan_sha256, "request_id": "submit-1"})):
        responses.append(run(state, action, body, settings=settings, profiles=[found.profile]))
    text = json.dumps(responses)
    assert SECRET not in text and MLFLOW_SECRET not in text
    stored = wpt.state_bytes(state)
    assert SECRET.encode() not in stored and MLFLOW_SECRET.encode() not in stored
    assert dict(os.environ) == environment


def test_workflow_runs_imports_no_heavy_or_keyring_modules(tmp_path):
    runs()
    code = textwrap.dedent("""
        import json, sys
        import tuning_factory.workflow_runs
        from tuning_factory.desktop_workflows import registered_requests
        heavy = ("torch", "peft", "transformers", "safetensors", "huggingface_hub", "keyring", "paramiko",
                 "mlflow")
        print(json.dumps({"heavy": sorted(name for name in heavy if name in sys.modules),
                          "actions": sorted(registered_requests())}))
    """)
    process = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120, cwd=tmp_path)
    assert process.returncode == 0, process.stderr
    result = json.loads(process.stdout)
    assert result["heavy"] == []
    assert set(ACTIONS_93) <= set(result["actions"])
