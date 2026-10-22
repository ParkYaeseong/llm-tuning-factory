"""Task 9.8: backend additions for the workflow workbench UI (Task 10 plan §2, root decisions of 2026-10-21).

- ``graph_status`` and ``graph_run_get`` nodes carry ``job_id`` and ``attempt_id``.
- ``graph_release_lock`` is the desktop operator release. It only takes a current attempt that needs the operator,
  and it goes through exactly the store path of CLI ``release-lock --confirm-execution-stopped``.
- ``observations_list``, ``executor_profile_inventory`` (SSH only, the 6A inventory probe), ``recipe_get`` by
  ``revision_sha256``, ``planning_inputs_get``/``planning_inputs_save`` and per-node references in ``plan_summary``.
- Legacy ``recover`` (the desktop "연결 끊긴 작업 확인") never fails a graph attempt whose heartbeat is fresh (Q-K).

Fakes only, with the socket guard: no network, GPU, SSH, download, MLflow write or provider call.
"""
import json
import os
import shutil
import socket
import sqlite3
import time
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
import test_desktop_projects as desk
import test_workflow_planning as wpt
import test_workflow_runs as wrt
from test_gpu_servers import A4000, UUID1, UUID2

from tuning_factory.registry import digest

# Built at runtime: the history-walking secret scan must never see a credential-shaped literal.
SECRET = "synthetic-" + "runpod-" + "k" * 12
MLFLOW_SECRET = "synthetic-" + "mlflow-" + "s" * 12
NOW = wpt.NOW
SUBMIT_AT = wrt.SUBMIT_AT
ACTIONS_98 = desk.ACTIONS_98
UUID3 = "GPU-33333333-3333-3333-3333-333333333333"
A100 = "NVIDIA A100-SXM4-80GB"
NVIDIA_QUERY = ["nvidia-smi", "--query-gpu=uuid,name,memory.total,mig.mode.current", "--format=csv,noheader,nounits"]
# Every node field graph_status had before 9.8 (8.5, 8.5 review Minor 6); 9.8 only adds job_id.
NODE_FIELDS_85 = {"node_id", "label", "kind", "status", "job_status", "attempt_id", "attempts", "counted_attempts",
                  "max_attempts", "resource_id", "destination_profile_id", "uncertain", "awaiting_result",
                  "result_deadline", "needs_operator", "wait_reason", "error", "retry_at", "tracking_status",
                  "registration_status", "tracking_action", "output", "selection", "cache_seed"}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Everything here is local: no provider, SSH host, HF or MLflow connection."""
    def refuse(*args, **kwargs):
        raise AssertionError("workbench backend tests must not open a network connection")
    monkeypatch.setattr(socket.socket, "connect", refuse)


@pytest.fixture
def isolated_environment(monkeypatch):
    """The legacy DesktopService exports its settings to the process environment; restore it afterwards."""
    from tuning_factory.connections import ENV_SETTINGS
    for key in ENV_SETTINGS:
        monkeypatch.setenv(key, "x")
        monkeypatch.delenv(key)


def run(state, action, data=None, **options):
    """One workflow action exactly as the channel dispatches it (every 9.8 module registered)."""
    wrt.runs()
    return wpt.run(state, action, data, **options)


def tables(state):
    """Every row of every ledger table, comparable across copies of the state directory."""
    with closing(sqlite3.connect(Path(state) / "jobs.db")) as db:
        names = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        return {name: sorted(repr(tuple(row)) for row in db.execute(f"SELECT * FROM {name}")) for name in names}


def claimed_world(state, tmp_path, monkeypatch):
    """A real run whose train node holds its lock with a launched RUNNING attempt, as a dispatch thread leaves it."""
    wp = wpt.planning()
    found = wrt.real_world(state, tmp_path, monkeypatch)
    run_id = wrt.submitted(state, found)
    store = wrt.ledger(state)
    lease = store.acquire_controller(run_id)
    store.prepare_ready(lease)
    claimed = store.claim_ready(wp.stored_plan(state, found.plan_sha256).resource_snapshots[0], lease)
    attempt = claimed["lease"]
    launch_id = store.jobs.begin_launch(attempt)
    store.jobs.transition(attempt, "RUNNING")
    return SimpleNamespace(found=found, run_id=run_id, store=store, job_id=claimed["id"], lease=attempt,
                           attempt_id=attempt.attempt_id, launch_id=launch_id, controller=lease)


def lose(state, w, *, record=False):
    """The attempt becomes uncertain: FAILED with its lock kept (a stale recover). With ``record`` its launch wrote
    the remote record first, so a late result stays plausible until the result deadline."""
    from tuning_factory.graph_store import REMOTE_ATTEMPT
    w.store.jobs.transition(w.lease, "FAILED", "worker heartbeat expired; reconcile execution before releasing GPU lock")
    if record:
        path = Path(state) / "jobs" / w.job_id / "launches" / w.launch_id / REMOTE_ATTEMPT
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"{}")


def release(state, w, *, clock=SUBMIT_AT, **changes):
    body = {"run_id": w.run_id, "node_id": "train", "job_id": w.job_id, "attempt_id": w.attempt_id,
            "confirm_execution_stopped": True, **changes}
    return wrt.run(state, "graph_release_lock", {key: value for key, value in body.items() if value is not ...},
                   clock=clock)


def node_status(state, w, *, clock=SUBMIT_AT):
    result = wrt.run(state, "graph_run_get", {"run_id": w.run_id}, clock=clock)
    assert result["status"] == "ok", result
    [node] = result["data"]["run"]["nodes"]
    return node


def held(w):
    return w.store.jobs.holds_lock(w.lease)


# -- 1. node identifiers --------------------------------------------------------------------------

def test_run_status_nodes_carry_job_and_attempt_ids(tmp_path, monkeypatch):
    wp = wpt.planning()
    from tuning_factory.graph_controller import graph_status
    state = tmp_path / "state"
    found = wrt.real_world(state, tmp_path, monkeypatch)
    run_id = wrt.submitted(state, found)
    node = node_status(state, SimpleNamespace(run_id=run_id))
    assert set(node) == NODE_FIELDS_85 | {"job_id"}            # every 8.5 field stays
    assert (node["job_id"], node["attempt_id"]) == (None, None)   # not prepared yet
    store = wrt.ledger(state)
    lease = store.acquire_controller(run_id)
    store.prepare_ready(lease)
    [prepared] = store.get_run(run_id)["nodes"]
    node = node_status(state, SimpleNamespace(run_id=run_id))
    assert prepared["job_id"] and (node["job_id"], node["attempt_id"]) == (prepared["job_id"], None)
    claimed = store.claim_ready(wp.stored_plan(state, found.plan_sha256).resource_snapshots[0], lease)
    node = node_status(state, SimpleNamespace(run_id=run_id))
    assert (node["job_id"], node["attempt_id"]) == (claimed["id"], claimed["lease"].attempt_id)
    [direct] = graph_status(store, run_id, now=SUBMIT_AT)["runs"][0]["nodes"]
    assert (direct["job_id"], direct["attempt_id"]) == (claimed["id"], claimed["lease"].attempt_id)
    assert set(direct) == NODE_FIELDS_85 | {"job_id"}


# -- 2. graph_release_lock ------------------------------------------------------------------------

def test_release_lock_only_for_the_current_attempt_that_needs_the_operator(tmp_path, monkeypatch):
    wr = wrt.runs()
    from tuning_factory.graph_controller import OPERATOR_CONFIRMATION
    state = tmp_path / "state"
    w = claimed_world(state, tmp_path, monkeypatch)
    # A running attempt needs nobody.
    before = tables(state)
    issue = desk.only_issue(release(state, w), "conflict", "operator_not_needed", "/node_id")
    assert issue["message"] == wr.OPERATOR_NOT_NEEDED and issue["node_id"] == "train"
    # A forged attempt ID on the running node is stale first: identity is checked before the state (9.8 review 4).
    issue = desk.only_issue(release(state, w, attempt_id="0" * 32), "conflict", "attempt_stale", "/attempt_id")
    assert issue["message"] == wr.ATTEMPT_STALE and issue["node_id"] == "train"
    assert tables(state) == before and held(w)
    lose(state, w, record=True)
    before = tables(state)
    # While a late result is plausible (the real clock is before the result deadline) it waits: never released.
    node = node_status(state, w, clock=time.time())
    assert (node["uncertain"], node["awaiting_result"], node["needs_operator"]) == (True, True, False)
    issue = desk.only_issue(release(state, w, clock=time.time()), "conflict", "awaiting_result", "/node_id")
    assert issue["message"] == wr.AWAITING_RESULT_REFUSED and issue["node_id"] == "train"
    # SUBMIT_AT is past the deadline: no result can come any more, so the operator must decide.
    node = node_status(state, w)
    assert (node["uncertain"], node["awaiting_result"], node["needs_operator"]) == (True, False, True)
    assert (node["job_id"], node["attempt_id"]) == (w.job_id, w.attempt_id)
    # The confirmation is exactly true; a truthy string or number is not a confirmation.
    issue = desk.only_issue(release(state, w, confirm_execution_stopped=False), "invalid", "confirmation_required",
                            "/confirm_execution_stopped")
    assert issue["message"] == wr.RELEASE_CONFIRMATION_REQUIRED
    for value in ("true", 1, None, ...):
        desk.only_issue(release(state, w, confirm_execution_stopped=value), "invalid", "invalid_value",
                        "/confirm_execution_stopped")
    # Another run, node, job or attempt.
    for change, status, code, path in (({"run_id": "0" * 32}, "invalid", "run_unknown", "/run_id"),
                                       ({"node_id": "absent"}, "invalid", "node_unknown", "/node_id"),
                                       ({"job_id": "f" * 32}, "invalid", "job_mismatch", "/job_id"),
                                       ({"attempt_id": "0" * 32}, "conflict", "attempt_stale", "/attempt_id")):
        desk.only_issue(release(state, w, **change), status, code, path)
    assert tables(state) == before and held(w)
    # The release: the lock goes, nothing else in the ledger changes, and the attempt stays the current one.
    result = release(state, w)
    assert result["status"] == "ok", result
    assert set(result["data"]) == {"released", "run", "controller"}
    assert result["data"]["released"] == {"run_id": w.run_id, "node_id": "train", "job_id": w.job_id,
                                          "attempt_id": w.attempt_id}
    assert result["data"]["controller"] == wrt.ABSENT and result["data"]["run"]["run_id"] == w.run_id
    [node] = result["data"]["run"]["nodes"]
    assert (node["uncertain"], node["job_status"], node["attempt_id"]) == (False, "FAILED", w.attempt_id)
    after = tables(state)
    assert not held(w) and after["gpu_locks"] == [] and after["resource_leases"] == []
    assert before["gpu_locks"] and before["resource_leases"]
    unchanged = {name: rows for name, rows in before.items() if name not in {"gpu_locks", "resource_leases"}}
    assert {name: rows for name, rows in after.items() if name not in {"gpu_locks", "resource_leases"}} == unchanged
    # Until the controller settles the node, its note still asks the operator; a second release settles nothing.
    wrt.execute(state, "UPDATE nodes SET wait_reason=? WHERE run_id=?", OPERATOR_CONFIRMATION, w.run_id)
    node = node_status(state, w)
    assert (node["needs_operator"], node["uncertain"]) == (True, False)
    noted = tables(state)
    issue = desk.only_issue(release(state, w), "conflict", "release_not_applicable", "/node_id")
    assert issue["message"] == wr.RELEASE_NOT_APPLICABLE
    assert tables(state) == noted and noted["gpu_locks"] == []
    # An unreadable run proves nothing: refused.
    wrt.execute(state, "UPDATE graph_runs SET plan='{' WHERE id=?", w.run_id)
    desk.only_issue(release(state, w), "invalid", "run_unreadable", "/run_id")


def test_release_lock_never_frees_an_operator_node_that_is_not_uncertain(tmp_path, monkeypatch):
    """A node can need the operator for another reason (here: a COMPLETED attempt whose outputs were refused, its
    lock still held for the controller's rebuild). CLI ``release-lock`` would free that lock; the desktop does not."""
    from tuning_factory.graph_controller import OUTPUTS_REFUSED
    state = tmp_path / "state"
    w = claimed_world(state, tmp_path, monkeypatch)
    w.store.jobs.transition(w.lease, "EVALUATING")
    w.store.jobs.transition(w.lease, "COMPLETED")
    wrt.execute(state, "UPDATE nodes SET wait_reason=? WHERE run_id=?", OUTPUTS_REFUSED.format(error="bad"), w.run_id)
    node = node_status(state, w)
    assert (node["needs_operator"], node["uncertain"], node["job_status"]) == (True, False, "COMPLETED") and held(w)
    before = tables(state)
    desk.only_issue(release(state, w), "conflict", "release_not_applicable", "/node_id")
    assert tables(state) == before and held(w)


def test_release_lock_rechecks_the_ledger_after_reading_the_status(tmp_path, monkeypatch):
    """The CLI's own checks run again after ``graph_status``: a takeover, a release or a requeue in between wins."""
    wr = wrt.runs()
    from tuning_factory.graph_controller import graph_status
    from tuning_factory.jobs import JobStore
    state = tmp_path / "state"
    w = claimed_world(state, tmp_path, monkeypatch)
    lose(state, w)
    # A takeover (a controller's reconcile) rotates the token after the lease was read: the old lease is stale.
    original = JobStore.current_lease

    def taken_over(self, job_id):
        lease = original(self, job_id)
        self.takeover(lease)
        return lease

    monkeypatch.setattr(JobStore, "current_lease", taken_over)
    desk.only_issue(release(state, w), "conflict", "attempt_stale", "/attempt_id")
    monkeypatch.setattr(JobStore, "current_lease", original)
    assert tables(state)["gpu_locks"]
    # The status said "uncertain", but the lock went meanwhile (another release), or the job was requeued.
    seen = graph_status(w.store, w.run_id, now=SUBMIT_AT)
    monkeypatch.setattr(wr, "graph_status", lambda store, run_id=None, *, now: seen)
    before = tables(state)
    wrt.execute(state, "UPDATE jobs SET status='QUEUED' WHERE id=?", w.job_id)
    desk.only_issue(release(state, w), "conflict", "operator_not_needed", "/node_id")
    wrt.execute(state, "UPDATE jobs SET status='FAILED' WHERE id=?", w.job_id)
    assert tables(state) == before
    w.store.jobs.release_gpu(original(w.store.jobs, w.job_id), execution_stopped=True)
    desk.only_issue(release(state, w), "conflict", "lock_not_held", "/node_id")
    # A newer attempt became the current one, or none is.
    wrt.execute(state, "INSERT INTO job_attempts SELECT ?,job_id,?,resource_id,?,1,0,created FROM job_attempts "
                "WHERE attempt_id=?", "9" * 32, "8" * 32, "7" * 32, w.attempt_id)
    wrt.execute(state, "UPDATE jobs SET active_attempt_id=? WHERE id=?", "9" * 32, w.job_id)
    desk.only_issue(release(state, w), "conflict", "attempt_stale", "/attempt_id")
    wrt.execute(state, "UPDATE jobs SET active_attempt_id=NULL WHERE id=?", w.job_id)
    desk.only_issue(release(state, w), "conflict", "attempt_stale", "/attempt_id")


def test_release_lock_takes_exactly_the_cli_store_path(tmp_path, monkeypatch):
    """The same reads, checks and single ``JobStore.release_gpu(lease, execution_stopped=True)`` as CLI
    ``release-lock --confirm-execution-stopped``: two copies of one ledger end byte-identical."""
    from typer.testing import CliRunner

    from tuning_factory.cli import app
    from tuning_factory.jobs import JobStore
    state, twin = tmp_path / "state", tmp_path / "twin"
    w = claimed_world(state, tmp_path, monkeypatch)
    lose(state, w)
    shutil.copytree(state, twin)
    calls = []
    original = JobStore.release_gpu

    def spy(self, lease, *, execution_stopped=False):
        calls.append((Path(self.path).parent.name, lease, execution_stopped))
        return original(self, lease, execution_stopped=execution_stopped)

    monkeypatch.setattr(JobStore, "release_gpu", spy)
    result = release(state, w)
    assert result["status"] == "ok", result
    monkeypatch.delenv("MLFLOW_TRACKING_URI", raising=False)
    env_file = tmp_path / "empty.env"
    env_file.write_bytes(b"")
    cli = CliRunner().invoke(app, ["--state-dir", str(twin), "--env-file", str(env_file), "release-lock", w.job_id,
                                   w.attempt_id, "--confirm-execution-stopped"])
    assert cli.exit_code == 0, cli.output
    assert calls == [("state", w.lease, True), ("twin", w.lease, True)]
    assert tables(state) == tables(twin)


def test_release_lock_request_is_strict_and_reads_no_absent_ledger(tmp_path):
    state = tmp_path / "state"
    body = {"run_id": "0" * 32, "node_id": "train", "job_id": "1" * 32, "attempt_id": "2" * 32,
            "confirm_execution_stopped": True}
    desk.only_issue(wrt.run(state, "graph_release_lock", body), "invalid", "run_unknown", "/run_id")
    desk.only_issue(wrt.run(state, "graph_release_lock", {**body, "confirm_execution_stopped": False}), "invalid",
                    "confirmation_required", "/confirm_execution_stopped")
    for name, value in (("run_id", "x"), ("job_id", "../" + "a" * 29), ("attempt_id", "A" * 32), ("node_id", "a/b")):
        desk.only_issue(wrt.run(state, "graph_release_lock", {**body, name: value}), "invalid", "invalid_value",
                        f"/{name}")
    assert not state.exists()


# -- 3. observations_list -------------------------------------------------------------------------

def test_observations_list_shows_each_profile_last_observation_publicly(tmp_path, monkeypatch):
    wp = wpt.planning()
    from test_gpu_servers import FakeServer
    state = tmp_path / "state"
    assert run(state, "observations_list") == {"status": "ok", "issues": [], "data": {"observations": []}}
    assert not state.exists()
    pod = wpt.runpod_profile(tmp_path)
    inspected, _ = wpt.inspect(state, monkeypatch, pod)
    assert inspected["status"] == "ok", inspected
    FakeServer(tmp_path / "remote").install(monkeypatch)
    lab = wpt.server_profile()
    inspected = run(state, "executor_profile_inspect", {"profile_id": "lab"}, profiles=[lab], clock=NOW + 30)
    assert inspected["status"] == "ok", inspected
    before = desk.snapshot(state)
    pod_seen, lab_seen = wp.load_observation(state, "pod-a"), wp.load_observation(state, "lab")
    listed = run(state, "observations_list", profiles=[pod, lab], clock=NOW + 60)
    assert listed["status"] == "ok", listed
    assert listed["data"]["observations"] == [
        {"profile_id": "lab", "revision": wp.profile_revision("lab", lab["fields"]),
         "observed_at": lab_seen["observed_at"].isoformat(), "age_seconds": 30.0, "provider": "ssh_server",
         "name": "Lab server", "resource_id": lab_seen["facts"].exclusivity_key, "gpu_type": A4000, "gpu_count": 1,
         "total_hourly_cost_usd": 0.0, "cost_source": "operator", "selected_gpu_uuids": [UUID2],
         "profile_current": True, "fresh_for_real_plan": True},
        {"profile_id": "pod-a", "revision": wp.profile_revision("pod-a", pod["fields"]),
         "observed_at": pod_seen["observed_at"].isoformat(), "age_seconds": 60.0, "provider": "existing_runpod",
         "name": "Pod A", "resource_id": "existing_runpod:pod1", "gpu_type": pod_seen["facts"].gpu_type,
         "gpu_count": pod_seen["facts"].gpu_count, "total_hourly_cost_usd": pod_seen["facts"].total_hourly_cost_usd,
         "cost_source": "runpod_api", "selected_gpu_uuids": [], "profile_current": True,
         "fresh_for_real_plan": True}]
    responses = [listed]
    # A changed profile, a profile that no longer exists, and an observation older than an hour.
    renamed = {**pod, "fields": {**pod["fields"], "name": "Renamed"}}
    responses.append(run(state, "observations_list", profiles=[renamed], clock=NOW + 60))
    entries = {item["profile_id"]: item for item in responses[-1]["data"]["observations"]}
    assert (entries["pod-a"]["profile_current"], entries["pod-a"]["fresh_for_real_plan"]) == (False, False)
    assert (entries["lab"]["profile_current"], entries["lab"]["fresh_for_real_plan"]) == (None, False)
    responses.append(run(state, "observations_list", profiles=[pod, lab], clock=NOW + 3601))
    entries = {item["profile_id"]: item for item in responses[-1]["data"]["observations"]}
    assert (entries["pod-a"]["age_seconds"], entries["pod-a"]["fresh_for_real_plan"]) == (3601.0, False)
    assert (entries["lab"]["age_seconds"], entries["lab"]["fresh_for_real_plan"]) == (3571.0, True)
    assert desk.snapshot(state) == before                       # reads only
    # A damaged record is listed as unreadable; the others stay readable.
    (wp.observations_dir(state) / "pod-a.json").write_bytes(b"{")
    responses.append(run(state, "observations_list", profiles=[pod, lab], clock=NOW + 60))
    entries = {item["profile_id"]: item for item in responses[-1]["data"]["observations"]}
    assert entries["pod-a"] == {"profile_id": "pod-a", "error": wp.OBSERVATION_INVALID}
    assert entries["lab"]["fresh_for_real_plan"] is True
    text = json.dumps(responses)
    assert pod["fields"]["ssh_key_file"] not in text and "test-key" not in text and SECRET not in text


# -- 4. executor_profile_inventory -----------------------------------------------------------------

def test_profile_inventory_lists_ssh_gpus_without_a_selection(tmp_path, monkeypatch):
    wp = wpt.planning()
    from test_gpu_servers import FakeServer

    from tuning_factory import desktop_runtime
    state = tmp_path / "state"
    inventory = ((UUID2, A100, 81920), (UUID1, A4000, 16376))
    server = FakeServer(tmp_path / "remote", inventory=inventory).install(monkeypatch)
    monkeypatch.setattr(desktop_runtime, "check", lambda *a, **kw: pytest.fail("the inventory runs no runtime probe"))
    lab = wpt.server_profile(server_selected_gpu_uuids="")   # the native store keeps "" before any selection
    result = run(state, "executor_profile_inventory", {"profile_id": "lab"}, profiles=[lab])
    assert result["status"] == "ok", result
    server_id = digest(("b" * 32).encode())
    assert result["data"] == {
        "profile_id": "lab", "revision": wp.profile_revision("lab", lab["fields"]),
        "observed_at": result["data"]["observed_at"], "server_id": server_id, "resource_id": "ssh_server:" + server_id,
        "inventory": [{"uuid": UUID1, "name": A4000, "memory_mib": 16376}, {"uuid": UUID2, "name": A100,
                                                                          "memory_mib": 81920}],
        "selected_gpu_uuids": []}
    assert datetime.fromisoformat(result["data"]["observed_at"]).utcoffset() is not None
    # The fixed read-only probe of the legacy server_inspect: the identity read, then the restricted nvidia-smi query.
    assert [argv[:2] for argv in server.commands] == [["python3", "-c"], NVIDIA_QUERY[:2]]
    assert server.commands[1] == NVIDIA_QUERY
    legacy = FakeServer(tmp_path / "legacy", inventory=inventory).install(monkeypatch)
    fields = {key: value for key, value in lab["fields"].items() if key != "server_selected_gpu_uuids"}
    desktop_runtime.inspect_server(fields)
    assert legacy.commands == server.commands
    assert not state.exists()                                    # nothing stored: the listing is no-op safe
    assert "test-key" not in json.dumps(result) and "gpu.example" not in json.dumps(result)
    # A saved selection, even one that is no longer installed, never blocks the listing and is reported as saved.
    for selection in (json.dumps([UUID2]), json.dumps([UUID3])):
        listed = run(state, "executor_profile_inventory", {"profile_id": "lab"},
                     profiles=[wpt.server_profile(server_selected_gpu_uuids=selection)])
        assert listed["status"] == "ok", listed
        assert listed["data"]["selected_gpu_uuids"] == json.loads(selection)
        assert [gpu["uuid"] for gpu in listed["data"]["inventory"]] == [UUID1, UUID2]
    without = wpt.server_profile()
    del without["fields"]["server_selected_gpu_uuids"]
    assert run(state, "executor_profile_inventory", {"profile_id": "lab"}, profiles=[without])["status"] == "ok"
    assert not state.exists()


def test_profile_inventory_refusals_contact_nothing(tmp_path, monkeypatch):
    wp = wpt.planning()
    from test_gpu_servers import FakeServer

    from tuning_factory import desktop_runtime
    state = tmp_path / "state"
    server = FakeServer(tmp_path / "remote").install(monkeypatch)
    pods = wpt.FakeRunPod().install(monkeypatch)
    pod = wpt.runpod_profile(tmp_path)
    issue = desk.only_issue(run(state, "executor_profile_inventory", {"profile_id": "pod-a"}, profiles=[pod]),
                            "invalid", "inventory_ssh_only", "/profile_id")
    assert issue["message"] == wp.INVENTORY_SSH_ONLY
    desk.only_issue(run(state, "executor_profile_inventory", {"profile_id": "lab"}, profiles=[pod]), "invalid",
                    "executor_profile_missing", "/profile_id")
    for change in ({"server_ssh_port": "ssh"}, {"server_ssh_key_file": "keys/relative"},
                   {"server_selected_gpu_uuids": "[\"GPU-1\"]"}, {"server_total_hourly_cost_usd": ""}):
        broken = wpt.server_profile(**change)
        desk.only_issue(run(state, "executor_profile_inventory", {"profile_id": "lab"}, profiles=[broken]),
                        "invalid", "executor_profile_invalid", "/profile_id")
    assert server.commands == [] and pods.calls == [] and pods.keys == []
    remote = "remote output " + "x" * 64 + " /home/researcher/private"

    @contextmanager
    def failing(*args, **kwargs):
        raise desktop_runtime.RemoteCommandFailed("GPU runtime command failed. " + remote)
        yield

    monkeypatch.setattr(desktop_runtime, "connection", failing)
    result = run(state, "executor_profile_inventory", {"profile_id": "lab"}, profiles=[wpt.server_profile()])
    issue = desk.only_issue(result, "invalid", "remote_command_failed")
    assert issue["message"] == wp.REMOTE_COMMAND_FAILED and "remote output" not in json.dumps(result)
    assert not state.exists()


# -- 5. recipe_get by revision hash ----------------------------------------------------------------

def test_recipe_get_accepts_a_revision_hash_instead_of_the_id(tmp_path):
    state = tmp_path / "state"
    desk.only_issue(desk.call(state, "recipe_get", {"revision_sha256": "0" * 64}), "invalid", "unknown_definition",
                    "/revision_sha256")
    assert not state.exists()
    first = wpt.save_recipe(state)
    original = desk.call(state, "recipe_get", {"recipe_id": "sft-lora"})["data"]
    edited = desk.call(state, "recipe_save", {"spec": {**original["recipe"], "description": "다음 버전"},
                                              "expected_revision": first})
    assert edited["status"] == "ok", edited
    # A card pinned to a superseded revision still shows its recipe by the hash alone.
    assert desk.call(state, "recipe_get", {"revision_sha256": first}) == {"status": "ok", "issues": [],
                                                                          "data": original}
    assert desk.call(state, "recipe_get", {"revision_sha256": edited["data"]["revision_sha256"]})["data"] == \
        edited["data"]
    for body, path in (({}, "/recipe_id"),
                       ({"recipe_id": "sft-lora", "revision_sha256": first}, "/revision_sha256"),
                       ({"revision_sha256": first, "revision": first}, "/revision")):
        desk.only_issue(desk.call(state, "recipe_get", body), "invalid", "invalid_value", path)
    desk.only_issue(desk.call(state, "recipe_get", {"revision_sha256": "e" * 64}), "invalid", "unknown_definition",
                    "/revision_sha256")
    desk.only_issue(desk.call(state, "recipe_get", {"revision_sha256": "E" * 64}), "invalid", "invalid_value",
                    "/revision_sha256")
    # The existing lookups are unchanged.
    assert desk.call(state, "recipe_get", {"recipe_id": "sft-lora", "revision": first})["data"] == original


# -- 6. planning_inputs_get / planning_inputs_save -------------------------------------------------

def planning_project(state, tmp_path):
    nodes = [{"kind": "train", "id": "a", "label": "학습"}, {"kind": "evaluate", "id": "ea", "label": "평가"},
             {"kind": "select", "id": "pick", "label": "선택"}]
    return wpt.make_project(state, nodes, [])


def inputs_body(project_id, revision, **changes):
    return {"project_id": project_id, "project_revision": revision, "simulation": False,
            "node_time_limits": [{"node_id": "a", "timeout_minutes": 90}, {"node_id": "ea", "timeout_minutes": 10080}],
            "estimates": [wpt.ESTIMATE, {"profile_id": "estimate-b", "gpu_type": "H100", "gpu_count": 8,
                                         "total_hourly_cost_usd": 24.5}], **changes}


def test_planning_inputs_round_trip_with_cas_on_the_project_revision(tmp_path):
    wp = wpt.planning()
    state = tmp_path / "state"
    for action, body in (("planning_inputs_get", {"project_id": "p"}),
                         ("planning_inputs_save", inputs_body("p", "0" * 64))):
        desk.only_issue(run(state, action, body), "invalid", "unknown_definition", "/project_id")
    assert not state.exists()
    project_id, revision = planning_project(state, tmp_path)
    directory = wp.planning_inputs_dir(state)
    assert run(state, "planning_inputs_get", {"project_id": project_id}) == {"status": "ok", "issues": [], "data": {
        "project_id": project_id, "current_project_revision": revision, "inputs": None}}
    assert not directory.exists()
    body = inputs_body(project_id, revision)
    saved = run(state, "planning_inputs_save", body, clock=NOW + 5)
    assert saved == {"status": "ok", "issues": [], "data": {
        "project_id": project_id, "current_project_revision": revision,
        "inputs": {"project_revision": revision, "simulation": False, "node_time_limits": body["node_time_limits"],
                   "estimates": body["estimates"],
                   "saved_at": datetime.fromtimestamp(NOW + 5, tz=timezone.utc).isoformat()}}}
    assert run(state, "planning_inputs_get", {"project_id": project_id}) == saved
    path = directory / f"{digest(project_id.encode())}.json"
    assert wpt.files(directory) == [path.name]                    # one file per project, no temporary left
    stored = path.read_bytes()
    # Saved again without the estimates: the record is replaced as a whole.
    resaved = run(state, "planning_inputs_save", {**body, "estimates": [], "simulation": True}, clock=NOW + 6)
    assert resaved["data"]["inputs"]["estimates"] == [] and resaved["data"]["inputs"]["simulation"] is True
    assert wpt.files(directory) == [path.name] and path.read_bytes() != stored
    stored = path.read_bytes()
    # CAS: the project changed since; the stored inputs stay, and a read names both revisions.
    current = desk.call(state, "project_get", {"project_id": project_id})["data"]
    changed = desk.call(state, "project_save", {"spec": {**current["project"], "purpose": "바뀐 목적"},
                                                "expected_revision": revision})
    assert changed["status"] == "ok", changed
    issue = desk.only_issue(run(state, "planning_inputs_save", body), "conflict", "revision_conflict",
                            "/project_revision")
    assert issue["message"] == wp.PLANNING_INPUTS_STALE and path.read_bytes() == stored
    read = run(state, "planning_inputs_get", {"project_id": project_id})["data"]
    assert (read["current_project_revision"], read["inputs"]["project_revision"]) == (
        changed["data"]["revision_sha256"], revision)
    again = run(state, "planning_inputs_save", inputs_body(project_id, changed["data"]["revision_sha256"]))
    assert again["status"] == "ok" and again["data"]["inputs"]["project_revision"] == changed["data"]["revision_sha256"]
    # A damaged record is reported, and a save replaces it.
    path.write_bytes(b"{")
    desk.only_issue(run(state, "planning_inputs_get", {"project_id": project_id}), "invalid",
                    "planning_inputs_invalid", "/project_id")
    assert run(state, "planning_inputs_save", inputs_body(project_id, changed["data"]["revision_sha256"]))[
        "status"] == "ok"
    assert run(state, "planning_inputs_get", {"project_id": project_id})["status"] == "ok"


def test_planning_inputs_refuse_out_of_range_unknown_and_duplicate_values(tmp_path):
    state = tmp_path / "state"
    project_id, revision = planning_project(state, tmp_path)
    base = inputs_body(project_id, revision)
    limits, estimate = base["node_time_limits"], wpt.ESTIMATE
    cases = [
        ({"node_time_limits": [{"node_id": "a", "timeout_minutes": 0}]}, "invalid_value",
         "/node_time_limits/0/timeout_minutes"),
        ({"node_time_limits": [{"node_id": "a", "timeout_minutes": 10081}]}, "invalid_value",
         "/node_time_limits/0/timeout_minutes"),
        ({"node_time_limits": [{"node_id": "a", "timeout_minutes": "90"}]}, "invalid_value",
         "/node_time_limits/0/timeout_minutes"),
        ({"node_time_limits": [{"node_id": "a", "timeout_minutes": 1.5}]}, "invalid_value",
         "/node_time_limits/0/timeout_minutes"),
        ({"estimates": [{**estimate, "total_hourly_cost_usd": 0}]}, "invalid_value",
         "/estimates/0/total_hourly_cost_usd"),
        ({"estimates": [{**estimate, "profile_id": "pod-a"}]}, "invalid_value", "/estimates/0/profile_id"),
        ({"estimates": [{**estimate, "gpu_count": 9}]}, "invalid_value", "/estimates/0/gpu_count"),
        ({"simulation": "true"}, "invalid_value", "/simulation"),
        ({"project_revision": "A" * 64}, "invalid_value", "/project_revision"),
        ({"node_time_limits": [limits[0], {"node_id": "a", "timeout_minutes": 5}]}, "node_resource_duplicate",
         "/node_time_limits/1/node_id"),
        ({"node_time_limits": [{"node_id": "absent", "timeout_minutes": 5}]}, "node_resource_unknown",
         "/node_time_limits/0/node_id"),
        ({"node_time_limits": [{"node_id": "pick", "timeout_minutes": 5}]}, "node_resource_unknown",
         "/node_time_limits/0/node_id"),
        ({"estimates": [estimate, {**estimate, "profile_id": "estimate-A."}]}, "estimate_duplicate",
         "/estimates/1/profile_id"),
        ({"RUNPOD_API_KEY": SECRET}, "extra_field", ""),   # the object holding the unknown key (11.1)
        ({"estimates": [{**estimate, "RUNPOD_API_KEY": SECRET}]}, "extra_field", "/estimates/0"),
    ]
    for change, code, path in cases:
        result = run(state, "planning_inputs_save", {**base, **change})
        desk.only_issue(result, "invalid", code, path)
        assert SECRET not in json.dumps(result)
    for name, item, limit in (("node_time_limits", limits[0], 64), ("estimates", estimate, 16)):
        desk.only_issue(run(state, "planning_inputs_save", {**base, name: [item] * (limit + 1)}), "invalid",
                        "invalid_value", f"/{name}")
    assert not wpt.planning().planning_inputs_dir(state).exists()


# -- 7. plan_summary references --------------------------------------------------------------------

def test_plan_summary_names_each_node_recipe_and_data(tmp_path, monkeypatch):
    state = tmp_path / "state"
    dataset = wpt.register_data(state, tmp_path)
    heldout = wpt.register_data(state, tmp_path, "heldout")
    recipe = wpt.save_recipe(state, **wpt.TARGETED)
    root = wpt.snapshot_root(state, monkeypatch)
    spec = {**wpt.minimal_bodies()["benchmark_save"]["spec"], "dataset": heldout,
            "tokenizer_fingerprint": root["tokenizer_fingerprint"]}
    benchmark = run(state, "benchmark_save", {"spec": spec})
    assert benchmark["status"] == "ok", benchmark
    benchmark = benchmark["data"]["revision_sha256"]

    def evaluate(node_id, model):
        return {"kind": "evaluate", "id": node_id, "label": node_id, "model": wpt.output(model),
                "benchmark_revision": benchmark, "executor_profile_ids": ["estimate-a"]}

    select = {"kind": "select", "id": "pick", "label": "선택", "selection": {"metric": "accuracy", "direction": "max"},
              "evaluations": [{"node_id": "ea", "output_name": "evaluation"},
                              {"node_id": "eb", "output_name": "evaluation"}]}
    nodes = [wpt.train("a", recipe, wpt.from_root(root)), wpt.train("b", recipe, wpt.from_root(root)),
             evaluate("ea", "a"), evaluate("eb", "b"), select]
    project_id, _ = wpt.make_project(state, nodes, [wpt.binding("train", "train", dataset),
                                                    wpt.binding("held", "validation", heldout)])
    planned = run(state, "graph_plan", wpt.plan_request(project_id, resources=(("a", 1800), ("b", 1800),
                                                                                ("ea", 600), ("eb", 600))))
    assert planned["status"] == "ok", planned
    summary = {node["node_id"]: node for node in planned["data"]["nodes"]}
    recipe_ref = {"recipe_id": "sft-lora", "version": "1", "name": "SFT LoRA", "training_stage": "sft",
                  "training_method": "lora", "revision_sha256": recipe}
    for node_id in ("a", "b"):
        assert summary[node_id]["recipe"] == recipe_ref
        assert summary[node_id]["datasets"] == [{"binding_id": "train", "benchmark_revision": None, **dataset}]
    for node_id in ("ea", "eb"):
        assert summary[node_id]["recipe"] is None
        assert summary[node_id]["datasets"] == [{"binding_id": None, "benchmark_revision": benchmark, **heldout}]
    assert (summary["pick"]["recipe"], summary["pick"]["datasets"]) == (None, [])
    got = run(state, "graph_plan_get", {"plan_sha256": planned["data"]["plan_sha256"]})
    assert got["data"] == {key: value for key, value in planned["data"].items() if key != "warnings"}


# -- 8. legacy recover (Q-K) -----------------------------------------------------------------------

def test_legacy_recover_never_fails_a_graph_attempt_with_a_fresh_heartbeat(tmp_path, monkeypatch, capsys,
                                                                         isolated_environment):
    from tuning_factory.jobs import JobStore
    state = tmp_path / "state"
    w = claimed_world(state, tmp_path, monkeypatch)
    w.store.jobs.heartbeat(w.lease)
    legacy = {"root": str(state), "settings": {}, "action": "recover", "data": {}}
    for age in (0, 60, 100):     # the desktop's "연결 끊긴 작업 확인" uses the legacy 120 s threshold
        wrt.execute(state, "UPDATE jobs SET updated=? WHERE id=?", time.time() - age, w.job_id)
        response, _ = desk.sidecar(monkeypatch, capsys, legacy)
        assert response == {"ok": True, "result": {"recovered": 0}}, age
        assert JobStore(state / "jobs.db").recover() == 0
        job = w.store.jobs.get(w.job_id)
        assert (job["status"], job["error"], job["active_attempt_id"]) == ("RUNNING", None, w.attempt_id)
        assert held(w)
    w.store.jobs.heartbeat(w.lease)     # the attempt keeps beating: still its own
    # Only an expired heartbeat is recovered, into the controller's own FAILED-with-lock (uncertain) state.
    wrt.execute(state, "UPDATE jobs SET updated=? WHERE id=?", time.time() - 600, w.job_id)
    response, _ = desk.sidecar(monkeypatch, capsys, legacy)
    assert response == {"ok": True, "result": {"recovered": 1}}
    job = w.store.jobs.get(w.job_id)
    assert (job["status"], job["active_attempt_id"]) == ("FAILED", w.attempt_id) and held(w)
    assert node_status(state, w)["uncertain"] is True


# -- shared contracts of the 9.8 actions -----------------------------------------------------------

def minimal_bodies():
    return {"graph_release_lock": {"run_id": "0" * 32, "node_id": "train", "job_id": "1" * 32,
                                   "attempt_id": "2" * 32, "confirm_execution_stopped": True},
            "observations_list": {}, "executor_profile_inventory": {"profile_id": "lab"},
            "planning_inputs_get": {"project_id": "p"},
            "planning_inputs_save": {"project_id": "p", "project_revision": "0" * 64}}


@pytest.mark.parametrize("action", ACTIONS_98)
def test_workbench_actions_are_strict_and_take_no_credentials(tmp_path, action):
    wf = desk.workflows()
    state = tmp_path / "state"
    result = run(state, action, {**minimal_bodies()[action], "command": "calc.exe"})
    issue = desk.only_issue(result, "invalid", "extra_field", "")
    assert issue["node_id"] is None and "calc.exe" not in json.dumps(result)
    assert action not in wf.SECRET_SCOPES
    expected = "tuning_factory.workflow_runs" if action == "graph_release_lock" else "tuning_factory.workflow_planning"
    assert wf.ACTION_MODULES[action] == expected
    assert wf.registered_requests()[action].model_config["extra"] == "forbid"
    for name in ("RUNPOD_API_KEY", "HF_TOKEN", "MLFLOW_TRACKING_TOKEN"):
        context = wf.WorkflowContext(state_dir=state, secrets={name: SECRET})
        with pytest.raises(ValueError) as refused:
            wf.dispatch_action(context, action, minimal_bodies()[action])
        assert str(refused.value) == wf.SCOPE_REFUSED and SECRET not in str(refused.value)
    assert not state.exists()


def test_synthetic_secrets_never_reach_workbench_responses_files_or_environment(tmp_path, monkeypatch):
    from test_gpu_servers import FakeServer
    state = tmp_path / "state"
    for name, value in (("MLFLOW_TRACKING_TOKEN", MLFLOW_SECRET), ("MLFLOW_OIDC_CLIENT_SECRET", MLFLOW_SECRET),
                        ("RUNPOD_API_KEY", SECRET)):
        monkeypatch.setenv(name, value)
    environment = dict(os.environ)
    w = claimed_world(state, tmp_path, monkeypatch)
    lose(state, w)
    FakeServer(tmp_path / "remote").install(monkeypatch)
    settings = {**wpt.TOKEN_SETTINGS, "MLFLOW_OIDC_CLIENT_SECRET_saved": True, "RUNPOD_API_KEY_saved": True,
                "HF_TOKEN_saved": True}
    profiles = [w.found.profile, wpt.server_profile()]
    project = desk.call(state, "project_get", {"project_id": w.found.project_id})["data"]
    calls = [("observations_list", {}), ("executor_profile_inventory", {"profile_id": "lab"}),
             ("executor_profile_inventory", {"profile_id": "pod-a"}),
             ("recipe_get", {"revision_sha256": w.found.recipe}),
             ("planning_inputs_save", {"project_id": w.found.project_id, "project_revision": project["revision_sha256"],
                                       "node_time_limits": [{"node_id": "train", "timeout_minutes": 30}],
                                       "estimates": [wpt.ESTIMATE]}),
             ("planning_inputs_get", {"project_id": w.found.project_id}),
             ("graph_run_get", {"run_id": w.run_id}),
             ("graph_release_lock", {"run_id": w.run_id, "node_id": "train", "job_id": w.job_id,
                                     "attempt_id": w.attempt_id, "confirm_execution_stopped": True})]
    responses = [wrt.run(state, action, body, settings=settings, profiles=profiles) for action, body in calls]
    assert [response["status"] for response in responses] == ["ok", "ok", "invalid", "ok", "ok", "ok", "ok", "ok"]
    text = json.dumps(responses)
    assert SECRET not in text and MLFLOW_SECRET not in text
    assert w.found.profile["fields"]["ssh_key_file"] not in text and "test-key" not in text
    stored = wpt.state_bytes(state)
    assert SECRET.encode() not in stored and MLFLOW_SECRET.encode() not in stored
    assert dict(os.environ) == environment


# -- 9.8 review follow-ups ------------------------------------------------------------------------

def test_release_then_canceled_classification_settles_a_canceled_uncertain_attempt(tmp_path, monkeypatch):
    """A canceled attempt left uncertain (launched, its lock kept) is released by the operator; the controller's
    ``classify_attempt('canceled')`` then settles the node CANCELED, as after CLI ``release-lock``."""
    state = tmp_path / "state"
    w = claimed_world(state, tmp_path, monkeypatch)
    w.store.jobs.transition(w.lease, "CANCELED")
    node = node_status(state, w)
    assert (node["job_status"], node["uncertain"], node["needs_operator"]) == ("CANCELED", True, True) and held(w)
    result = release(state, w)
    assert result["status"] == "ok", result
    assert not held(w)
    assert w.store.classify_attempt(w.controller, w.job_id, "canceled") == "canceled"
    node = node_status(state, w)
    assert (node["status"], node["job_status"], node["uncertain"]) == ("CANCELED", "CANCELED", False)
    assert w.store.attempt_disposition(w.job_id)["disposition"] == "canceled"


def test_planning_inputs_save_ignores_draft_nodes_without_a_string_id(tmp_path):
    """``ProjectSpec.graph`` is free JSON: a draft node whose ``id`` is a list or an object is no node a time limit
    can name, and it never crashes the save (9.8 review 1)."""
    state = tmp_path / "state"
    nodes = [{"kind": "train", "id": "a", "label": "학습"}, {"kind": "train", "id": ["a"], "label": "목록"},
             {"kind": "evaluate", "id": {"a": 1}, "label": "객체"}, {"kind": "train", "label": "ID 없음"}]
    project_id, revision = wpt.make_project(state, nodes, [])
    body = {"project_id": project_id, "project_revision": revision, "simulation": True,
            "node_time_limits": [{"node_id": "a", "timeout_minutes": 90}], "estimates": []}
    saved = run(state, "planning_inputs_save", body)
    assert saved["status"] == "ok", saved
    refused = run(state, "planning_inputs_save",
                  {**body, "node_time_limits": [{"node_id": "a", "timeout_minutes": 90},
                                                {"node_id": "b", "timeout_minutes": 90}]})
    desk.only_issue(refused, "invalid", "node_resource_unknown", "/node_time_limits/1/node_id")


def test_fresh_for_real_plan_says_what_graph_plan_decides(tmp_path, monkeypatch):
    """``observations_list``'s ``fresh_for_real_plan`` uses the same rule as ``graph_plan``: the key paths still
    check, the revision is the observed one, and the profile still rebuilds from the current fields. Probe: an SSH key
    path under ``~`` whose home directory changed after the observation (9.8 review 2)."""
    from test_gpu_servers import FakeServer
    state = tmp_path / "state"
    homes = [tmp_path / "home-a", tmp_path / "home-b"]
    for home in homes:
        (home / "keys").mkdir(parents=True)
    for name in ("USERPROFILE", "HOME"):
        monkeypatch.setenv(name, str(homes[0]))
    lab = wpt.server_profile(server_ssh_key_file="~/keys/lab-key")
    found = wpt.scenario(state, tmp_path, monkeypatch, profiles=("lab",))
    FakeServer(tmp_path / "remote").install(monkeypatch)
    inspected = run(state, "executor_profile_inspect", {"profile_id": "lab"}, profiles=[lab], clock=NOW)
    assert inspected["status"] == "ok", inspected

    def decisions():
        listed = run(state, "observations_list", profiles=[lab], clock=NOW + 60)
        [entry] = listed["data"]["observations"]
        planned = wpt.real_plan(state, found, profile=lab, clock=NOW + 60)
        profile_issues = [item["code"] for item in planned["issues"] if item["path"] == "/profiles/lab"]
        return entry, planned, profile_issues

    entry, planned, profile_issues = decisions()
    assert entry["fresh_for_real_plan"] is True and profile_issues == [], planned
    for name in ("USERPROFILE", "HOME"):   # the same fields and revision, but "~" now names another key file
        monkeypatch.setenv(name, str(homes[1]))
    entry, planned, profile_issues = decisions()
    assert entry["profile_current"] is True                        # the revision still matches
    assert profile_issues == ["observation_revision_differs"], planned
    assert entry["fresh_for_real_plan"] is False                    # and the list now says so too


def test_a_retried_submission_heals_a_missing_approval_receipt(tmp_path, monkeypatch):
    """A crash after ``GraphStore.submit`` and before the receipt leaves a run without its approval receipt; the
    retry takes the replay shortcut and writes it then. A further retry writes nothing new (9.8 review 3)."""
    wr = wrt.runs()
    from tuning_factory.registry import canonical
    state = tmp_path / "state"
    found = wrt.real_world(state, tmp_path, monkeypatch)
    write_receipt = wr._write_receipt

    def crash(*args, **kwargs):
        raise RuntimeError("the sidecar stopped before the receipt")

    monkeypatch.setattr(wr, "_write_receipt", crash)
    with pytest.raises(RuntimeError):
        wrt.submit(state, found)
    [(run_id, request_id, status)] = wrt.run_rows(state)
    receipt = wr.submissions_dir(state) / f"{run_id}.json"
    assert (request_id, status) == ("submit-1", "RUNNING") and not receipt.exists()
    monkeypatch.setattr(wr, "_write_receipt", write_receipt)
    retried = wrt.submit(state, found)
    assert retried["status"] == "ok" and retried["data"]["run_id"] == run_id, retried
    created = wrt.ledger(state).get_run(run_id)["created"]
    assert receipt.read_bytes() == canonical({
        "run_id": run_id, "plan_sha256": found.plan_sha256, "request_id": "submit-1", "simulation": False,
        "approve_execution": True, "approve_data_transfer": True, "created": created})
    before = desk.snapshot(state)
    assert wrt.submit(state, found)["data"]["run_id"] == run_id
    assert desk.snapshot(state) == before and len(wpt.files(wr.submissions_dir(state))) == 1
