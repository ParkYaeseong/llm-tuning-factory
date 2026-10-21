"""Task 9.6: project and graph commands of the factory CLI (plan §4 "9.6"; D9, D10, D14).

The CLI calls the desktop's workflow actions through ``desktop_workflows.dispatch_action`` and runs the Task 9.4
controller in the foreground (``controller_entry.serve``). Fakes only: an autouse socket guard, a fake RunPod
controller for the read-only inspection, Hugging Face over ``httpx.MockTransport`` (9.2's ``FakeHub``), and spies on
every provider, SSH and MLflow entry point. Every wait is bounded (``WAIT``). Synthetic secrets are built at runtime,
never committed as literals. No GPU, SSH, network, model download, MLflow write or provider call.
"""
import _thread
import contextlib
import importlib
import json
import os
import pathlib
import signal
import socket
import sqlite3
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import httpx
import pytest
import test_desktop_projects as desk
import test_workflow_planning as wpt
import test_workflow_runs as wrt
import typer.main
from typer.testing import CliRunner

from tuning_factory.cli import app

WAIT = 30   # seconds: the bound of every wait in this module
# Built at runtime: the history-walking secret scan must never see a credential-shaped literal.
RUNPOD_KEY = "synthetic-" + "runpod-cli-" + "k" * 12
RUNPOD_KEY_ENV = "synthetic-" + "runpod-env-" + "k" * 12
MLFLOW_TOKEN = "synthetic-" + "mlflow-cli-" + "t" * 12
SECRETS = (RUNPOD_KEY, RUNPOD_KEY_ENV, MLFLOW_TOKEN)
# Independent of the implementation: every credential variable the CLI process may hold (9.4's list).
CREDENTIAL_NAMES = ("RUNPOD_API_KEY", "MLFLOW_TRACKING_URI", "MLFLOW_TRACKING_TOKEN", "MLFLOW_TRACKING_USERNAME",
                    "MLFLOW_TRACKING_PASSWORD", "MLFLOW_OIDC_TOKEN_URL", "MLFLOW_OIDC_CLIENT_ID",
                    "MLFLOW_OIDC_CLIENT_SECRET", "FACTORY_MLFLOW_ARTIFACT_LOCATION", "FACTORY_OPERATOR",
                    "S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY", "S3_SESSION_TOKEN", "AWS_ACCESS_KEY_ID",
                    "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "HF_TOKEN", "HUGGING_FACE_HUB_TOKEN")
GRAPH_COMMANDS = ("project-list", "executor-profile-inspect", "graph-validate", "graph-plan", "graph-submit",
                  "graph-status", "graph-pause", "graph-resume", "graph-cancel", "graph-rearm-tracking",
                  "graph-work")
REQUEST = {"node_resources": [{"node_id": "train", "timeout_seconds": 1800}]}
# The v1 command set and every flag, pinned before Task 9.6 (plan §1 "Legacy stays frozen"):
# (kind, name, option strings..., type, required, default).
LEGACY = {
    "": [("option", "state_dir", "--state-dir", "path", False, "state"),
         ("option", "policy_file", "--policy-file", "path", False, None),
         ("option", "tracking_uri", "--tracking-uri", "str", False, None),
         ("option", "connection_file", "--connection-file", "path", False, None),
         ("option", "env_file", "--env-file", "path", False, ".env")],
    "doctor": [("option", "pod_id", "--pod-id", "str", False, None),
               ("option", "write_test", "--write-test", "--no-write-test", "boolean", False, False),
               ("option", "server_profile", "--server-profile", "path", False, None)],
    "server-inspect": [("argument", "profile", "profile", "path", True, None)],
    "server-runtime": [("argument", "profile", "profile", "path", True, None),
                       ("option", "prepare", "--prepare", "--no-prepare", "boolean", False, False),
                       ("option", "approve_preparation", "--approve-preparation", "--no-approve-preparation",
                        "boolean", False, False),
                       ("option", "training_stage", "--training-stage", "str", False, "sft")],
    "server-runtime-status": [],
    "server-runtime-release": [("argument", "profile", "profile", "path", True, None),
                               ("argument", "reservation_id", "reservation_id", "str", True, None),
                               ("option", "confirm_preparation_stopped", "--confirm-preparation-stopped",
                                "--no-confirm-preparation-stopped", "boolean", False, False)],
    "dataset-register": [("argument", "dataset_id", "dataset_id", "str", True, None),
                         ("argument", "version", "version", "str", True, None),
                         ("argument", "source", "source", "path", True, None),
                         ("option", "export_allowed", "--export-allowed", "--no-export-allowed", "boolean", False,
                          False),
                         ("option", "synthetic", "--synthetic", "--no-synthetic", "boolean", False, False),
                         ("option", "kind", "--kind", "str", False, "instruction")],
    "validate": [("argument", "config", "config", "path", True, None)],
    "plan": [("argument", "config", "config", "path", True, None),
             ("option", "real", "--real", "--no-real", "boolean", False, False),
             ("option", "server_profile", "--server-profile", "path", False, None)],
    "run": [("argument", "config", "config", "path", True, None),
            ("option", "mock", "--mock", "--no-mock", "boolean", False, False),
            ("option", "submit", "--submit", "--no-submit", "boolean", False, False),
            ("option", "real", "--real", "--no-real", "boolean", False, False),
            ("option", "request_key", "--request-key", "str", False, None),
            ("option", "priority", "--priority", "int", False, 0),
            ("option", "server_profile", "--server-profile", "path", False, None)],
    "batch": [("argument", "configs", "configs", "path", True, None),
              ("option", "submit", "--submit", "--no-submit", "boolean", False, False),
              ("option", "real", "--real", "--no-real", "boolean", False, False)],
    "work": [("option", "drain", "--drain", "--no-drain", "boolean", False, False),
             ("option", "pod_id", "--pod-id", "str", False, None),
             ("option", "host", "--host", "str", False, None),
             ("option", "port", "--port", "int", False, 22),
             ("option", "username", "--username", "str", False, "root"),
             ("option", "key_file", "--key-file", "path", False, None),
             ("option", "approve_execution_and_transfer", "--approve-execution-and-transfer",
              "--no-approve-execution-and-transfer", "boolean", False, False),
             ("option", "server_profile", "--server-profile", "path", False, None)],
    "status": [("option", "job_id", "--job-id", "str", False, None)],
    "logs": [("argument", "job_id", "job_id", "str", True, None)],
    "cancel": [("argument", "job_id", "job_id", "str", True, None)],
    "resume": [("argument", "job_id", "job_id", "str", True, None),
               ("option", "checkpoint", "--checkpoint", "path", False, None)],
    "recover": [("option", "stale_after_seconds", "--stale-after-seconds", "int", False, 120)],
    "reconcile": [("argument", "job_id", "job_id", "str", True, None),
                  ("argument", "pod_id", "pod_id", "str", False, None),
                  ("argument", "host", "host", "str", False, None),
                  ("argument", "key_file", "key_file", "path", False, None),
                  ("option", "port", "--port", "int", False, 22),
                  ("option", "server_profile", "--server-profile", "path", False, None)],
    "release-lock": [("argument", "job_id", "job_id", "str", True, None),
                     ("argument", "attempt_id", "attempt_id", "str", True, None),
                     ("option", "confirm_execution_stopped", "--confirm-execution-stopped",
                      "--no-confirm-execution-stopped", "boolean", False, False)],
    "compare": [("argument", "job_id", "job_id", "str", True, None),
                ("argument", "baseline_id", "baseline_id", "str", True, None)],
    "evaluate": [("argument", "predictions", "predictions", "path", True, None),
                 ("option", "kind", "--kind", "str", False, "classification")],
    "pod-create": [("argument", "payload", "payload", "path", True, None),
                   ("option", "approve_paid_creation", "--approve-paid-creation", "--no-approve-paid-creation",
                    "boolean", False, False),
                   ("option", "hourly_quote_usd", "--hourly-quote-usd", "float", False, 0),
                   ("option", "timeout_seconds", "--timeout-seconds", "int", False, 3600)],
    "pod-start": [("argument", "pod_id", "pod_id", "str", True, None),
                  ("option", "approve_paid_start", "--approve-paid-start", "--no-approve-paid-start", "boolean",
                   False, False)],
    "pod-stop": [("argument", "pod_id", "pod_id", "str", True, None)],
    "guard": [("option", "watch", "--watch", "--no-watch", "boolean", False, False),
              ("option", "interval_seconds", "--interval-seconds", "int", False, 15)],
}
# Path options that refer to a credential file or a file next to one (plan §4 "9.6" test 8), and the v1 run's
# idempotency key, which is not a credential.
PATH_OPTIONS = ("executor_profiles", "key_file", "policy_file", "env_file", "connection_file", "server_profile")
NOT_A_CREDENTIAL = ("request_key",)
CREDENTIAL_WORDS = ("key", "token", "secret", "password", "passwd", "credential", "credentials", "apikey")


def cli_module():
    # A missing implementation is a test failure, rather than a collection error.
    module = importlib.import_module("tuning_factory.cli")
    for name in ("CliProfile", "CliProfiles", "cli_context", "emit", "tracking_view"):
        if not hasattr(module, name):
            pytest.fail(f"tuning_factory.cli.{name} (Task 9.6) is missing")
    return module


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """The CLI is tested with fakes only: no RunPod API, SSH, Hugging Face or MLflow connection."""
    def refuse(*args, **kwargs):
        raise AssertionError("CLI graph tests must not open a network connection")
    monkeypatch.setattr(socket.socket, "connect", refuse)


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch):
    """The CLI loads ``--env-file`` into the process environment and ``graph-work`` applies the one MLflow scope:
    restore every credential variable afterwards."""
    for name in (*CREDENTIAL_NAMES, "MLFLOW_HTTP_REQUEST_MAX_RETRIES", "MLFLOW_HTTP_REQUEST_TIMEOUT",
                 "MLFLOW_SUPPRESS_PRINTING_URL_TO_STDOUT"):
        monkeypatch.setenv(name, "x")   # registers the teardown even for variables that start absent
        monkeypatch.delenv(name)
    monkeypatch.setattr("tuning_factory.auth.configure_oidc", lambda: None)   # never an OIDC refresher thread


# -- helpers -------------------------------------------------------------------------------------

class Cli:
    """``factory --state-dir STATE --env-file ENV ...`` through ``CliRunner``; every argv and result is kept for the
    secret scans."""

    def __init__(self, state, env, charset="utf-8"):
        self.state, self.env, self.charset, self.calls = Path(state), Path(env), charset, []

    def __call__(self, *args):
        argv = ["--state-dir", str(self.state), "--env-file", str(self.env), *map(str, args)]
        result = CliRunner(charset=self.charset).invoke(app, argv)
        self.calls.append((argv, result))
        return result

    def captured(self) -> bytes:
        return b"".join(result.stdout_bytes + result.stderr_bytes for _, result in self.calls)


def env_file(tmp_path, name="factory.env", **values):
    path = Path(tmp_path) / name
    path.write_text("".join(f"{key}={value}\n" for key, value in values.items()), encoding="utf-8")
    return path


def parsed(result):
    assert result.exception is None or isinstance(result.exception, SystemExit), result.exception
    return json.loads(result.stdout)


def final_line(result, exit_code):
    """``graph-work``'s last stdout line, which every outcome prints (ASCII JSON)."""
    assert result.exit_code == exit_code, result.output
    assert result.stdout_bytes.isascii(), result.stdout
    last = lines(result)[-1]
    assert set(last) - {"issues"} == {"ok", "exit_code", "code", "message", "controller"}, last
    assert last["exit_code"] == exit_code and last["ok"] is (exit_code == 0)
    return last


def lines(result):
    return [json.loads(line) for line in result.stdout.splitlines() if line.strip()]


def flat(text):
    """Typer's error panel without its box: words joined by single spaces, so a wrapped message reads whole."""
    return " ".join(word for word in text.split() if any(char.isalnum() for char in word))


def codes(result):
    return [item["code"] for item in parsed(result)["issues"]]


def refused(result, code, path=None, status="invalid"):
    assert result.exit_code == 1, result.output
    body = parsed(result)
    assert body["status"] == status, body
    found = [item for item in body["issues"] if item["code"] == code]
    assert found, body
    if path is not None:
        assert path in [item["path"] for item in found], body
    return body


def request_file(tmp_path, body, name="plan-request.json"):
    path = Path(tmp_path) / name
    path.write_text(json.dumps(body), encoding="utf-8")
    return path


def profiles_file(tmp_path, *profiles, name="profiles.json", raw=None):
    directory = Path(tmp_path) / "profiles"
    directory.mkdir(exist_ok=True)
    path = directory / name
    path.write_text(raw if raw is not None else json.dumps({"schema_version": 1, "profiles": list(profiles)}),
                    encoding="utf-8")
    return path


def runpod_entry(tmp_path, profile_id="pod-a", *, credential_file="pod-a.env", key=RUNPOD_KEY, **fields):
    """A profiles-file entry with 9.2's RunPod fields; the key lives only in the referenced credential file."""
    entry = {"profile_id": profile_id, "fields": wpt.runpod_profile(tmp_path, profile_id, **fields)["fields"]}
    if credential_file is not None:
        entry["credential_file"] = credential_file
        if key is not None:
            directory = Path(tmp_path) / "profiles"
            directory.mkdir(exist_ok=True)
            (directory / credential_file).write_text(f"# RunPod\nRUNPOD_API_KEY={key}\n", encoding="utf-8")
    return entry


def owner_row(state):
    from tuning_factory.graph_store import GraphStore
    return GraphStore(Path(state) / "jobs.db").owner_state()


def run_status(state, run_id):
    from tuning_factory.graph_controller import graph_status
    return graph_status(wrt.ledger(state), run_id, now=time.time())["runs"][0]


def current_json(state):
    return json.loads((Path(state) / "controller" / "current.json").read_bytes())


def scanned(directory):
    directory = Path(directory)
    return {path.relative_to(directory).as_posix(): path.read_bytes()
            for path in sorted(directory.rglob("*")) if path.is_file()}


def provider_spies(monkeypatch, *, executor_for=None, mlflow_tracker=None):
    """Every way to reach a provider, SSH host, HF or MLflow records the call and fails. (``LazyMLflowTracker`` is
    allowed: the v1 callback builds one lazily, and building it contacts nothing.)"""
    import paramiko

    from tuning_factory import desktop_runtime, execution, tracking
    calls = []

    def spy(name):
        def record(*args, **kwargs):
            calls.append(name)
            raise AssertionError(f"{name} must not be reached here")
        return record

    monkeypatch.setattr(execution, "RunPodController", wpt.REAL_RUNPOD_CONTROLLER)   # the inspection fake is gone
    for method in ("__init__", "request", "status", "create", "start", "stop", "delete"):
        monkeypatch.setattr(execution.RunPodController, method, spy(f"RunPodController.{method}"))
    monkeypatch.setattr(desktop_runtime, "connection", spy("desktop_runtime.connection"))
    monkeypatch.setattr(desktop_runtime, "executor_for", executor_for or spy("desktop_runtime.executor_for"))
    monkeypatch.setattr(paramiko.SSHClient, "connect", spy("paramiko.connect"))
    monkeypatch.setattr(httpx.Client, "send", spy("httpx.send"))
    monkeypatch.setattr(tracking.MLflowTracker, "__init__", mlflow_tracker or spy("MLflowTracker"))
    return calls


# -- worlds ----------------------------------------------------------------------------------------

def simulation_world(tmp_path, monkeypatch, env=None):
    """9.2's scenario (one train node on a snapshotted HF root, ``lora_target: q_proj,v_proj``, an estimate) and a
    stored dry plan, with a CLI bound to its state directory."""
    state = tmp_path / "state"
    found = wrt.simulation_world(state, tmp_path, monkeypatch)
    found.state, found.cli = state, Cli(state, env or env_file(tmp_path))
    return found


def submitted_simulation(tmp_path, monkeypatch):
    found = simulation_world(tmp_path, monkeypatch)
    result = found.cli("graph-submit", found.plan_sha256, "--request-id", "sim-1")
    assert result.exit_code == 0, result.output
    found.run_id = parsed(result)["data"]["run_id"]
    return found


def real_world(tmp_path, monkeypatch):
    """A real RunPod plan made through the CLI: the profiles file references a credential file, MLflow comes from
    the env file, the inspection uses a fake RunPod controller (read-only ``status``), and planning is offline."""
    state = tmp_path / "state"
    found = wpt.scenario(state, tmp_path, monkeypatch, profiles=("pod-a",))
    env = env_file(tmp_path, MLFLOW_TRACKING_URI="https://mlflow.invalid", MLFLOW_TRACKING_TOKEN=MLFLOW_TOKEN)
    found.state, found.cli = state, Cli(state, env)
    found.profiles = profiles_file(tmp_path, runpod_entry(tmp_path))
    found.fake = wpt.FakeRunPod().install(monkeypatch)
    inspected = found.cli("executor-profile-inspect", "pod-a", "--executor-profiles", found.profiles)
    assert inspected.exit_code == 0, inspected.output
    found.inspected = parsed(inspected)
    request = request_file(tmp_path, REQUEST)
    planned = found.cli("graph-plan", found.project_id, "--request", request, "--executor-profiles", found.profiles,
                        "--real")
    assert planned.exit_code == 0, planned.output
    found.plan = parsed(planned)["data"]
    found.plan_sha256 = found.plan["plan_sha256"]
    return found


def submitted_real(tmp_path, monkeypatch):
    found = real_world(tmp_path, monkeypatch)
    result = found.cli("graph-submit", found.plan_sha256, "--request-id", "real-1", "--approve-execution",
                       "--approve-data-transfer", "--executor-profiles", found.profiles)
    assert result.exit_code == 0, result.output
    found.run_id = parsed(result)["data"]["run_id"]
    return found


# 1
def test_cli_project_list_prints_projects(tmp_path):
    cli_module()
    state = tmp_path / "state"
    cli = Cli(state, env_file(tmp_path))
    empty = cli("project-list")
    assert empty.exit_code == 0, empty.output
    assert parsed(empty) == {"status": "ok", "issues": [], "data": {"projects": []}}
    project_id, revision = wpt.make_project(state, [], [])
    listed = cli("project-list")
    assert listed.exit_code == 0, listed.output
    body = parsed(listed)
    assert body == {"status": "ok", "issues": [], "data": desk.call(state, "projects_list")["data"]}   # same action
    assert [(item["project_id"], item["revision_sha256"], item["name"]) for item in body["data"]["projects"]] == [
        (project_id, revision, "효소 기능 요약")]
    assert listed.stdout.endswith("\n") and "효소 기능 요약" in listed.stdout   # readable JSON, not \u escapes


# 2
def test_cli_graph_plan_defaults_to_a_dry_run_and_stores_the_plan(tmp_path, monkeypatch):
    cli_module()
    found = simulation_world(tmp_path, monkeypatch)
    cli, state = found.cli, found.state
    validated = cli("graph-validate", found.project_id)
    assert validated.exit_code == 0, validated.output
    assert parsed(validated)["data"] == {"project_id": found.project_id, "project_revision": found.revision,
                                         "node_count": 1}
    refused(cli("graph-validate", found.project_id, "--revision", "0" * 64), "unknown_definition", "/revision")
    request = request_file(tmp_path, {**REQUEST, "estimates": [wpt.ESTIMATE]})
    planned = cli("graph-plan", found.project_id, "--request", request)
    assert planned.exit_code == 0, planned.output
    summary = parsed(planned)["data"]
    assert summary["simulation"] is True and summary["project_id"] == found.project_id
    assert summary["nodes"][0]["allowed"][0]["profile_id"] == "estimate-a" and summary["datasets"]
    assert (state / "workflows" / "plans" / f"{summary['plan_sha256']}.json").is_file()
    assert wpt.run(state, "graph_plan_get", {"plan_sha256": summary["plan_sha256"]})["data"] == summary
    assert wrt.run_rows(state) == []   # planning submits nothing
    # --real without profiles or tracking: exit 1 with the planning issues, and no plan.
    plans = sorted((state / "workflows" / "plans").iterdir())
    real = cli("graph-plan", found.project_id, "--request", request, "--real")
    body = refused(real, "tracking_required", "/tracking")
    assert {"estimate_real", "tracking_required"} <= set(codes(real)), body
    assert sorted((state / "workflows" / "plans").iterdir()) == plans
    # The request file never chooses the project or a real plan: the argument and --real do.
    for extra, path in (({"project_id": found.project_id}, "/project_id"), ({"simulation": False}, "/simulation"),
                        ({"simulation": True}, "/simulation")):
        refused(cli("graph-plan", found.project_id, "--request", request_file(tmp_path, {**REQUEST, **extra})),
                "extra_field", path)
    refused(cli("graph-plan", found.project_id, "--request", request_file(tmp_path, {**REQUEST, "command": "x"})),
            "extra_field", "")
    # An unknown key is never echoed, not even in a pointer: the issue points at the object that holds it.
    hidden = {"node_resources": [{**REQUEST["node_resources"][0], RUNPOD_KEY: 1}], MLFLOW_TOKEN: 2}
    result = cli("graph-plan", found.project_id, "--request", request_file(tmp_path, hidden))
    body = refused(result, "extra_field")
    assert sorted(item["path"] for item in body["issues"]) == ["", "/node_resources/0"], body
    assert RUNPOD_KEY not in result.output and MLFLOW_TOKEN not in result.output
    for raw in ("[1, 2]", "{not json", ""):
        path = tmp_path / "broken-request.json"
        path.write_text(raw, encoding="utf-8")
        refused(cli("graph-plan", found.project_id, "--request", path), "request_file_invalid")
    refused(cli("graph-plan", found.project_id, "--request", tmp_path / "missing.json"), "request_file_invalid")


# 3
def test_cli_graph_submit_requires_hash_and_separate_approvals(tmp_path, monkeypatch):
    wr = wrt.runs()
    found = real_world(tmp_path, monkeypatch)
    cli, state = found.cli, found.state
    assert found.plan["simulation"] is False and found.fake.keys == [RUNPOD_KEY]
    base = ("graph-submit", found.plan_sha256, "--request-id", "real-1", "--executor-profiles", found.profiles)
    body = refused(cli(*base), "approval_required")
    assert [(item["code"], item["path"], item["message"]) for item in body["issues"]] == [
        ("approval_required", "/approve_execution", wr.APPROVAL_REQUIRED),
        ("approval_required", "/approve_data_transfer", wr.APPROVAL_REQUIRED)]
    refused(cli(*base, "--approve-execution"), "approval_required", "/approve_data_transfer")
    refused(cli(*base, "--approve-data-transfer"), "approval_required", "/approve_execution")
    assert codes(cli(*base, "--approve-execution")) == ["approval_required"]
    refused(cli("graph-submit", "0" * 64, "--request-id", "real-1", "--approve-execution", "--approve-data-transfer"),
            "plan_unknown", "/plan_sha256")
    # Without the profiles file the real plan's pinned profile is gone: a stale plan (409), never a run.
    refused(cli("graph-submit", found.plan_sha256, "--request-id", "real-1", "--approve-execution",
                "--approve-data-transfer"), "plan_stale", "/profiles/pod-a", status="conflict")
    assert wrt.run_rows(state) == []
    result = cli(*base, "--approve-execution", "--approve-data-transfer")
    assert result.exit_code == 0, result.output
    data = parsed(result)["data"]
    assert len(data["run_id"]) == 32 and int(data["run_id"], 16) >= 0
    assert (data["status"], data["simulation"], data["plan_sha256"]) == ("RUNNING", False, found.plan_sha256)
    assert data["controller"]["state"] == "absent"
    again = cli(*base, "--approve-execution", "--approve-data-transfer")   # idempotent by request ID
    assert parsed(again)["data"]["run_id"] == data["run_id"]
    receipt = json.loads((state / "workflows" / "submissions" / f"{data['run_id']}.json").read_bytes())
    assert receipt["approve_execution"] is True and receipt["approve_data_transfer"] is True
    for value in SECRETS:
        assert value.encode() not in cli.captured()


# 4
def test_cli_graph_status_pause_resume_cancel(tmp_path, monkeypatch):
    cli_module()
    found = submitted_simulation(tmp_path, monkeypatch)
    cli, state, run_id = found.cli, found.state, found.run_id
    listed = cli("graph-status")
    assert listed.exit_code == 0, listed.output
    body = parsed(listed)
    assert body == {"status": "ok", "issues": [], "data": wpt.run(state, "graph_runs_list")["data"]}   # same action
    assert [run["run_id"] for run in body["data"]["runs"]] == [run_id]
    assert body["data"]["controller"] == {"owner_id": None, "state": "absent", "heartbeat_age_seconds": None}
    one = parsed(cli("graph-status", run_id))["data"]
    assert set(one) == {"run", "controller", "plan", "tracking_failures"}
    assert one["run"]["run_id"] == run_id and one["run"]["status"] == "RUNNING"
    assert [node["node_id"] for node in one["run"]["nodes"]] == ["train"]
    assert one["plan"]["plan_sha256"] == found.plan_sha256 and one["controller"]["state"] == "absent"
    for command, status in (("graph-pause", "PAUSED"), ("graph-pause", "PAUSED"), ("graph-resume", "RUNNING"),
                            ("graph-cancel", "CANCELED")):
        result = cli(command, run_id)
        assert result.exit_code == 0, result.output
        data = parsed(result)["data"]
        assert set(data) == {"run", "controller"} and data["run"]["status"] == status
    body = refused(cli("graph-resume", run_id), "run_state", "/run_id", status="conflict")
    assert body["issues"][0]["message"] == "canceled graph cannot resume"
    refused(cli("graph-pause", run_id), "run_state", status="conflict")
    refused(cli("graph-status", "f" * 32), "run_unknown", "/run_id")
    refused(cli("graph-pause", "not-a-run"), "invalid_value", "/run_id")
    refused(cli("graph-rearm-tracking", "0" * 64), "tracking_event_unknown", "/event_id")


# 5
def test_cli_graph_work_runs_simulated_runs_without_network(tmp_path, monkeypatch):
    cli_module()
    found = submitted_simulation(tmp_path, monkeypatch)
    calls = provider_spies(monkeypatch)
    started = time.monotonic()
    result = found.cli("graph-work", "--max-ticks", "40", "--tick-seconds", "0.05")
    assert time.monotonic() - started < WAIT
    assert result.exit_code == 0, result.output
    ready, final = lines(result)[0], lines(result)[-1]
    assert set(ready) == {"ok", "owner_id", "pid"} and ready["ok"] is True and ready["pid"] == os.getpid()
    assert ready["owner_id"].startswith(f"cli.{os.getpid()}.")
    assert (final["ok"], final["exit_code"], final["code"]) == (True, 0, "stopped")
    assert wrt.ledger(found.state).get_run(found.run_id)["status"] == "COMPLETED"
    owner = owner_row(found.state)
    assert owner["owner_id"] == ready["owner_id"] and owner["expires"] == 0   # drained and released
    current = current_json(found.state)
    assert current["owner_id"] == ready["owner_id"] and current["code"] == "stopped" and current["stopped_at"]
    assert current["profiles"] == {} and current["tracking_settings_sha256"] is None
    assert final["controller"] == current
    assert calls == []


# 6
def test_cli_graph_work_refuses_a_second_controller(tmp_path, monkeypatch):
    cli = cli_module()
    from tuning_factory.graph_store import OWNER_HELD, GraphStore
    state = tmp_path / "state"
    factory = Cli(state, env_file(tmp_path))
    held = GraphStore(state / "jobs.db").acquire_owner("test.held", ttl_seconds=600)
    before = owner_row(state)
    result = factory("graph-work", "--max-ticks", "1", "--tick-seconds", "0.05")
    assert result.exit_code == 3, result.output
    assert lines(result)[0] == {"ok": False, "code": "owner_held"}
    final = lines(result)[-1]
    assert (final["ok"], final["exit_code"], final["code"], final["message"]) == (False, 3, "owner_held", OWNER_HELD)
    assert OWNER_HELD == "controller already running" and final["controller"] is None
    assert owner_row(state) == before and not (state / "controller" / "current.json").exists()
    GraphStore(state / "jobs.db").release_owner(held)
    # One controller per process, whatever its state directory (Task 9.4 note 1: one MLflow scope per process).
    assert cli._CONTROLLER.acquire(blocking=False)
    try:
        busy = Cli(tmp_path / "other", factory.env)("graph-work", "--max-ticks", "1", "--tick-seconds", "0.05")
    finally:
        cli._CONTROLLER.release()
    final = final_line(busy, 2)
    assert (final["code"], final["message"], final["controller"]) == ("controller_in_process", cli.ONE_CONTROLLER,
                                                                      None)
    assert not (tmp_path / "other" / "controller").exists()


# 7
def test_cli_graph_work_without_approval_leaves_real_runs_waiting(tmp_path, monkeypatch):
    cli = cli_module()
    from tuning_factory.executor_profiles import PROFILE_MISSING
    found = submitted_real(tmp_path, monkeypatch)
    calls = provider_spies(monkeypatch)
    # Executor profiles are used only with the approval flag; alone they are refused, not silently ignored.
    alone = found.cli("graph-work", "--executor-profiles", found.profiles, "--max-ticks", "1")
    final = final_line(alone, 2)
    assert (final["code"], final["message"]) == ("approval_required", cli.PROFILES_REQUIRE_APPROVAL)
    assert owner_row(found.state) is None
    result = found.cli("graph-work", "--max-ticks", "4", "--tick-seconds", "0.05")
    assert result.exit_code == 0, result.output
    node = run_status(found.state, found.run_id)["nodes"][0]
    assert node["status"] in ("PENDING", "READY") and PROFILE_MISSING in node["wait_reason"], node
    current = current_json(found.state)
    assert current["profiles"] == {} and current["tracking_settings_sha256"] is None   # no tracker, no profile
    assert current["tracking"]["effective_auth_mode"] == "none"
    assert calls == []
    assert found.fake.calls == [("status", "pod1")]   # the inspection's one read-only GET, before graph-work
    assert "RUNPOD_API_KEY" not in os.environ and "MLFLOW_TRACKING_TOKEN" not in os.environ   # popped, no scope


# 8
def test_cli_options_never_take_credential_values():
    cli_module()
    from tuning_factory.desktop_workflows import command_like_field
    group = typer.main.get_command(app)
    assert set(GRAPH_COMMANDS) <= set(group.commands)
    params = [("", param) for param in group.params]
    params += [(name, param) for name, command in group.commands.items() for param in command.params]
    for command, param in params:
        words = {word.casefold() for word in param.name.replace("-", "_").split("_")}
        assert not command_like_field(param.name), (command, param.name)
        if words & set(CREDENTIAL_WORDS) and param.name not in NOT_A_CREDENTIAL:
            assert param.name in PATH_OPTIONS and param.type.name == "path", (command, param.name)
    names = {param.name for _, param in params}
    assert "executor_profiles" in names


# 9
def test_cli_profiles_file_refuses_inline_secrets(tmp_path, monkeypatch):
    cli = cli_module()
    from tuning_factory.desktop_workflows import CREDENTIALS_REFUSED, LOCAL_FILE_ONLY
    state = tmp_path / "state"
    factory = Cli(state, env_file(tmp_path, RUNPOD_API_KEY=RUNPOD_KEY_ENV))
    fake = wpt.FakeRunPod().install(monkeypatch)
    inline = runpod_entry(tmp_path, credential_file=None)
    inline["fields"] = {**inline["fields"], "RUNPOD_API_KEY": RUNPOD_KEY}
    nested = {**runpod_entry(tmp_path, credential_file=None), "RUNPOD_API_KEY": RUNPOD_KEY}
    secrets = {**runpod_entry(tmp_path, credential_file=None), "secrets": {"RUNPOD_API_KEY": RUNPOD_KEY}}
    server = {**wpt.server_profile(), "credential_file": "pod-a.env"}
    server.pop("RUNPOD_API_KEY_saved", None)
    cases = [
        (profiles_file(tmp_path, inline, name="inline.json"), "credential_field", "/profiles/0"),
        (profiles_file(tmp_path, nested, name="nested.json"), "extra_field", "/profiles/0"),
        (profiles_file(tmp_path, secrets, name="secrets.json"), "extra_field", "/profiles/0"),
        (profiles_file(tmp_path, {**runpod_entry(tmp_path, credential_file=None), RUNPOD_KEY: "x"}, name="key.json"),
         "extra_field", "/profiles/0"),
        (profiles_file(tmp_path, {"profile_id": "pod-a", "fields": {RUNPOD_KEY: 1}}, name="value.json"),
         "invalid_value", "/profiles/0/fields"),
        (profiles_file(tmp_path, name="top.json", raw=json.dumps({"profiles": [], RUNPOD_KEY: 1})), "extra_field", ""),
        # An unknown key spelled like a field of a nested model still points at the object holding it.
        (profiles_file(tmp_path, name="misplaced.json", raw=json.dumps({"profiles": [], "profile_id": "x"})),
         "extra_field", ""),
        (profiles_file(tmp_path, server, name="server.json"), "invalid_value", "/profiles/0"),
        (profiles_file(tmp_path, runpod_entry(tmp_path), runpod_entry(tmp_path, "POD-A.", name="Other"),
                       name="duplicate.json"), "invalid_value", ""),
        (profiles_file(tmp_path, name="version.json", raw=json.dumps({"schema_version": 2, "profiles": []})),
         "invalid_value", "/schema_version"),
        (profiles_file(tmp_path, name="broken.json", raw="{" + RUNPOD_KEY), "profiles_file_invalid", ""),
        (profiles_file(tmp_path, name="list.json", raw="[]"), "profiles_file_invalid", ""),
        (tmp_path / "profiles" / "missing.json", "profiles_file_invalid", ""),
        (profiles_file(tmp_path, runpod_entry(tmp_path, credential_file="absent.env", key=None), name="absent.json"),
         "credential_file_unreadable", "/profiles/0/credential_file"),
        (profiles_file(tmp_path, runpod_entry(tmp_path, credential_file="//server/share/pod.env", key=None),
                       name="unc.json"), "local_file_only", "/profiles/0/credential_file"),
    ]
    (tmp_path / "profiles" / "empty.env").write_text("# no key here\n", encoding="utf-8")
    cases.append((profiles_file(tmp_path, runpod_entry(tmp_path, credential_file="empty.env", key=None),
                                name="empty.json"), "credential_missing", "/profiles/0/credential_file"))
    for path, code, pointer in cases:
        for command in (("executor-profile-inspect", "pod-a"), ("graph-plan", "p1", "--request",
                                                                 request_file(tmp_path, REQUEST))):
            result = factory(*command, "--executor-profiles", path)
            body = refused(result, code, pointer)
            if code == "credential_field":
                assert CREDENTIALS_REFUSED in body["issues"][0]["message"]
            if code == "local_file_only":
                assert body["issues"][0]["message"] == LOCAL_FILE_ONLY
            if code in ("credential_file_unreadable", "credential_missing", "local_file_only"):
                break   # only the inspection reads a credential file; planning needs public fields only
    assert fake.calls == [] and wrt.run_rows(state) == []
    absent = next(path for path, code, _ in cases if code == "credential_file_unreadable")
    result = factory("graph-work", "--executor-profiles", absent, "--approve-execution-and-transfer", "--max-ticks",
                     "1")
    final = final_line(result, 2)   # refused before the start, as the sidecar's invalid start (exit 2)
    assert (final["code"], final["controller"]) == ("invalid_start", None)
    assert [(item["code"], item["path"]) for item in final["issues"]] == [
        ("credential_file_unreadable", "/profiles/0/credential_file")]
    assert owner_row(state) is None and not (state / "controller").exists()   # refused before any controller
    for value in SECRETS:
        assert value.encode() not in factory.captured()
    # A credential file is read for the one profile inspected, and its value is never printed.
    good = profiles_file(tmp_path, runpod_entry(tmp_path), runpod_entry(tmp_path, "pod-env", credential_file=None,
                                                                         name="Env key"), name="good.json")
    assert RUNPOD_KEY.encode() not in good.read_bytes()
    result = factory("executor-profile-inspect", "pod-a", "--executor-profiles", good)
    assert result.exit_code == 0, result.output
    data = parsed(result)["data"]
    assert data["profile"]["profile_id"] == "pod-a" and data["revision"] == wpt.planning().profile_revision(
        "pod-a", runpod_entry(tmp_path)["fields"])
    assert fake.keys == [RUNPOD_KEY]   # the file's key, as a value; the env key was not used for this profile
    # Without a credential file the key comes from the existing .env/env (D10).
    result = factory("executor-profile-inspect", "pod-env", "--executor-profiles", good)
    assert result.exit_code == 0, result.output
    assert fake.keys == [RUNPOD_KEY, RUNPOD_KEY_ENV]
    for value in SECRETS:
        assert value.encode() not in factory.captured()
        assert all(value not in argument for argv, _ in factory.calls for argument in argv)
        assert all(value.encode() not in data for data in scanned(state).values())
    # The models behind the file refuse credentials without echoing them.
    with pytest.raises(ValueError) as error:
        cli.CliProfiles.model_validate({"schema_version": 1, "profiles": [inline]})
    assert RUNPOD_KEY not in str(error.value)


# 10
def test_existing_cli_commands_are_unchanged():
    """Plan §1 "Legacy stays frozen": every v1 command keeps its flags (``tests/test_runner_cli.py`` and the other
    CLI tests pass unchanged in the same run), and ``--help`` still lists every one of them."""
    cli_module()
    group = typer.main.get_command(app)

    def normalized(value):
        return str(value) if isinstance(value, pathlib.PurePath) else value

    def signature(param):
        return (param.param_type_name, param.name, *param.opts, *param.secondary_opts, param.type.name,
                param.required, normalized(param.default))

    assert [signature(param) for param in group.params
            if param.name not in ("install_completion", "show_completion")] == LEGACY[""]
    for name, expected in LEGACY.items():
        if name:
            assert [signature(param) for param in group.commands[name].params] == expected, name
    assert set(group.commands) == set(LEGACY) - {""} | set(GRAPH_COMMANDS)
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0, result.output
    listed = set(flat(result.output).split())
    for name in [*(name for name in LEGACY if name), *GRAPH_COMMANDS]:
        assert name in listed, name


# 11 (D11 parity: the CLI plans the destination hash its own controller reports)
@pytest.mark.parametrize("environment", [
    {"MLFLOW_TRACKING_URI": "https://mlflow.invalid", "MLFLOW_TRACKING_TOKEN": "token"},
    {"MLFLOW_TRACKING_URI": "https://mlflow.invalid", "MLFLOW_OIDC_TOKEN_URL": "https://idp.invalid/token",
     "MLFLOW_OIDC_CLIENT_ID": "factory", "MLFLOW_OIDC_CLIENT_SECRET": "oidc"},
    {"MLFLOW_TRACKING_URI": "https://mlflow.invalid", "MLFLOW_OIDC_TOKEN_URL": "https://idp.invalid/token",
     "MLFLOW_OIDC_CLIENT_ID": "factory", "MLFLOW_OIDC_CLIENT_SECRET": "oidc", "MLFLOW_TRACKING_TOKEN": "token"},
    {"MLFLOW_TRACKING_URI": "http://127.0.0.1:5000", "FACTORY_MLFLOW_ARTIFACT_LOCATION": "mlflow-artifacts:/a"},
    {"MLFLOW_TRACKING_URI": "https://mlflow.invalid", "MLFLOW_OIDC_TOKEN_URL": "https://idp.invalid/token"},
    {},
], ids=["token", "oidc", "oidc-and-token", "none-http", "oidc-incomplete", "nothing"])
def test_cli_settings_match_the_controller_scope(monkeypatch, environment):
    cli = cli_module()
    from tuning_factory.auth import current_auth_mode
    from tuning_factory.controller_entry import TrackingScope, apply_tracking_scope
    from tuning_factory.graph_controller import controller_tracker
    from tuning_factory.tracking import tracking_settings_sha256
    from tuning_factory.workflow_planning import _oidc_incomplete, public_auth_mode, tracking_destination
    values = {name: ("synthetic-" + name.casefold() + "-" + "v" * 8 if name in ("MLFLOW_TRACKING_TOKEN",
                                                                               "MLFLOW_OIDC_CLIENT_SECRET") else value)
              for name, value in environment.items()}
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    mode = current_auth_mode()
    scope = TrackingScope.from_environment()
    view = cli.tracking_view(scope)
    secret_values = [value for name, value in values.items() if name in ("MLFLOW_TRACKING_TOKEN",
                                                                          "MLFLOW_OIDC_CLIENT_SECRET")]
    assert not any(value in json.dumps(view) for value in secret_values)   # flags only, never a credential
    assert public_auth_mode(view) == mode
    planned = tracking_destination(view, auth_mode=public_auth_mode(view))
    try:
        apply_tracking_scope(scope)
    except ValueError:
        assert _oidc_incomplete(view)   # the controller refuses to start; planning reports tracking_incomplete
        return
    assert not _oidc_incomplete(view)
    assert current_auth_mode() == mode
    uri = scope.MLFLOW_TRACKING_URI
    if not uri:
        assert planned is None
        return
    assert planned.public_settings_sha256 == tracking_settings_sha256(controller_tracker(uri).public_settings())


# 12 (no synthetic secret in argv, stdout, stderr, files or the DB; keys reach only the provider)
def test_cli_secrets_never_reach_argv_output_files_or_the_db(tmp_path, monkeypatch):
    cli_module()
    found = submitted_real(tmp_path, monkeypatch)
    opened = []

    def refusing_executor_for(settings, *args, **kwargs):
        opened.append(dict(settings))
        environment = dict(os.environ)
        assert "RUNPOD_API_KEY" not in environment   # the key never travels through the environment
        raise RuntimeError("synthetic transport refusal for " + settings.get("RUNPOD_API_KEY", "?"))

    built = []

    def refusing_tracker(self, *args, **kwargs):
        """An approved real run publishes to MLflow: refused here with the token in the text, which must not leak."""
        built.append(args[:1])
        raise RuntimeError("synthetic MLflow refusal for " + os.environ.get("MLFLOW_TRACKING_TOKEN", "?"))

    calls = provider_spies(monkeypatch, executor_for=refusing_executor_for, mlflow_tracker=refusing_tracker)
    result = found.cli("graph-work", "--executor-profiles", found.profiles, "--approve-execution-and-transfer",
                       "--max-ticks", "6", "--tick-seconds", "0.05")
    assert result.exit_code == 0, result.output
    assert opened and all(item["RUNPOD_API_KEY"] == RUNPOD_KEY for item in opened)   # the credential file's key
    assert calls == []
    node = run_status(found.state, found.run_id)["nodes"][0]
    assert node["wait_reason"].startswith("destination unavailable") and "[redacted]" in node["wait_reason"], node
    # The run's tracking event tried MLflow once the scope was applied; its stored error is redacted too.
    assert built == [("https://mlflow.invalid",)] * len(built) and built
    with contextlib.closing(sqlite3.connect(found.state / "jobs.db")) as db:
        errors = [row[0] for row in db.execute("SELECT last_error FROM tracking_events WHERE run_id=?",
                                               (found.run_id,))]
    assert errors and all("[redacted]" in error for error in errors), errors
    current = current_json(found.state)
    assert current["profiles"] == {"pod-a": found.inspected["data"]["revision"]} and current["skipped_profiles"] == {}
    # D11 through the CLI: the controller's tracker reports exactly the destination the real plan pinned.
    assert current["tracking_settings_sha256"] == found.plan["tracking"]["public_settings_sha256"]
    assert current["tracking"]["effective_auth_mode"] == "token"
    assert "RUNPOD_API_KEY" not in os.environ   # RunPod keys live only in ProfileSecrets
    assert os.environ["MLFLOW_TRACKING_TOKEN"] == MLFLOW_TOKEN   # the one MLflow scope of this process
    files = scanned(found.state)
    assert "jobs.db" in files and "controller/current.json" in files
    assert RUNPOD_KEY.encode() not in found.profiles.read_bytes()
    for value in SECRETS:
        assert value.encode() not in found.cli.captured()
        assert all(value not in argument for argv, _ in found.cli.calls for argument in argv)
        for name, data in files.items():
            assert value.encode() not in data, name


def stop_through_request(state):
    """The bounded fallback of the Ctrl+C tests: the desktop's ``controller_stop`` request for the running owner."""
    from tuning_factory.controller_entry import request_stop
    owner = owner_row(state)
    if owner:
        request_stop(state, owner["owner_id"])


def held_or_active(state, run_id):
    """Jobs of the run that hold a lock or are still active: a drained stop leaves none."""
    with contextlib.closing(sqlite3.connect(Path(state) / "jobs.db")) as db:
        return db.execute(
            "SELECT id,status FROM jobs j WHERE graph_run_id=? AND (status IN ('PROVISIONING','RUNNING','EVALUATING') "
            "OR EXISTS (SELECT 1 FROM gpu_locks g WHERE g.job_id=j.id) "
            "OR EXISTS (SELECT 1 FROM resource_leases r WHERE r.job_id=j.id))", (run_id,)).fetchall()


class CtrlC:
    """Presses Ctrl+C from a helper thread through the real interrupt path. ``_thread.interrupt_main(SIGINT)`` trips
    Python's SIGINT handling exactly as the C-level handler does for a console Ctrl+C, on Windows and POSIX alike, so
    the press lands wherever the main thread is: almost always inside its wait for the controller (9.6 review
    Critical 1). ``signal.raise_signal`` is not used: on Windows it runs the C handler on the calling thread.

    Bounded: it presses only while the command runs and ``ready()`` holds (or at the deadline), and a controller still
    running ``WAIT`` seconds after the last press is stopped through its stop request (``controller_stop``), which the
    test then reports as a failure."""

    def __init__(self, state, ready, presses=1, gap=1.0):
        self.state, self.ready, self.presses, self.gap = state, ready, presses, gap
        self.pressed, self.fallback, self.finished = [], [], threading.Event()
        self.thread = threading.Thread(target=self._run, name="ctrl-c", daemon=True)

    def _run(self):
        deadline = time.monotonic() + WAIT
        while not self.finished.is_set():
            try:
                ready = self.ready()
            except Exception:
                ready = False
            if ready or time.monotonic() > deadline:
                break
            time.sleep(0.05)
        for index in range(self.presses):
            if self.finished.is_set():
                return
            if index:
                time.sleep(self.gap)
            self.pressed.append(time.monotonic())
            _thread.interrupt_main(signal.SIGINT)
        if not self.finished.wait(WAIT):
            self.fallback.append(True)
            stop_through_request(self.state)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.finished.set()
        self.thread.join(WAIT)


def settle(cli, saved_stdout):
    """After the command: wait (bounded) until no controller thread runs in this process, and restore the test's
    stdout should a controller that outlived its command have swapped it."""
    if cli._CONTROLLER.acquire(timeout=WAIT):
        cli._CONTROLLER.release()
    sys.stdout = saved_stdout


def owner_running(state):
    owner = owner_row(state)
    return bool(owner) and owner["expires"] > time.time()


# 13 (D14 and 9.6 review Critical 1: a real Ctrl+C drains, then releases the owner)
@pytest.mark.parametrize("moment", ["as_soon_as_running", "after_the_run_completed"])
def test_a_real_ctrl_c_drains_then_releases_the_owner(tmp_path, monkeypatch, moment):
    cli = cli_module()
    found = submitted_simulation(tmp_path, monkeypatch)
    provider_spies(monkeypatch)

    def ready():
        return owner_running(found.state) and (
            moment == "as_soon_as_running"
            or wrt.ledger(found.state).get_run(found.run_id)["status"] == "COMPLETED")

    handler, saved = signal.getsignal(signal.SIGINT), sys.stdout
    try:
        with CtrlC(found.state, ready) as press:
            result = found.cli("graph-work", "--tick-seconds", "0.05")   # no --max-ticks: it runs until Ctrl+C
    finally:
        settle(cli, saved)
    assert press.fallback == [], "Ctrl+C did not stop the controller"
    assert len(press.pressed) == 1 and signal.getsignal(signal.SIGINT) is handler   # the handler is restored
    final = final_line(result, 0)   # not lost to the controller's null stdout
    assert final["code"] == "stopped" and cli.DRAINING in result.stderr
    assert lines(result)[0]["ok"] is True   # the ready line, then the final line
    current = current_json(found.state)
    assert owner_row(found.state)["expires"] == 0 and current["code"] == "stopped" and current["stopped_at"]
    assert final["controller"] == current
    # Drained: whatever was in flight finished; nothing holds a lock or is still active; nothing failed.
    assert held_or_active(found.state, found.run_id) == []
    status = wrt.ledger(found.state).get_run(found.run_id)["status"]
    assert status == "COMPLETED" if moment == "after_the_run_completed" else status in ("RUNNING", "COMPLETED")
    # The next controller starts at once (no owner_held), adopts the run and finishes it without a relaunch.
    again = found.cli("graph-work", "--max-ticks", "40", "--tick-seconds", "0.05")
    assert final_line(again, 0)["code"] == "stopped"
    assert wrt.ledger(found.state).get_run(found.run_id)["status"] == "COMPLETED"
    with contextlib.closing(sqlite3.connect(found.state / "jobs.db")) as db:
        assert db.execute("SELECT COUNT(*) FROM job_attempts a JOIN jobs j ON j.id=a.job_id "
                          "WHERE j.graph_run_id=?", (found.run_id,)).fetchone()[0] == 1


# The child of the second-press test. It holds the drain open (bounded) so the second Ctrl+C lands while the controller
# is stopping, and presses twice through the real interrupt path once the controller owns the state directory.
SECOND_PRESS = textwrap.dedent('''\
    import _thread, os, signal, socket, sqlite3, sys, threading, time

    def refuse(*args, **kwargs):
        raise OSError("network disabled in CLI graph tests")

    socket.socket.connect = refuse
    state = sys.argv[1]
    from tuning_factory import graph_controller
    drain = graph_controller.GraphController.run_forever

    def slow_drain(self, stop_event):
        stop_event.wait(25)
        time.sleep(20)
        return drain(self, stop_event)

    graph_controller.GraphController.run_forever = slow_drain

    def owner_running():
        try:
            path = os.path.join(state, "jobs.db").replace(os.sep, "/")
            db = sqlite3.connect("file:" + path + "?mode=ro", uri=True)
            try:
                row = db.execute("SELECT expires FROM controller_owner WHERE id=1").fetchone()
            finally:
                db.close()
            return bool(row) and row[0] > time.time()
        except Exception:
            return False

    def press_twice():
        deadline = time.monotonic() + 20
        while not owner_running() and time.monotonic() < deadline:
            time.sleep(0.05)
        _thread.interrupt_main(signal.SIGINT)
        time.sleep(1.0)
        _thread.interrupt_main(signal.SIGINT)

    threading.Thread(target=press_twice, daemon=True).start()
    from tuning_factory.cli import app
    app(args=["--state-dir", state, "graph-work", "--tick-seconds", "0.05"], prog_name="factory")
''')


def test_a_second_real_ctrl_c_exits_at_once_and_keeps_the_owner_until_its_ttl(tmp_path):
    """A child process presses Ctrl+C twice through the real interrupt path while the controller drains: it exits at
    once with 130 (``os._exit``), the owner lease stays held until its TTL, and a restart is refused (exit 3)."""
    cli = cli_module()
    state = tmp_path / "state"
    process = subprocess.Popen([sys.executable, "-c", SECOND_PRESS, str(state)], stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, cwd=tmp_path)
    try:
        stdout, stderr = process.communicate(timeout=WAIT)
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=WAIT)
    assert process.returncode == cli.EXIT_INTERRUPTED == 130, (stdout, stderr)
    assert " ".join(cli.DRAINING.split()) in " ".join(stderr.decode("utf-8", "replace").split())
    output = [json.loads(line) for line in stdout.splitlines() if line.strip()]
    assert len(output) == 1 and output[0]["ok"] is True   # the ready line only: no drain, no final line
    owner = owner_row(state)
    assert owner["owner_id"] == output[0]["owner_id"] and owner["expires"] > time.time() + 5   # held until its TTL
    assert "stopped_at" not in current_json(state)
    restart = Cli(state, env_file(tmp_path))("graph-work", "--max-ticks", "1", "--tick-seconds", "0.05")
    assert final_line(restart, 3)["code"] == "owner_held"   # refused until the owner lease expires


class Exited(BaseException):
    """The stubbed ``_exit_now`` raises this, so a handler ends at its exit as ``os._exit`` ends the process."""


class OwnedLock:
    """``Event._cond``'s non-reentrant lock, reporting a self-deadlock instead of hanging: the owner thread taking it
    again is exactly what blocks a real ``Event.set`` forever."""

    def __init__(self):
        self.lock, self.owner = threading.Lock(), None

    def acquire(self, blocking=True, timeout=-1):
        if blocking and self.owner == threading.get_ident():
            raise AssertionError("the stop event's lock was taken again by its owner: a real Event deadlocks here")
        taken = self.lock.acquire(blocking, timeout)
        if taken:
            self.owner = threading.get_ident()
        return taken

    def release(self):
        self.owner = None
        self.lock.release()

    __enter__ = acquire

    def __exit__(self, *exc):
        self.release()


def code_objects(*classes):
    """The code of every function and property of ``classes``."""
    found = []
    for owner in classes:
        for value in vars(owner).values():
            for function in ((value.fget, value.fset) if isinstance(value, property) else (value,)):
                if hasattr(function, "__code__"):
                    found.append(function.__code__)
    return found


def nested_press(cli, monkeypatch, at):
    """Runs ``_CtrlC`` once with a second SIGINT landing before bytecode number ``at`` of the first handler, counted
    over the handler's own methods and the stop event's (``Event`` and its ``Condition``), as CPython can run a signal
    handler between any two bytecodes of another. ``at=None`` presses once and only counts. Uses ``sys.monitoring``
    instruction events (``sys.settrace`` delivers no opcode events on 3.12). Returns ``(exit codes, the exception
    that ended the outer handler or None, bytecodes counted)``."""
    exits = []

    def exit_now(code):
        exits.append(code)
        raise Exited

    monkeypatch.setattr(cli, "_exit_now", exit_now)
    event = threading.Event()
    event._cond = threading.Condition(OwnedLock())
    handler = cli._CtrlC(event)
    counted, nested = [0], []

    def instruction(code, offset):
        if nested:
            return   # the nested press itself runs to its end
        if counted[0] == at:
            nested.append(True)
            handler(signal.SIGINT, None)   # its Exited propagates into the first handler, like a process exit
        counted[0] += 1

    monitoring = sys.monitoring
    tool = next((tool for tool in range(6) if monitoring.get_tool(tool) is None), None)
    if tool is None:
        pytest.skip("every sys.monitoring tool ID is in use")
    codes = code_objects(cli._CtrlC, threading.Event, threading.Condition)
    ended = None
    monitoring.use_tool_id(tool, "nested-ctrl-c")
    try:
        monitoring.register_callback(tool, monitoring.events.INSTRUCTION, instruction)
        for code in codes:
            monitoring.set_local_events(tool, code, monitoring.events.INSTRUCTION)
        try:
            handler(signal.SIGINT, None)
        except BaseException as exc:   # Exited, or the stand-in for a deadlock
            ended = exc
    finally:
        for code in codes:
            monitoring.set_local_events(tool, code, 0)
        monitoring.register_callback(tool, monitoring.events.INSTRUCTION, None)
        monitoring.free_tool_id(tool)
    return exits, ended, counted[0]


def test_a_ctrl_c_arriving_inside_the_handler_still_exits_on_the_second_press(monkeypatch):
    """9.6 re-review N1: a second Ctrl+C can run the SIGINT handler re-entrantly, between any two bytecodes of the first
    press's handler. At every such point the two presses exit exactly once with 130, and the nested press never takes
    the stop event's non-reentrant lock while the first press holds it (a deadlock of the main thread)."""
    cli = cli_module()
    exits, ended, total = nested_press(cli, monkeypatch, None)
    assert (exits, ended) == ([], None) and total > 10   # one press drains: no exit
    failures = []
    for at in range(total):
        exits, ended, _ = nested_press(cli, monkeypatch, at)
        if exits != [cli.EXIT_INTERRUPTED] or not isinstance(ended, Exited):
            failures.append((at, exits, repr(ended)))
    assert failures == [], failures
    handler = cli._CtrlC(threading.Event())
    assert handler.presses == 0
    handler(signal.SIGINT, None)
    assert handler.presses == 1 and handler.stop_event.is_set()   # the main loop's DRAINING note reads this


def test_a_broken_stderr_does_not_end_the_drain_early(tmp_path, monkeypatch):
    """9.6 re-review N2: the DRAINING note goes to stderr; a broken stderr pipe (OSError) loses the note only. The
    command still waits for the drain and prints its final line."""
    cli = cli_module()
    found = submitted_simulation(tmp_path, monkeypatch)
    provider_spies(monkeypatch)
    echo = cli.typer.echo
    notes = []

    def broken_stderr(message=None, *args, err=False, **kwargs):
        if err and message == cli.DRAINING:
            notes.append(message)
            raise BrokenPipeError(32, "synthetic broken stderr pipe")
        return echo(message, *args, err=err, **kwargs)

    monkeypatch.setattr(cli.typer, "echo", broken_stderr)
    handler, saved = signal.getsignal(signal.SIGINT), sys.stdout
    try:
        with CtrlC(found.state, lambda: owner_running(found.state)) as press:
            result = found.cli("graph-work", "--tick-seconds", "0.05")
    finally:
        settle(cli, saved)
    assert press.fallback == [] and len(press.pressed) == 1 and notes == [cli.DRAINING]
    assert signal.getsignal(signal.SIGINT) is handler
    assert final_line(result, 0)["code"] == "stopped", result.output
    assert owner_row(found.state)["expires"] == 0


def test_graph_work_refuses_insecure_tracking_before_the_owner_lease(tmp_path, monkeypatch):
    """The sidecar's exit 4: credentialed MLflow over plain HTTP is refused before any owner lease (Task 9.4)."""
    cli_module()
    from tuning_factory.auth import INSECURE_TRACKING
    state = tmp_path / "state"
    factory = Cli(state, env_file(tmp_path, MLFLOW_TRACKING_URI="http://mlflow.invalid",
                                  MLFLOW_TRACKING_TOKEN=MLFLOW_TOKEN), charset="cp1252")   # Korean text, cp1252
    result = factory("graph-work", "--approve-execution-and-transfer", "--max-ticks", "1", "--tick-seconds", "0.05")
    assert result.exit_code == 4, result.output
    assert lines(result)[0] == {"ok": False, "code": "settings_refused"}
    final = final_line(result, 4)
    assert (final["code"], final["message"]) == ("settings_refused", INSECURE_TRACKING)
    assert owner_row(state) is None and final["controller"]["code"] == "settings_refused"
    assert "MLFLOW_TRACKING_TOKEN" not in os.environ   # the refusal leaves no credential variable set
    assert MLFLOW_TOKEN.encode() not in factory.captured()
    assert all(MLFLOW_TOKEN.encode() not in data for data in scanned(state).values())
    # Without the approval flag no MLflow scope is applied at all, so the same environment starts and stops cleanly.
    result = factory("graph-work", "--max-ticks", "1", "--tick-seconds", "0.05")
    assert result.exit_code == 0, result.output


def test_cli_graph_commands_import_no_heavy_or_keyring_modules(tmp_path):
    code = textwrap.dedent("""
        import json, socket, sys

        def refuse(*args, **kwargs):
            raise OSError("network disabled in CLI graph tests")

        socket.socket.connect = refuse
        from typer.testing import CliRunner
        from tuning_factory.cli import app
        base = ["--state-dir", sys.argv[1]]
        codes = [CliRunner().invoke(app, [*base, *args]).exit_code for args in (
            ["project-list"], ["graph-status"], ["graph-work", "--max-ticks", "1", "--tick-seconds", "0.05"])]
        heavy = ("torch", "peft", "transformers", "safetensors", "huggingface_hub", "keyring")
        print(json.dumps({"codes": codes, "heavy": sorted(name for name in heavy if name in sys.modules)}))
    """)
    process = subprocess.run([sys.executable, "-c", code, str(tmp_path / "state")], capture_output=True, text=True,
                             timeout=WAIT, cwd=tmp_path)
    assert process.returncode == 0, process.stderr
    assert json.loads(process.stdout.strip().splitlines()[-1]) == {"codes": [0, 0, 0], "heavy": []}


def test_tracking_uri_option_applies_to_graph_plans_submissions_and_the_controller(tmp_path, monkeypatch):
    """``--tracking-uri`` overrides the environment for the graph commands as for the v1 ones; plans, submissions and
    ``graph-work`` use the same scope, so the controller's tracker reports the pinned destination (D11)."""
    cli_module()
    from tuning_factory.tracking import DEFAULT_EXPERIMENT, public_tracking_settings, tracking_settings_sha256
    found = real_world(tmp_path, monkeypatch)
    other = "https://other-mlflow.invalid"
    planned = found.cli("--tracking-uri", other, "graph-plan", found.project_id, "--request",
                        request_file(tmp_path, REQUEST), "--executor-profiles", found.profiles, "--real")
    assert planned.exit_code == 0, planned.output
    plan = parsed(planned)["data"]
    expected = tracking_settings_sha256(public_tracking_settings(other, DEFAULT_EXPERIMENT, None, auth_mode="token"))
    assert plan["tracking"]["public_settings_sha256"] == expected != found.plan["tracking"]["public_settings_sha256"]
    submit = ("graph-submit", plan["plan_sha256"], "--request-id", "other-1", "--approve-execution",
              "--approve-data-transfer", "--executor-profiles", found.profiles)
    refused(found.cli(*submit), "plan_stale", "/tracking", status="conflict")   # the environment's URI differs
    submitted = found.cli("--tracking-uri", other, *submit)
    assert submitted.exit_code == 0, submitted.output

    def refusing_executor_for(settings, *args, **kwargs):
        raise RuntimeError("synthetic transport refusal")

    def refusing_tracker(self, *args, **kwargs):
        raise RuntimeError("synthetic MLflow refusal")

    calls = provider_spies(monkeypatch, executor_for=refusing_executor_for, mlflow_tracker=refusing_tracker)
    worked = found.cli("--tracking-uri", other, "graph-work", "--executor-profiles", found.profiles,
                       "--approve-execution-and-transfer", "--max-ticks", "2", "--tick-seconds", "0.05")
    assert worked.exit_code == 0, worked.output
    assert current_json(found.state)["tracking_settings_sha256"] == expected and calls == []


def test_graph_work_refusals_before_the_start_print_a_final_line(tmp_path):
    """Exit 2 (the sidecar's invalid start) with the uniform final line: here MLflow settings that cannot form a
    tracking scope (an over-long URI)."""
    cli = cli_module()
    state = tmp_path / "state"
    factory = Cli(state, env_file(tmp_path, MLFLOW_TRACKING_URI="https://mlflow.invalid/" + "a" * 3000))
    result = factory("graph-work", "--approve-execution-and-transfer", "--max-ticks", "1")
    final = final_line(result, 2)
    assert (final["code"], final["message"], final["controller"]) == ("invalid_start", cli.TRACKING_INVALID, None)
    assert owner_row(state) is None
    # --help documents the output shapes, the exit codes and what a second Ctrl+C leaves behind (9.6 review Minor 5, 6).
    shown = flat(CliRunner().invoke(app, ["graph-work", "--help"]).output)
    for fragment in ("then a final line", "2 refused before the start", "130 a second Ctrl+C",
                     "a restart gets exit 3", "release-lock --confirm-execution-stopped",
                     # 9.6 re-review N2: not "always": these two outcomes print no final line.
                     "A second Ctrl+C (exit 130) and a usage error (exit 2, the usage message on stderr) print no "
                     "final line"):
        assert fragment in shown, fragment
    assert "always a final line" not in shown
    usage = CliRunner().invoke(app, ["--state-dir", str(state), "graph-work", "--max-ticks", "0"])
    assert usage.exit_code == 2 and usage.stdout.strip() == "", usage.output   # Click's usage error: no final line


def test_new_commands_stay_encodable_on_a_legacy_code_page(tmp_path):
    """Redirected output in a legacy code page (cp949) cannot encode every name: the new commands fall back to
    ASCII-escaped JSON instead of crashing, and keep readable UTF-8 JSON otherwise (9.6 review Minor 3)."""
    cli_module()
    state = tmp_path / "state"
    name = "café — \U0001F600 요약"
    created = desk.call(state, "project_create", {"request_id": "enc-1", "name": name})
    assert created["status"] == "ok", created
    legacy = Cli(state, env_file(tmp_path), charset="cp949")("project-list")
    assert legacy.exit_code == 0, legacy.output
    assert legacy.stdout_bytes.isascii() and parsed(legacy)["data"]["projects"][0]["name"] == name
    readable = Cli(state, env_file(tmp_path))("project-list")
    assert name in readable.stdout   # UTF-8 output keeps the v1 format (no escapes)
    korean = Cli(state, env_file(tmp_path), charset="cp949")("graph-status")
    assert korean.exit_code == 0, korean.output
