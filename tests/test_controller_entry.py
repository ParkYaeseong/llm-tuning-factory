"""Task 9.4: the graph controller as a stdin-configured background sidecar (plan §4 "9.4"; D2-D5, D7, D14, D19).

Main env only, fakes only. In-process tests install a socket guard. Subprocess tests run exactly
``[sys.executable, scripts/desktop_entry.py, "--controller"]`` with ``PYTHONPATH`` pointing at this tree's ``src``
and a ``sitecustomize`` guard that refuses every socket connection in the child. Every spawned process is detached
(``CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS`` on Windows, ``start_new_session`` elsewhere), killed in teardown,
and every wait is bounded (``WAIT``). Synthetic secrets are built at runtime, never committed as literals.
No GPU, SSH, network, model download, MLflow write or provider call.
"""
import contextlib
import importlib
import io
import json
import os
import signal
import socket
import sqlite3
import subprocess
import sys
import textwrap
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest
import test_graph_readiness as readiness
from test_executor_profiles import LIVE_POD, PIN
from test_gpu_servers import UUID2

from tuning_factory import workflow_planning as wp

ROOT = Path(__file__).resolve().parents[1]
ENTRY = ROOT / "scripts" / "desktop_entry.py"
SRC = ROOT / "src"
WAIT = 30   # seconds: the bound of every wait in this module
LOADED_WAIT = 120   # seconds: the bound of a wait on an explicit progress signal, generous for a loaded machine
# Built at runtime: the history-walking secret scan must never see a credential-shaped literal.
RUNPOD_KEY = "synthetic-" + "runpod-" + "k" * 12
RUNPOD_KEY_B = "synthetic-" + "runpod-b-" + "k" * 12
MLFLOW_TOKEN = "synthetic-" + "mlflow-token-" + "t" * 12
OIDC_SECRET = "synthetic-" + "oidc-" + "s" * 12
INHERITED = "synthetic-" + "inherited-" + "x" * 12
SECRETS = (RUNPOD_KEY, RUNPOD_KEY_B, MLFLOW_TOKEN, OIDC_SECRET, INHERITED)
# Subprocess timing (review 8): a 6 s owner lease leaves windows-latest a 4.5 s margin over a 1.5 s heartbeat.
FAST = {"lease_ttl_seconds": 6, "tick_seconds": 0.2, "lease_heartbeat_seconds": 1.5}
# Independent of the implementation: every inherited credential variable the controller must pop (D7), including the
# ambient cloud credentials and MLflow TLS overrides of the review's record list.
CREDENTIAL_NAMES = ("RUNPOD_API_KEY", "MLFLOW_TRACKING_URI", "MLFLOW_TRACKING_TOKEN", "MLFLOW_TRACKING_USERNAME",
                    "MLFLOW_TRACKING_PASSWORD", "MLFLOW_OIDC_TOKEN_URL", "MLFLOW_OIDC_CLIENT_ID",
                    "MLFLOW_OIDC_CLIENT_SECRET", "FACTORY_MLFLOW_ARTIFACT_LOCATION", "FACTORY_OPERATOR",
                    "S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY", "S3_SESSION_TOKEN", "AWS_ACCESS_KEY_ID",
                    "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "HF_TOKEN", "HUGGING_FACE_HUB_TOKEN",
                    "HUGGINGFACE_HUB_TOKEN", "AWS_PROFILE", "AWS_DEFAULT_PROFILE", "AWS_SHARED_CREDENTIALS_FILE",
                    "AWS_CONFIG_FILE", "AWS_WEB_IDENTITY_TOKEN_FILE", "AWS_ROLE_ARN",
                    "AWS_CONTAINER_CREDENTIALS_FULL_URI", "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI",
                    "AWS_CONTAINER_AUTHORIZATION_TOKEN", "AZURE_STORAGE_CONNECTION_STRING", "AZURE_STORAGE_ACCESS_KEY",
                    "AZURE_STORAGE_SAS_TOKEN", "AZURE_CLIENT_SECRET", "AZURE_CLIENT_CERTIFICATE_PATH",
                    "GOOGLE_APPLICATION_CREDENTIALS", "MLFLOW_TRACKING_INSECURE_TLS", "MLFLOW_TRACKING_CLIENT_CERT_PATH")
TRACKING_FIELDS = ("MLFLOW_TRACKING_URI", "FACTORY_MLFLOW_ARTIFACT_LOCATION", "FACTORY_OPERATOR", "auth_mode",
                   "MLFLOW_OIDC_TOKEN_URL", "MLFLOW_OIDC_CLIENT_ID", "MLFLOW_OIDC_CLIENT_SECRET",
                   "MLFLOW_TRACKING_TOKEN")
OIDC_NAMES = ("MLFLOW_OIDC_TOKEN_URL", "MLFLOW_OIDC_CLIENT_ID", "MLFLOW_OIDC_CLIENT_SECRET")
DETACH = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS}
          if os.name == "nt" else {"start_new_session": True})
GUARD = textwrap.dedent('''\
    """Controller entry test guard (sitecustomize): this child process may not open a network connection."""
    import socket


    def _refuse(*args, **kwargs):
        raise OSError("network disabled in controller entry tests")


    socket.socket.connect = _refuse
    socket.socket.connect_ex = _refuse
''')
# The broker stand-in (Task 9.5's broker.rs serve_broker, in Python): one start line from its own stdin to the
# controller's stdin, then it holds the pipe open until the controller exits, and records the exit code.
STANDIN = textwrap.dedent('''\
    import json, os, subprocess, sys
    workdir, entry = sys.argv[1], sys.argv[2]
    line = sys.stdin.buffer.readline()
    with open(os.path.join(workdir, "controller.out"), "wb") as out, \\
            open(os.path.join(workdir, "controller.err"), "wb") as err:
        child = subprocess.Popen([sys.executable, entry, "--controller"], stdin=subprocess.PIPE, stdout=out,
                                 stderr=err)
        pids = os.path.join(workdir, "pids.json")
        with open(pids + ".tmp", "w") as stream:
            json.dump({"standin": os.getpid(), "controller": child.pid}, stream)
        os.replace(pids + ".tmp", pids)
        child.stdin.write(line)
        child.stdin.flush()
        code = child.wait()
    result = os.path.join(workdir, "result.json")
    with open(result + ".tmp", "w") as stream:
        json.dump({"controller_exit": code}, stream)
    os.replace(result + ".tmp", result)
''')
# The GUI proxy: it starts the broker stand-in detached, hands it the start line and exits at once (D1, D3).
PROXY = textwrap.dedent('''\
    import os, subprocess, sys
    line = sys.stdin.buffer.readline()
    flags = ({"creationflags": 0x00000200 | 0x00000008} if os.name == "nt" else {"start_new_session": True})
    with open(os.path.join(sys.argv[2], "standin.err"), "wb") as err:
        child = subprocess.Popen([sys.executable, sys.argv[1], sys.argv[2], sys.argv[3]], stdin=subprocess.PIPE,
                                 stdout=subprocess.DEVNULL, stderr=err, **flags)
    child.stdin.write(line)
    child.stdin.close()
''')


def entry():
    # A missing implementation is a test failure, rather than a collection error.
    try:
        module = importlib.import_module("tuning_factory.controller_entry")
    except ModuleNotFoundError as exc:
        if exc.name != "tuning_factory.controller_entry":
            raise
        pytest.fail("tuning_factory.controller_entry (Task 9.4) is missing")
    for name in ("ControllerStart", "TrackingScope", "ProfileStart", "StartSettings", "read_start",
                 "apply_tracking_scope", "provider_profiles", "build_controller", "request_stop", "stop_requested",
                 "serve", "main", "ControllerStatusRequest", "ControllerStopRequest"):
        if not hasattr(module, name):
            pytest.fail(f"controller_entry.{name} (Task 9.4) is missing")
    return module


def workflows():
    return importlib.import_module("tuning_factory.desktop_workflows")


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """The controller entry is tested with fakes only: no RunPod API, SSH, Hugging Face or MLflow connection."""
    def refuse(*args, **kwargs):
        raise AssertionError("controller entry tests must not open a network connection")
    monkeypatch.setattr(socket.socket, "connect", refuse)


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch):
    """``apply_tracking_scope`` writes the process environment: restore every credential variable afterwards."""
    for name in (*CREDENTIAL_NAMES, "MLFLOW_HTTP_REQUEST_MAX_RETRIES", "MLFLOW_HTTP_REQUEST_TIMEOUT",
                 "MLFLOW_SUPPRESS_PRINTING_URL_TO_STDOUT"):
        monkeypatch.setenv(name, "x")   # registers the teardown even for variables that start absent
        monkeypatch.delenv(name)
    monkeypatch.setattr("tuning_factory.auth.configure_oidc", lambda: None)   # never an OIDC refresher thread


# -- start messages ------------------------------------------------------------------------------

def start_message(state, owner_id="desktop.1.1", *, tracking=None, profiles=(), settings=None, **extra):
    message = {"schema": "factory-controller/1", "state_root": str(state), "owner_id": owner_id,
               "tracking": dict(tracking or {}), "profiles": list(profiles)}
    if settings is not None:
        message["settings"] = settings
    return {**message, **extra}


def line(message) -> bytes:
    return json.dumps(message).encode("utf-8") + b"\n"


def padded(message, size) -> bytes:
    """A start line of exactly ``size`` bytes (newline included): JSON whitespace before the closing brace."""
    raw = json.dumps(message).encode("utf-8")
    return raw[:-1] + b" " * (size - len(raw) - 1) + b"}\n"


def runpod_start(tmp_path, profile_id="pod-a", key=RUNPOD_KEY, **fields):
    return {"profile_id": profile_id,
            "fields": {"provider": "existing_runpod", "name": "Pod A", "pod_id": "pod1", "ssh_fingerprint": PIN,
                       "ssh_key_file": str(tmp_path / "keys" / "pod-key"), **fields},
            "secrets": {"RUNPOD_API_KEY": key} if key else {}}


def server_start(tmp_path, profile_id="lab", **fields):
    return {"profile_id": profile_id,
            "fields": {"provider": "ssh_server", "name": "Lab server", "server_ssh_host": "gpu.example",
                       "server_ssh_port": "22", "server_ssh_username": "researcher", "server_ssh_fingerprint": PIN,
                       "server_remote_root": "/home/researcher/factory",
                       "server_ssh_key_file": str(tmp_path / "keys" / "lab-key"),
                       "server_total_hourly_cost_usd": "0", "server_selected_gpu_uuids": json.dumps([UUID2]),
                       **fields},
            "secrets": {}}


def native(*profiles):
    """The public profile view the native side sends with ``controller_status`` (no ``*_saved`` flag, no vault)."""
    return [{"profile_id": item["profile_id"], "fields": item["fields"]} for item in profiles]


def broker_tracking(saved, vault):
    """``broker.rs`` ``controller_message``: the tracking fields, filtered by ``auth_mode`` exactly as main.rs
    ``filter_mlflow_auth`` filters the sidecar's settings (token keeps the token, none nothing, else OIDC)."""
    withheld = {"token": OIDC_NAMES, "none": (*OIDC_NAMES, "MLFLOW_TRACKING_TOKEN")}.get(
        saved.get("auth_mode", ""), ("MLFLOW_TRACKING_TOKEN",))
    values = {name: value for name, value in saved.items() if name in TRACKING_FIELDS and isinstance(value, str)}
    values.update({name: value for name, value in vault.items() if value})
    return {name: value for name, value in values.items() if name not in withheld}


def simulation_world(tmp_path, nodes=None):
    """A submitted simulation run (``test_graph_readiness.build``) on the wall clock; its owner-less pre-controller
    run lease is expired at once, as ``test_graph_controller.world`` does."""
    w = readiness.build(tmp_path, nodes or (lambda r: [readiness.train(r, "a")]), simulation=True, clock=time.time)
    with w.store.jobs.connection() as db:
        db.execute("UPDATE controller_leases SET expires=0")
    return w


def scanned(directory):
    directory = Path(directory)
    return {path.relative_to(directory).as_posix(): path.read_bytes()
            for path in sorted(directory.rglob("*")) if path.is_file()}


def current_json(state):
    return json.loads((Path(state) / "controller" / "current.json").read_bytes())


def wait_for(predicate, what, timeout=WAIT):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with contextlib.suppress(Exception):
            if predicate():
                return
        time.sleep(0.05)
    raise AssertionError(f"timed out after {timeout} s waiting for {what}")


def owner_row(state):
    from tuning_factory.graph_store import GraphStore
    return GraphStore(Path(state) / "jobs.db").owner_state()


def heartbeats_advance(state, times=2):
    """The owner's heartbeat increases ``times`` times (seen from this process, i.e. from the DB)."""
    seen = [owner_row(state)["heartbeat"]]

    def advanced():
        value = owner_row(state)["heartbeat"]
        if value > seen[-1]:
            seen.append(value)
        return len(seen) > times
    wait_for(advanced, f"{times} owner heartbeats")
    return seen


def action(state, name, data=None, *, settings=None, profiles=(), clock=None):
    wf = workflows()
    context = wf.WorkflowContext(
        state_dir=Path(state), settings=dict(settings or {}),
        profiles={item["profile_id"]: wf.NativeProfile.model_validate(item) for item in profiles},
        clock=clock or time.time)
    return wf.dispatch_action(context, name, {} if data is None else data).model_dump(mode="json")


# -- subprocesses --------------------------------------------------------------------------------

def child_env(tmp_path):
    """The parent's environment without any credential variable, plus this tree's ``src`` and the socket guard."""
    guard = Path(tmp_path) / "guard"
    guard.mkdir(exist_ok=True)
    (guard / "sitecustomize.py").write_text(GUARD, encoding="utf-8")
    env = {key: value for key, value in os.environ.items()
           if key not in CREDENTIAL_NAMES and not key.startswith(("MLFLOW_", "PYTHON"))}
    env["PYTHONPATH"] = os.pathsep.join([str(guard), str(SRC)])
    return env


class Spawned:
    """One detached child with stdout and stderr in files under ``tmp_path`` (never under the state directory)."""

    def __init__(self, tmp_path, argv, name, stdin=None):
        self.out, self.err = Path(tmp_path) / f"{name}.out", Path(tmp_path) / f"{name}.err"
        self.env = child_env(tmp_path)
        with open(self.out, "wb") as out, open(self.err, "wb") as err:
            self.process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=out, stderr=err, env=self.env,
                                            cwd=tmp_path, **DETACH)
        self.args = [str(arg) for arg in self.process.args]
        if stdin is not None:
            self.process.stdin.write(stdin)
            self.process.stdin.flush()

    @classmethod
    def controller(cls, tmp_path, message, name="controller"):
        return cls(tmp_path, [sys.executable, str(ENTRY), "--controller"], name, stdin=line(message))

    def stdout(self) -> bytes:
        return self.out.read_bytes()

    def stderr(self) -> bytes:
        return self.err.read_bytes()

    def lines(self):
        return [json.loads(item) for item in self.stdout().decode("utf-8").splitlines() if item.strip()]

    def ready(self):
        def printed():
            if self.process.poll() is not None:
                raise AssertionError(f"exited {self.process.returncode}: {self.stderr()[-2000:]!r}")
            return self.stdout().endswith(b"\n")
        deadline = time.monotonic() + WAIT
        while time.monotonic() < deadline and not printed():
            time.sleep(0.05)
        (ready,) = self.lines()
        # The pid is the controller's own: a venv's python.exe (like PyInstaller's onefile bootloader) is a
        # launcher whose child runs the interpreter, so it may differ from the spawned process.
        assert ready["ok"] is True and type(ready["pid"]) is int and ready["pid"] > 0, ready
        return ready

    def close_stdin(self):
        with contextlib.suppress(OSError):
            self.process.stdin.close()

    def wait(self):
        return self.process.wait(WAIT)

    def kill(self):
        if self.process.poll() is None:
            self.process.kill()
        with contextlib.suppress(subprocess.TimeoutExpired):
            self.process.wait(WAIT)
        self.close_stdin()


def pid_alive(pid) -> bool:
    if os.name == "nt":
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x00100000 | 0x1000, False, int(pid))   # SYNCHRONIZE | QUERY_LIMITED_INFO
        if not handle:
            return False
        try:
            return kernel32.WaitForSingleObject(handle, 0) == 0x102   # WAIT_TIMEOUT: still running
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:   # an orphan nobody reaped yet is a zombie: it no longer runs
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] != "Z"
    except (OSError, IndexError):
        return True


def kill_pid(pid):
    if pid and pid_alive(pid):
        with contextlib.suppress(OSError):
            os.kill(pid, signal.SIGTERM if os.name == "nt" else signal.SIGKILL)


class Standin:
    """The broker stand-in's work directory: its pids, the controller's output and the recorded exit code."""

    def __init__(self, tmp_path):
        self.dir = Path(tmp_path) / "broker"
        self.dir.mkdir()
        self.script = Path(tmp_path) / "broker_standin.py"
        self.script.write_text(STANDIN, encoding="utf-8")

    def pids(self):
        wait_for(lambda: (self.dir / "pids.json").is_file(), "the stand-in's pids")
        return json.loads((self.dir / "pids.json").read_text())

    def ready(self):
        out = self.dir / "controller.out"
        wait_for(lambda: out.is_file() and out.read_bytes().endswith(b"\n"), "the controller's ready line")
        (ready,) = [json.loads(item) for item in out.read_text().splitlines() if item.strip()]
        assert ready["ok"] is True, ready
        return ready

    def result(self):
        wait_for(lambda: (self.dir / "result.json").is_file(), "the controller's exit")
        return json.loads((self.dir / "result.json").read_text())

    def cleanup(self):
        if (self.dir / "pids.json").is_file() and not (self.dir / "result.json").is_file():
            pids = json.loads((self.dir / "pids.json").read_text())
            kill_pid(pids["controller"])
            kill_pid(pids["standin"])


# 1
@pytest.mark.parametrize("case", [
    "extra_key", "field_name_alias", "missing_line", "unterminated", "too_large", "not_json", "not_object",
    "duplicate_key", "nan", "invalid_utf8", "wrong_schema", "duplicate_profiles", "unknown_secret",
    "credential_in_fields", "unknown_field", "relative_root", "unc_root", "device_root", "parent_root",
    "bad_owner", "out_of_range", "inconsistent", "bool_setting", "string_setting", "secret_in_tracking_name",
    "non_string_tracking"])
def test_start_message_is_strict_bounded_and_never_echoed(tmp_path, case):
    ce = entry()
    state = tmp_path / "state"
    base = start_message(state, tracking={"MLFLOW_TRACKING_URI": "https://mlflow.invalid", "auth_mode": "token",
                                          "MLFLOW_TRACKING_TOKEN": MLFLOW_TOKEN},
                         profiles=[runpod_start(tmp_path)])
    profile = runpod_start(tmp_path)
    raw = {
        "extra_key": lambda: line({**base, "extra": RUNPOD_KEY}),
        "field_name_alias": lambda: line({**{k: v for k, v in base.items() if k != "schema"},
                                          "schema_": "factory-controller/1"}),
        "missing_line": lambda: b"",
        "unterminated": lambda: line(base)[:-1],
        "too_large": lambda: padded(base, ce.MAX_START_BYTES + 1),
        "not_json": lambda: b"{not json " + RUNPOD_KEY.encode() + b"\n",
        "not_object": lambda: line([base]),
        "duplicate_key": lambda: line(base)[:-2] + b', "owner_id": "desktop.9.9"}\n',
        "nan": lambda: line({**base, "settings": {"tick_seconds": 1}}).replace(b'"tick_seconds": 1', b'"tick_seconds": NaN'),
        "invalid_utf8": lambda: b'{"schema": "\xff"}\n',
        "wrong_schema": lambda: line({**base, "schema": "factory-controller/2"}),
        "duplicate_profiles": lambda: line({**base, "profiles": [profile, {**profile, "profile_id": "POD-A."}]}),
        "unknown_secret": lambda: line({**base, "profiles": [{**profile, "secrets": {"HF_TOKEN": RUNPOD_KEY}}]}),
        "credential_in_fields": lambda: line({**base, "profiles": [
            {**profile, "fields": {**profile["fields"], "RUNPOD_API_KEY": RUNPOD_KEY}}]}),
        "unknown_field": lambda: line({**base, "profiles": [
            {**profile, "fields": {**profile["fields"], "server_ssh_host": "gpu.example"}}]}),
        "relative_root": lambda: line({**base, "state_root": "state"}),
        "unc_root": lambda: line({**base, "state_root": "\\\\server\\share\\state"}),
        "device_root": lambda: line({**base, "state_root": "\\\\?\\C:\\factory\\state"}),
        "parent_root": lambda: line({**base, "state_root": str(state / ".." / "other")}),
        "bad_owner": lambda: line({**base, "owner_id": "-desktop"}),
        "out_of_range": lambda: line({**base, "settings": {"tick_seconds": 0.001}}),
        "inconsistent": lambda: line({**base, "settings": {"lease_ttl_seconds": 2, "lease_heartbeat_seconds": 2}}),
        "bool_setting": lambda: line({**base, "settings": {"tick_seconds": True}}),
        "string_setting": lambda: line({**base, "settings": {"tick_seconds": "1"}}),
        "secret_in_tracking_name": lambda: line({**base, "tracking": {"RUNPOD_API_KEY": RUNPOD_KEY}}),
        "non_string_tracking": lambda: line({**base, "tracking": {"MLFLOW_TRACKING_URI": 5}}),
    }[case]()
    with pytest.raises(ValueError) as error:
        ce.read_start(io.BytesIO(raw))
    assert str(error.value) == ce.INVALID_START == "invalid controller start message"
    text = str(error.value) + repr(error.value)
    for value in (*SECRETS, str(state), "desktop.9.9", "gpu.example", "server"):
        assert value not in text
    assert error.value.__cause__ is None and error.value.__suppress_context__


def test_valid_start_messages_ignore_a_second_line_and_hide_their_secrets(tmp_path):
    ce = entry()
    message = start_message(tmp_path / "state", "desktop.42.7",
                            tracking={"MLFLOW_TRACKING_URI": "https://mlflow.invalid", "auth_mode": "token",
                                      "MLFLOW_TRACKING_TOKEN": MLFLOW_TOKEN, "FACTORY_MLFLOW_ARTIFACT_LOCATION": ""},
                            profiles=[runpod_start(tmp_path), server_start(tmp_path)])
    stream = io.BytesIO(line(message) + b"a second line before EOF is never read as a start\n")
    start = ce.read_start(stream)
    assert stream.read() == b"a second line before EOF is never read as a start\n"
    assert start.owner_id == "desktop.42.7" and [p.profile_id for p in start.profiles] == ["pod-a", "lab"]
    assert start.settings == ce.StartSettings() and start.tracking.auth_mode == "token"
    for text in (repr(start), str(start), json.dumps(start.model_dump(mode="json")), repr(start.tracking),
                 repr(start.profiles)):
        for value in SECRETS:
            assert value not in text
    assert "desktop.42.7" in repr(start) and "pod-a" in repr(start) and "lab" in repr(start)
    for text in (repr(start), str(start)):   # owner and profile IDs only: no setting, URI or path
        for value in ("mlflow.invalid", "pod-key", "gpu.example", "state", "token"):
            assert value not in text, value
    # CRLF endings and the largest allowed line are accepted; the Python constructor uses the wire name.
    assert ce.read_start(io.BytesIO(line(message)[:-1] + b"\r\n")).owner_id == "desktop.42.7"
    largest = padded(message, ce.MAX_START_BYTES)
    assert len(largest) == ce.MAX_START_BYTES and ce.read_start(io.BytesIO(largest)).owner_id == "desktop.42.7"
    built = ce.ControllerStart(schema=ce.START_SCHEMA, state_root=str(tmp_path / "state"), owner_id="cli.1.2")
    assert built.profiles == () and built.tracking == ce.TrackingScope()


# 2
def test_desktop_entry_routes_only_the_exact_controller_flag(tmp_path):
    request = {"settings": {}, "action": "jobs", "data": {}}
    outputs = {}
    for name, argv in (("entry", [sys.executable, str(ENTRY)]),
                       ("before", [sys.executable, "-c", "from tuning_factory.desktop import main; main()"])):
        state = tmp_path / f"state-{name}"
        process = subprocess.run(argv, input=line({**request, "root": str(state)}), capture_output=True,
                                 env=child_env(tmp_path), cwd=tmp_path, timeout=WAIT, **DETACH)
        outputs[name] = (process.returncode, process.stdout)
    # No argv: the legacy one-request sidecar, byte for byte what the pre-9.4 entry printed.
    assert outputs["entry"] == outputs["before"]
    assert outputs["entry"][0] == 0 and json.loads(outputs["entry"][1]) == {"ok": True, "result": []}
    broken = Spawned(tmp_path, [sys.executable, str(ENTRY), "--controller"], "broken",
                     stdin=b"not a start message " + RUNPOD_KEY.encode() + b"\n")
    try:
        assert broken.wait() == 2
        (answer,) = broken.lines()
        assert answer == {"ok": False, "code": "invalid_start", "error": "invalid controller start message"}
        assert RUNPOD_KEY.encode() not in broken.stdout() + broken.stderr()
    finally:
        broken.kill()
    assert not (tmp_path / "state").exists()
    # Any other argv is refused before stdin is read: the pipe stays open and nothing is written to it.
    for index, argv in enumerate((["--bogus"], ["--controller", "x"], ["--Controller"], ["controller"],
                                  ["--controller="])):
        refused = Spawned(tmp_path, [sys.executable, str(ENTRY), *argv], f"refused-{index}")
        try:
            assert refused.wait() == 2, argv
            assert refused.stdout() == b"" and b"unsupported sidecar arguments" in refused.stderr()
        finally:
            refused.kill()


# 3 (parent RED: no synthetic secret in controller argv, stdout, stderr or the DB)
def test_controller_secrets_never_reach_argv_stdout_stderr_db_or_files(tmp_path):
    ce = entry()
    w = simulation_world(tmp_path)
    state = w.store.state_dir
    tracking = {"MLFLOW_TRACKING_URI": "https://mlflow.invalid", "auth_mode": "token",
                "MLFLOW_TRACKING_TOKEN": MLFLOW_TOKEN, "MLFLOW_OIDC_CLIENT_SECRET": OIDC_SECRET}   # unused in token mode
    message = start_message(state, "desktop.3.1", tracking=tracking, profiles=[runpod_start(tmp_path)], settings=FAST)
    controller = Spawned.controller(tmp_path, message)
    try:
        ready = controller.ready()
        assert set(ready) == {"ok", "owner_id", "pid"} and ready["owner_id"] == "desktop.3.1"
        assert current_json(state)["pid"] == ready["pid"]
        wait_for(lambda: w.store.get_run(w.run_id)["status"] == "COMPLETED", "the simulation run to complete")
        status = action(state, "controller_status", settings={"MLFLOW_TRACKING_URI": "https://mlflow.invalid",
                                                              "auth_mode": "token"},
                        profiles=native(runpod_start(tmp_path)))
        assert status["data"]["controller"]["state"] == "running"
        assert status["data"]["restart_required"] == {"profiles": [], "tracking": False}
        ce.request_stop(state, "desktop.3.1")
        assert controller.wait() == 0
    finally:
        controller.kill()
    assert controller.args == [sys.executable, str(ENTRY), "--controller"]
    captured = controller.stdout() + controller.stderr()
    assert b"network disabled" not in captured   # the child's socket guard was never hit
    files = scanned(state)
    assert {"jobs.db", "controller/current.json"} <= set(files)
    assert any(name.startswith("tracking/") for name in files)   # the simulation's local receipts
    for value in (RUNPOD_KEY, MLFLOW_TOKEN, OIDC_SECRET):
        assert value not in " ".join(controller.args)
        assert all(value not in item for item in controller.env.values())
        assert value.encode() not in captured
        assert value.encode() not in json.dumps(status).encode()
        for name, data in files.items():
            assert value.encode() not in data, name
    current = current_json(state)
    assert current["owner_id"] == "desktop.3.1" and current["code"] == "stopped" and current["stopped_at"]
    assert current["profiles"] == {"pod-a": wp.profile_revision("pod-a", runpod_start(tmp_path)["fields"])}
    assert current["skipped_profiles"] == {} and len(current["tracking_settings_sha256"]) == 64
    assert current["tracking"]["auth_mode"] == current["tracking"]["effective_auth_mode"] == "token"
    assert owner_row(state)["expires"] == 0


# 4 (parent RED: the controller heartbeat continues after the GUI proxy exits)
def test_controller_heartbeat_continues_after_the_gui_proxy_exits(tmp_path):
    ce = entry()
    state = tmp_path / "state"
    standin = Standin(tmp_path)
    message = start_message(state, "desktop.4.1", settings=FAST, profiles=[runpod_start(tmp_path)])
    proxy = Spawned(tmp_path, [sys.executable, "-c", PROXY, str(standin.script), str(standin.dir), str(ENTRY)],
                    "proxy", stdin=line(message))
    try:
        assert proxy.wait() == 0   # the GUI proxy is gone at once
        pids = standin.pids()
        standin.ready()
        assert not pid_alive(proxy.process.pid)
        heartbeats_advance(state, 2)   # after the proxy exited, the controller keeps its owner lease fresh
        assert owner_row(state)["owner_id"] == "desktop.4.1" and pid_alive(pids["controller"])
        ce.request_stop(state, "desktop.4.1")
        assert standin.result() == {"controller_exit": 0}
        wait_for(lambda: not pid_alive(pids["standin"]), "the broker stand-in to exit")
        assert owner_row(state)["expires"] == 0
    finally:
        proxy.kill()
        standin.cleanup()
    out = (standin.dir / "controller.out").read_bytes() + (standin.dir / "controller.err").read_bytes()
    assert RUNPOD_KEY.encode() not in out and b"network disabled" not in out


# 5
def test_stdin_eof_drains_and_releases_the_owner(tmp_path):
    state = tmp_path / "state"
    standin = Standin(tmp_path)
    message = start_message(state, "desktop.5.1", settings=FAST)
    broker = Spawned(tmp_path, [sys.executable, str(standin.script), str(standin.dir), str(ENTRY)], "broker",
                     stdin=line(message))
    try:
        broker.close_stdin()
        pids = standin.pids()
        standin.ready()
        heartbeats_advance(state, 1)
        broker.process.kill()   # the broker dies: its end of the controller's stdin closes (EOF)
        broker.process.wait(WAIT)
        wait_for(lambda: not pid_alive(pids["controller"]), "the controller to drain and exit")
        assert owner_row(state)["expires"] == 0
        current = current_json(state)
        assert current["owner_id"] == "desktop.5.1" and current["code"] == "stopped" and current["stopped_at"]
    finally:
        broker.kill()
        standin.cleanup()


# 6
def test_stop_request_drains_and_releases_the_owner(tmp_path):
    state = tmp_path / "state"
    controller = Spawned.controller(tmp_path, start_message(state, "desktop.6.1", settings=FAST))
    try:
        controller.ready()
        heartbeats_advance(state, 1)
        stopped = action(state, "controller_stop")
        assert stopped == {"status": "ok", "issues": [], "data": {"requested": True, "owner_id": "desktop.6.1"}}
        assert controller.wait() == 0
    finally:
        controller.kill()
    assert owner_row(state)["expires"] == 0
    current = current_json(state)
    assert current["code"] == "stopped" and current["stopped_at"] >= current["started_at"]
    assert not (state / "controller" / "stop-request.json").exists()   # it named this owner: removed
    again = action(state, "controller_stop")
    assert again["data"] == {"requested": False, "reason": "not_running"}
    assert not (state / "controller" / "stop-request.json").exists()


# 7
def test_duplicate_broker_exits_with_owner_held(tmp_path):
    state = tmp_path / "state"
    first = Spawned.controller(tmp_path, start_message(state, "desktop.7.1", settings=FAST), "first")
    second = None
    try:
        first.ready()
        before = scanned(state / "controller")
        second = Spawned.controller(tmp_path, start_message(state, "desktop.7.2", settings=FAST), "second")
        assert second.wait() == 3
        assert second.lines() == [{"ok": False, "code": "owner_held"}]
        assert scanned(state / "controller") == before   # no status file written or changed by the duplicate
        assert owner_row(state)["owner_id"] == "desktop.7.1"
        heartbeats_advance(state, 1)   # the first keeps running
        entry().request_stop(state, "desktop.7.1")
        assert first.wait() == 0
    finally:
        first.kill()
        if second is not None:
            second.kill()


# 8 (parent RED: a restart adopts the same run without restarting a running attempt)
def test_restart_adopts_the_same_run_and_never_restarts_a_running_attempt(tmp_path):
    ce = entry()
    w = simulation_world(tmp_path)
    store, state, jobs = w.store, w.store.state_dir, w.store.jobs
    owner = store.acquire_owner("test.owner", ttl_seconds=600)
    lease = store.acquire_controller(w.run_id, 600, owner=owner)
    assert len(store.prepare_ready(lease, node_ids=["a"])) == 1
    claimed = readiness.claim(w, "a", ctl=lease)
    jobs.begin_launch(claimed["lease"])
    jobs.transition(claimed["lease"], "RUNNING")
    job_id, beating = claimed["id"], threading.Event()

    def beat():   # the attempt's own worker keeps its heartbeat fresh while it runs
        while not beating.wait(0.2):
            with contextlib.suppress(Exception):
                jobs.heartbeat(claimed["lease"])

    beater = threading.Thread(target=beat, daemon=True)
    beater.start()
    store.release_owner(owner)

    def attempt():
        with jobs.connection() as db:
            row = db.execute("SELECT j.status,j.active_attempt_id,a.launch_id,a.launch_started FROM jobs j JOIN "
                             "job_attempts a ON a.attempt_id=j.active_attempt_id WHERE j.id=?", (job_id,)).fetchone()
            count = db.execute("SELECT COUNT(*) FROM job_attempts WHERE job_id=?", (job_id,)).fetchone()[0]
        return tuple(row) + (count,)

    def run_lease_owner():
        with jobs.connection() as db:
            row = db.execute("SELECT o.owner_id FROM controller_leases c JOIN controller_owner o ON o.id=1 "
                             "WHERE c.run_id=? AND c.owner_token=o.lease_token AND c.expires>?",
                             (w.run_id, time.time())).fetchone()
        return row[0] if row else None

    def launches():
        directory = state / "jobs" / job_id / "launches"
        return sorted(path.name for path in directory.iterdir()) if directory.is_dir() else []

    before, launched = attempt(), launches()
    assert before[0] == "RUNNING" and before[3] == 1 and before[4] == 1
    first = second = None
    try:
        first = Spawned.controller(tmp_path, start_message(state, "desktop.8.1", settings=FAST), "first")
        first.ready()
        wait_for(lambda: run_lease_owner() == "desktop.8.1", "controller #1 to adopt the run")
        heartbeats_advance(state, 2)
        assert attempt() == before
        first.process.kill()   # no drain: the next controller reconciles from the DB
        first.process.wait(WAIT)
        wait_for(lambda: owner_row(state)["expires"] < time.time(), "the killed owner's lease to expire")
        second = Spawned.controller(tmp_path, start_message(state, "desktop.8.2", settings=FAST), "second")
        second.ready()
        wait_for(lambda: run_lease_owner() == "desktop.8.2", "controller #2 to adopt the run")
        heartbeats_advance(state, 2)
        assert attempt() == before   # same attempt, launch and launch flag; no new attempt
        assert launches() == launched   # no launch directory gained a launch
        ce.request_stop(state, "desktop.8.2")
        assert second.wait() == 0
    finally:
        beating.set()
        beater.join(WAIT)
        for spawned in (first, second):
            if spawned is not None:
                spawned.kill()
    assert attempt() == before and launches() == launched


# 9
def test_tracking_scope_is_applied_once_and_never_per_profile(tmp_path, monkeypatch):
    ce = entry()
    from tuning_factory import desktop_runtime
    assert set(CREDENTIAL_NAMES) <= set(ce.CREDENTIAL_ENV)
    for name in CREDENTIAL_NAMES:
        monkeypatch.setenv(name, INHERITED)
    scope = ce.TrackingScope(MLFLOW_TRACKING_URI="https://mlflow.invalid", auth_mode="token",
                             MLFLOW_TRACKING_TOKEN=MLFLOW_TOKEN, FACTORY_OPERATOR="researcher",
                             MLFLOW_OIDC_TOKEN_URL="https://idp.invalid/token", MLFLOW_OIDC_CLIENT_SECRET=OIDC_SECRET)
    ce.apply_tracking_scope(scope)
    assert INHERITED not in os.environ.values()
    assert {name: os.environ.get(name) for name in CREDENTIAL_NAMES if name in os.environ} == {
        "MLFLOW_TRACKING_URI": "https://mlflow.invalid", "MLFLOW_TRACKING_TOKEN": MLFLOW_TOKEN,
        "FACTORY_OPERATOR": "researcher"}   # token mode withholds the OIDC values; nothing else is restored
    keys = []

    class RunPod:   # read-only status of the profile's own Pod; the key arrives as a value, never from env
        def __init__(self, api_key, client=None):
            keys.append(api_key)

        def status(self, pod_id):
            return {**LIVE_POD, "id": pod_id}

    class SSH:
        def set_missing_host_key_policy(self, policy):
            pass

        def connect(self, *args, **kwargs):
            pass

        def close(self):
            pass

    monkeypatch.setattr(desktop_runtime, "RunPodController", RunPod)
    monkeypatch.setattr(desktop_runtime.paramiko, "SSHClient", SSH)
    monkeypatch.setattr(desktop_runtime, "check", lambda *args, **kwargs: {})
    state = tmp_path / "state"
    start = ce.ControllerStart.model_validate(start_message(state, profiles=[
        runpod_start(tmp_path), runpod_start(tmp_path, "pod-b", RUNPOD_KEY_B, pod_id="pod2", name="Pod B")]))
    usable, skipped = ce.provider_profiles(start)
    assert skipped == {} and sorted(usable) == ["pod-a", "pod-b"]
    controller, _ = ce.build_controller(start)
    try:
        before = dict(os.environ)
        for profile_id, pod_id in (("pod-a", "pod1"), ("pod-b", "pod2")):
            public = usable[profile_id].public
            with controller.provider.resolve(profile_id, public.revision, pinned=public,
                                             runtime_profile="primary") as resolved:
                assert resolved.executor.pod["id"] == pod_id and resolved.profile == public
                assert dict(os.environ) == before
        assert keys == [RUNPOD_KEY, RUNPOD_KEY_B] and dict(os.environ) == before
    finally:
        controller.pool.shutdown()
    for text in (repr(usable), repr(controller.provider), json.dumps(controller.provider.status())):
        assert RUNPOD_KEY not in text and RUNPOD_KEY_B not in text


# 10
@pytest.mark.parametrize("mode", ["", "oidc", "token", "none"])
def test_public_auth_mode_matches_the_controller_scope(tmp_path, monkeypatch, mode):
    ce = entry()
    from tuning_factory.auth import OIDC_CLIENT_MISSING, current_auth_mode
    for url in ("", "https://idp.invalid/token"):
        for token_saved in (False, True):
            for secret_saved in (False, True):
                saved = {"MLFLOW_TRACKING_URI": "https://mlflow.invalid", "auth_mode": mode,
                         "MLFLOW_OIDC_TOKEN_URL": url, "MLFLOW_OIDC_CLIENT_ID": "client-id"}
                vault = {"MLFLOW_TRACKING_TOKEN": MLFLOW_TOKEN if token_saved else "",
                         "MLFLOW_OIDC_CLIENT_SECRET": OIDC_SECRET if secret_saved else ""}
                public = {**saved, **{f"{name}_saved": True for name, value in vault.items() if value}}
                scope = ce.TrackingScope.model_validate(broker_tracking(saved, vault))
                case = (mode, url, token_saved, secret_saved)
                try:
                    ce.apply_tracking_scope(scope)
                except ValueError as exc:   # the controller refuses an incomplete OIDC scope at start
                    assert str(exc) == OIDC_CLIENT_MISSING and mode in ("", "oidc") and url and not secret_saved
                    assert "MLFLOW_OIDC_TOKEN_URL" not in os.environ, case
                    continue
                assert wp.public_auth_mode(public) == current_auth_mode(), case


# 11
def test_credentialed_tracking_over_http_is_refused_before_the_owner_lease(tmp_path, capsys):
    ce = entry()
    from tuning_factory.auth import INSECURE_TRACKING, OIDC_CLIENT_MISSING
    refused = [
        ({"MLFLOW_TRACKING_URI": "http://mlflow.invalid", "auth_mode": "token", "MLFLOW_TRACKING_TOKEN": MLFLOW_TOKEN},
         INSECURE_TRACKING),
        ({"MLFLOW_TRACKING_URI": "https://mlflow.invalid", "FACTORY_MLFLOW_ARTIFACT_LOCATION": "http://store.invalid/a",
          "auth_mode": "token", "MLFLOW_TRACKING_TOKEN": MLFLOW_TOKEN}, INSECURE_TRACKING),
        ({"MLFLOW_TRACKING_URI": "http://mlflow.invalid", "auth_mode": "oidc",
          "MLFLOW_OIDC_TOKEN_URL": "https://idp.invalid/token", "MLFLOW_OIDC_CLIENT_ID": "client",
          "MLFLOW_OIDC_CLIENT_SECRET": OIDC_SECRET}, INSECURE_TRACKING),
        ({"MLFLOW_TRACKING_URI": "https://mlflow.invalid", "auth_mode": "oidc",
          "MLFLOW_OIDC_TOKEN_URL": "https://idp.invalid/token", "MLFLOW_OIDC_CLIENT_ID": "client"}, OIDC_CLIENT_MISSING),
    ]
    for index, (tracking, text) in enumerate(refused):
        state = tmp_path / f"refused-{index}"
        start = ce.ControllerStart.model_validate(start_message(state, f"desktop.11.{index}", tracking=tracking,
                                                                settings=FAST))
        assert ce.serve(start, max_ticks=1) == ce.EXIT_SETTINGS_REFUSED == 4
        assert owner_row(state) is None   # refused before any owner lease
        current = current_json(state)
        assert set(current) == {"ok", "code", "error", "owner_id", "at"}
        assert (current["ok"], current["code"], current["error"]) == (False, "settings_refused", text)
        assert not any(name in os.environ for name in ("MLFLOW_TRACKING_TOKEN", "MLFLOW_OIDC_CLIENT_SECRET",
                                                       "MLFLOW_TRACKING_URI"))
    output = capsys.readouterr()
    assert output.out.splitlines() == [json.dumps({"ok": False, "code": "settings_refused"})] * len(refused)
    for value in SECRETS:
        assert value not in output.out + output.err
        assert all(value.encode() not in data for data in scanned(tmp_path).values())
    # A refusal never overwrites the status of a controller that is running in the same state directory.
    from tuning_factory.graph_store import GraphStore
    busy = tmp_path / "busy"
    held = GraphStore(busy / "jobs.db").acquire_owner("desktop.11.held", ttl_seconds=600)
    start = ce.ControllerStart.model_validate(start_message(busy, "desktop.11.9", tracking=refused[0][0]))
    assert ce.serve(start) == 4 and not (busy / "controller" / "current.json").exists()
    GraphStore(busy / "jobs.db").release_owner(held)
    capsys.readouterr()
    # auth_mode none allows a local http server and drops any MLflow credential even if one was sent.
    state = tmp_path / "local"
    start = ce.ControllerStart.model_validate(start_message(
        state, "desktop.11.5", settings=FAST,
        tracking={"MLFLOW_TRACKING_URI": "http://127.0.0.1:5000", "auth_mode": "none",
                  "MLFLOW_TRACKING_TOKEN": MLFLOW_TOKEN}))
    assert ce.serve(start, max_ticks=1) == 0
    assert capsys.readouterr().out.splitlines() == [
        json.dumps({"ok": True, "owner_id": "desktop.11.5", "pid": os.getpid()})]
    current = current_json(state)
    assert current["ok"] is True and current["code"] == "stopped"
    assert current["tracking"]["effective_auth_mode"] == "none" and len(current["tracking_settings_sha256"]) == 64
    assert owner_row(state)["owner_id"] == "desktop.11.5" and owner_row(state)["expires"] == 0
    assert os.environ["MLFLOW_TRACKING_URI"] == "http://127.0.0.1:5000" and "MLFLOW_TRACKING_TOKEN" not in os.environ


# 12
def test_provider_profiles_come_from_native_fields_and_observations(tmp_path, capsys):
    ce = entry()
    from test_gpu_servers import FakeServer

    from tuning_factory.gpu_servers import server_settings
    state = tmp_path / "state"
    runpod = runpod_start(tmp_path)
    lab, stale = server_start(tmp_path, "lab"), server_start(tmp_path, "stale", name="Stale")
    missing = server_start(tmp_path, "missing")
    nokey = runpod_start(tmp_path, "pod-nokey", key=None, name="No key")
    broken = runpod_start(tmp_path, "pod-broken", ssh_fingerprint="SHA256:short")
    relative = runpod_start(tmp_path, "pod-relative", ssh_key_file="keys/pod-key")   # D11 hashes resolved paths
    observed = datetime.now(timezone.utc)
    facts = FakeServer(tmp_path / "remote").facts(server_settings(lab["fields"]))
    public = wp.public_profile("lab", lab["fields"], facts)
    wp.save_observation(state, public, facts, revision=public.revision, observed_at=observed)
    before = wp.public_profile("stale", {**stale["fields"], "name": "Before the edit"}, facts)
    wp.save_observation(state, before, facts, revision=before.revision, observed_at=observed)
    profiles = [runpod, lab, stale, missing, nokey, broken, relative]
    start = ce.ControllerStart.model_validate(start_message(state, "desktop.12.1", profiles=profiles, settings=FAST))
    usable, skipped = ce.provider_profiles(start)
    assert skipped == {"stale": "observation_revision_differs", "missing": "observation_missing",
                       "pod-nokey": "credential_missing", "pod-broken": "invalid_fields",
                       "pod-relative": "invalid_fields"}
    assert sorted(usable) == ["lab", "pod-a"]
    assert usable["pod-a"].public == wp.public_profile("pod-a", runpod["fields"])   # RunPod: offline, 9.2 revision
    assert usable["pod-a"].public.revision == wp.profile_revision("pod-a", runpod["fields"])
    assert usable["lab"].public == public   # SSH: from the stored observation of the same revision
    assert dict(usable["pod-a"].settings) == {**runpod["fields"], "RUNPOD_API_KEY": RUNPOD_KEY}
    assert dict(usable["lab"].settings) == lab["fields"]   # no RunPod key for an SSH profile
    assert ce.serve(start, max_ticks=1) == 0
    current = current_json(state)
    assert current["profiles"] == {item["profile_id"]: wp.profile_revision(item["profile_id"], item["fields"])
                                   for item in profiles}
    assert current["skipped_profiles"] == skipped
    assert current["tracking_settings_sha256"] is None   # no MLflow server: no tracker
    text = (state / "controller" / "current.json").read_text(encoding="utf-8")
    for value in (RUNPOD_KEY, "gpu.example", "pod-key", "lab-key", PIN, "researcher", "Lab server", '"fields"',
                  '"secrets"'):
        assert value not in text
    assert RUNPOD_KEY not in capsys.readouterr().out


# 13
def test_controller_status_reports_state_and_restart_required(tmp_path):
    ce = entry()
    from tuning_factory.graph_controller import graph_status
    w = simulation_world(tmp_path)
    state = w.store.state_dir
    saved = {"MLFLOW_TRACKING_URI": "https://mlflow.invalid", "auth_mode": "token", "FACTORY_OPERATOR": "researcher",
             "MLFLOW_OIDC_TOKEN_URL": "https://idp.invalid/token", "MLFLOW_OIDC_CLIENT_ID": "client", "pod_id": "x"}
    pod_a = runpod_start(tmp_path)
    pod_b = runpod_start(tmp_path, "pod-b", RUNPOD_KEY_B, pod_id="pod2", name="Pod B")
    now = time.time()

    def status(settings=saved, profiles=(pod_a, pod_b), at=None):
        result = action(state, "controller_status", settings=settings, profiles=native(*profiles),
                        clock=lambda: at or now)
        assert result["status"] == "ok" and result["issues"] == [], result
        data = result["data"]
        assert data["controller"] == graph_status(w.store, now=at or now)["controller"]   # graph_status parity
        return data

    absent = status()
    assert absent == {"controller": {"owner_id": None, "state": "absent", "heartbeat_age_seconds": None},
                      "current": None, "broker": None, "stop_requested": False,
                      "restart_required": {"profiles": [], "tracking": False}, "runs": {"active": 1}}

    def running(tracking, owner_id):
        """The owner row and current.json exactly as ``serve`` leaves them for a running controller."""
        start = ce.ControllerStart.model_validate(start_message(state, owner_id, tracking=tracking,
                                                                profiles=[pod_a, pod_b]))
        ce.apply_tracking_scope(start.tracking)
        controller, info = ce.build_controller(start)
        owner = controller.acquire_controller(owner_id)
        ce.write_current(state, ce.running_record(owner_id, info))
        controller.pool.shutdown()
        return controller, owner

    controller, owner = running(broker_tracking(saved, {"MLFLOW_TRACKING_TOKEN": MLFLOW_TOKEN}), "desktop.13.1")
    now = time.time()
    data = status()
    assert data["controller"]["state"] == "running" and data["current"]["owner_id"] == "desktop.13.1"
    assert data["restart_required"] == {"profiles": [], "tracking": False}
    assert data["stop_requested"] is False and data["runs"] == {"active": 1}
    changed = {**pod_b, "fields": {**pod_b["fields"], "name": "Renamed"}}
    added = runpod_start(tmp_path, "pod-c", name="Pod C")
    assert status(profiles=(pod_a, changed))["restart_required"]["profiles"] == ["pod-b"]
    assert status(profiles=(pod_a,))["restart_required"]["profiles"] == ["pod-b"]       # deleted
    assert status(profiles=(pod_a, pod_b, added))["restart_required"]["profiles"] == ["pod-c"]   # added
    for change in ({"MLFLOW_TRACKING_URI": "https://other.invalid"}, {"FACTORY_OPERATOR": "someone"},
                   {"FACTORY_MLFLOW_ARTIFACT_LOCATION": "s3://bucket/x"}, {"auth_mode": "none"},
                   {"auth_mode": "oidc"}, {"auth_mode": ""}):
        assert status(settings={**saved, **change})["restart_required"] == {"profiles": [], "tracking": True}, change
    # OIDC values are unused by token mode, and unrelated public settings are not tracking settings.
    for change in ({"MLFLOW_OIDC_TOKEN_URL": "https://other.invalid/token"}, {"pod_id": "y"}):
        assert status(settings={**saved, **change})["restart_required"]["tracking"] is False, change
    ce.request_stop(state, "desktop.13.1")
    assert status()["stop_requested"] is True
    # A broker failure never describes the controller that runs now: older than its start, or written later by the
    # broker of a fenced predecessor that lingered after a sleep (review 4). Hidden while this owner runs.
    started = current_json(state)["started_at"]
    for at in (started - 30, now + 5):
        (state / "controller" / "broker.json").write_text(json.dumps({"at": at, "code": "controller_failed",
                                                                      "error": "fixed"}), encoding="utf-8")
        assert status()["broker"] is None, at
    stale = status(at=now + 61)
    assert stale["controller"]["state"] == "stale" and stale["restart_required"] == {"profiles": [], "tracking": False}
    assert stale["broker"]["code"] == "controller_failed"   # no running controller: the failure is shown
    assert status(profiles=(pod_a,), at=now + 61)["restart_required"]["profiles"] == []
    w.store.release_owner(owner)
    assert status()["controller"]["state"] == "absent" and status()["restart_required"]["profiles"] == []
    assert status()["broker"]["code"] == "controller_failed"
    (state / "controller" / "broker.json").unlink()

    # Token mode whose token was saved only after the start: the applied scope is "none", so a restart is required.
    controller, owner = running(broker_tracking(saved, {}), "desktop.13.2")
    now = time.time()
    assert status()["current"]["tracking"]["effective_auth_mode"] == "none"
    assert status()["restart_required"] == {"profiles": [], "tracking": True}
    w.store.release_owner(owner)
    # Legacy auth_mode "" (no *_saved flags in a poll): never a permanent false restart, whatever was applied.
    legacy = {**saved, "auth_mode": ""}
    for vault, effective in (({"MLFLOW_OIDC_CLIENT_SECRET": OIDC_SECRET}, "oidc"), ({}, None)):
        tracking = broker_tracking(legacy if effective else {**legacy, "MLFLOW_OIDC_TOKEN_URL": ""}, vault)
        controller, owner = running(tracking, f"desktop.13.{effective or 'none'}")
        now = time.time()
        polled = legacy if effective else {**legacy, "MLFLOW_OIDC_TOKEN_URL": ""}
        data = status(settings=polled)
        assert data["current"]["tracking"]["effective_auth_mode"] == (effective or "none")
        assert data["restart_required"] == {"profiles": [], "tracking": False}
        assert status(settings={**polled, "MLFLOW_OIDC_CLIENT_ID": "other"})["restart_required"]["tracking"] is True
        w.store.release_owner(owner)
    # An unreadable or older current.json: the tracking comparison is unknown, never invented.
    controller, owner = running(broker_tracking(saved, {"MLFLOW_TRACKING_TOKEN": MLFLOW_TOKEN}), "desktop.13.3")
    now = time.time()
    record = current_json(state)
    ce.write_current(state, {key: value for key, value in record.items() if key != "tracking"})
    assert status()["restart_required"] == {"profiles": [], "tracking": None}
    w.store.release_owner(owner)
    text = json.dumps(status())
    for value in SECRETS:
        assert value not in text


# 14
def test_controller_stop_without_a_running_controller_is_a_noop(tmp_path):
    entry()
    state = tmp_path / "state"
    for name in ("controller_stop", "controller_status"):
        result = action(state, name, {"command": "calc.exe"})
        assert result["status"] == "invalid" and [issue["code"] for issue in result["issues"]] == ["extra_field"]
    assert action(state, "controller_stop")["data"] == {"requested": False, "reason": "not_running"}
    status = action(state, "controller_status")["data"]
    assert status["controller"]["state"] == "absent" and status["runs"] == {"active": 0}
    assert not state.exists()   # a poll or a refused stop creates nothing
    from tuning_factory.graph_store import GraphStore
    store = GraphStore(state / "jobs.db")
    owner = store.acquire_owner("desktop.14.1", ttl_seconds=2)
    later = time.time() + 5
    assert action(state, "controller_stop", clock=lambda: later)["data"] == {"requested": False,
                                                                           "reason": "not_running"}
    assert not (state / "controller").exists()   # a stale controller gets no stop request
    store.release_owner(owner)
    # Through the channel exactly as the native side sends it: empty settings, profiles and secrets (9.5).
    answer = workflows().handle({"channel": "workflow", "root": str(state), "settings": {}, "profiles": [],
                                 "secrets": {}, "action": "controller_stop", "data": {}})
    assert answer == {"status": "ok", "issues": [], "data": {"requested": False, "reason": "not_running"}}
    assert not (state / "controller").exists()


# 15
def test_entry_modules_import_no_keyring(tmp_path):
    code = textwrap.dedent("""
        import json, sys
        loaded = []
        for name in ("tuning_factory.controller_entry", "tuning_factory.desktop_workflows",
                     "tuning_factory.workflow_planning", "tuning_factory.workflow_runs"):
            try:
                __import__(name)
                loaded.append(name)
            except ModuleNotFoundError as exc:
                if exc.name != name:
                    raise
        from tuning_factory import controller_entry as ce
        light = sorted(name for name in ("paramiko", "mlflow") if name in sys.modules)
        start = ce.ControllerStart(schema=ce.START_SCHEMA, state_root=sys.argv[1], owner_id="desktop.15.1")
        controller, info = ce.build_controller(start)
        heavy = ("torch", "peft", "transformers", "safetensors", "huggingface_hub", "keyring")
        print(json.dumps({"loaded": loaded, "light": light,
                          "heavy": sorted(name for name in heavy if name in sys.modules)}))
    """)
    process = subprocess.run([sys.executable, "-c", code, str(tmp_path / "state")], capture_output=True, text=True,
                             env=child_env(tmp_path), cwd=tmp_path, timeout=WAIT)
    assert process.returncode == 0, process.stderr
    result = json.loads(process.stdout.strip().splitlines()[-1])
    assert {"tuning_factory.controller_entry", "tuning_factory.desktop_workflows",
            "tuning_factory.workflow_planning"} <= set(result["loaded"])
    assert result["heavy"] == []
    assert result["light"] == []   # the polling sidecar never imports SSH or MLflow for the lifecycle actions


def test_the_lifeline_is_moved_off_standard_input(tmp_path):
    """Windows: a thread blocked reading the broker's pipe made every child process that inherited the pipe as its
    standard input hang (the manifest's ``git rev-parse`` did, so no node was ever prepared). After the start line
    the pipe leaves standard input: children get the null device, and EOF still reaches the lifeline reader."""
    code = textwrap.dedent("""
        import subprocess, sys, threading
        from tuning_factory import controller_entry as ce
        sys.stdin.buffer.readline()
        lifeline = ce._detach_lifeline(sys.stdin.buffer)
        eof = threading.Event()
        threading.Thread(target=ce._drain_on_eof, args=(lifeline, eof), daemon=True).start()
        child = subprocess.run([sys.executable, "-c", "import sys; print(len(sys.stdin.read()))"],
                               capture_output=True, text=True, timeout=20)
        print(child.stdout.strip(), eof.is_set(), flush=True)
        print(eof.wait(20), flush=True)
    """)
    process = Spawned(tmp_path, [sys.executable, "-c", code], "lifeline", stdin=b"start\nmore\n")
    try:
        wait_for(lambda: process.stdout().count(b"\n") >= 1, "the child's answer")
        assert process.stdout().split() == [b"0", b"False"]   # the null device; the broker's pipe is still open
        process.close_stdin()   # the broker goes away
        assert process.wait() == 0, process.stderr()
        assert process.stdout().split() == [b"0", b"False", b"True"]
    finally:
        process.kill()


def test_tracking_scope_from_the_cli_environment(monkeypatch):
    """Task 9.6's ``graph-work`` scope: this process's MLflow variables, with the auth mode they configure."""
    ce = entry()
    monkeypatch.setenv("MLFLOW_TRACKING_URI", "https://mlflow.invalid")
    monkeypatch.setenv("MLFLOW_TRACKING_TOKEN", MLFLOW_TOKEN)
    scope = ce.TrackingScope.from_environment()
    assert scope.auth_mode == "token" and scope.MLFLOW_TRACKING_TOKEN.get_secret_value() == MLFLOW_TOKEN
    assert MLFLOW_TOKEN not in repr(scope)
    ce.apply_tracking_scope(scope)
    assert os.environ["MLFLOW_TRACKING_TOKEN"] == MLFLOW_TOKEN
    monkeypatch.delenv("MLFLOW_TRACKING_TOKEN")
    monkeypatch.setenv("MLFLOW_OIDC_TOKEN_URL", "https://idp.invalid/token")
    scope = ce.TrackingScope.from_environment()   # an OIDC URL without client credentials stays oidc: refused
    assert scope.auth_mode == "oidc"
    with pytest.raises(ValueError):
        ce.apply_tracking_scope(scope)
    assert "MLFLOW_OIDC_TOKEN_URL" not in os.environ   # the refusal leaves no MLflow variable set
    assert ce.TrackingScope.from_environment().auth_mode == "none"


def test_destination_opening_is_bounded_and_redacted(tmp_path):
    """Task 8 review: ``resolve``/``recover`` need a time limit, and no provider error text keeps a credential."""
    ce = entry()
    opened, closed, release = [], [], threading.Event()

    class Provider:
        secret = RUNPOD_KEY

        def redact(self, text):
            return str(text).replace(self.secret, "[redacted]")

        def status(self):
            return {"provider": "fake"}

        @contextlib.contextmanager
        def resolve(self, profile_id, approved_revision, **options):
            if profile_id == "hang":
                release.wait(WAIT)
            if profile_id == "leaky":
                raise RuntimeError(f"connection refused for key {self.secret}")
            if profile_id == "refused":
                raise ValueError("executor profile changed after approval; re-plan before running")
            opened.append(profile_id)
            try:
                yield profile_id
            finally:
                closed.append(profile_id)

        recover = resolve

    provider = ce.BoundedProvider(Provider(), timeout_seconds=0.3)
    assert provider.redact(f"x {RUNPOD_KEY}") == "x [redacted]" and provider.status() == {"provider": "fake"}
    with provider.resolve("ok", "1", pinned=None) as value:
        assert value == "ok" and closed == []
    assert closed == ["ok"]
    with pytest.raises(ZeroDivisionError):   # the body's own exception passes through the inner exit
        with provider.recover("body", "1", pinned=None):
            raise ZeroDivisionError
    assert closed == ["ok", "body"]
    started = time.monotonic()
    with pytest.raises(TimeoutError) as error:
        with provider.resolve("hang", "1", pinned=None):
            pytest.fail("a hung destination resolved")
    assert time.monotonic() - started < 5 and str(error.value) == ce.DESTINATION_TIMEOUT
    release.set()
    wait_for(lambda: closed[-1] == "hang", "the late destination to be closed")   # never left open
    with pytest.raises(RuntimeError) as error:
        with provider.resolve("leaky", "1", pinned=None):
            pass
    assert RUNPOD_KEY not in str(error.value) and "[redacted]" in str(error.value)
    assert error.value.__cause__ is None and error.value.__suppress_context__
    with pytest.raises(ValueError) as error:   # fixed provider refusals pass unchanged (their type and text)
        with provider.resolve("refused", "1", pinned=None):
            pass
    assert str(error.value) == "executor profile changed after approval; re-plan before running"
    # build_controller installs the bounded provider and the controller's redaction filter on the ledger.
    start = ce.ControllerStart.model_validate(start_message(tmp_path / "state", profiles=[runpod_start(tmp_path)]))
    controller, _ = ce.build_controller(start)
    try:
        assert isinstance(controller.provider, ce.BoundedProvider)
        assert controller.provider.timeout_seconds == ce.DESTINATION_TIMEOUT_SECONDS
        assert controller.store.jobs.error_filter == controller.redact
        assert RUNPOD_KEY not in controller.store.jobs.filtered(f"remote said {RUNPOD_KEY}")
    finally:
        controller.pool.shutdown()


# -- review fixes (9.4 review of 1a9ae29) -------------------------------------------------------

HOT_WRITER = textwrap.dedent("""\
    import sqlite3, sys, time
    db = sqlite3.connect(sys.argv[1], isolation_level=None)
    db.execute("PRAGMA cache_size=10")   # spill: modified pages reach jobs.db before any commit
    db.execute("BEGIN IMMEDIATE")
    db.execute("UPDATE controller_owner SET expires=9e18")
    db.execute("CREATE TABLE IF NOT EXISTS filler (data BLOB)")
    for _ in range(500):
        db.execute("INSERT INTO filler VALUES (zeroblob(4096))")
    print("ready", flush=True)
    time.sleep(60)
""")


def test_controller_status_survives_a_hot_journal(tmp_path):
    """Review 1: a controller killed mid-write leaves ``jobs.db-journal`` hot. A read-only connection cannot roll it
    back, so every poll failed ("attempt to write a readonly database") and ``controller_start`` (which polls first)
    could never start a controller. The poll now rolls it back, and still never creates ``jobs.db``."""
    entry()
    from tuning_factory.graph_store import GraphStore
    state = tmp_path / "state"
    store = GraphStore(state / "jobs.db")
    store.release_owner(store.acquire_owner("desktop.20.1", ttl_seconds=600))
    writer = Spawned(tmp_path, [sys.executable, "-c", HOT_WRITER, str(state / "jobs.db")], "writer")
    try:
        wait_for(lambda: writer.stdout().startswith(b"ready"), "the writer's open transaction")
    finally:
        writer.kill()   # killed mid-transaction: the rollback journal stays hot
    assert (state / "jobs.db-journal").is_file()
    status = action(state, "controller_status")
    assert status["status"] == "ok", status
    # The uncommitted expiry was rolled back, not read: the released owner is absent.
    assert status["data"]["controller"] == {"owner_id": "desktop.20.1", "state": "absent", "heartbeat_age_seconds": None}
    assert not (state / "jobs.db-journal").exists()
    assert action(state, "controller_stop")["data"] == {"requested": False, "reason": "not_running"}
    fresh = tmp_path / "fresh"
    assert action(fresh, "controller_status")["status"] == "ok" and not fresh.exists()   # a poll creates nothing


def test_controller_status_is_bounded_while_the_database_is_locked(tmp_path, monkeypatch):
    """Review 2: one read transaction with one total bound; a locked database is a fixed refusal, never a hang (the
    native ``controller_start`` then starts nothing)."""
    ce = entry()
    from tuning_factory.graph_store import GraphStore
    state = tmp_path / "state"
    GraphStore(state / "jobs.db")
    assert ce.STATUS_TIMEOUT_SECONDS <= 5
    monkeypatch.setattr(ce, "STATUS_TIMEOUT_SECONDS", 0.5)
    blocker = sqlite3.connect(state / "jobs.db", isolation_level=None)
    blocker.execute("BEGIN EXCLUSIVE")
    try:
        for name in ("controller_status", "controller_stop"):
            started = time.monotonic()
            result = action(state, name)
            assert time.monotonic() - started < 4, name
            assert result["status"] == "invalid" and [item["code"] for item in result["issues"]] == ["status_busy"]
            assert result["issues"][0]["message"] == ce.STATUS_BUSY
    finally:
        blocker.rollback()
        blocker.close()
    assert action(state, "controller_status")["status"] == "ok"
    assert not (state / "controller").exists()


def test_restart_required_names_profiles_a_restart_would_now_serve(tmp_path):
    """Review 3: a profile skipped for its observation is reported once a matching observation exists. A profile
    skipped for its key cannot be: a poll carries no ``*_saved`` flag (Task 10 compares ``current.skipped_profiles``
    with ``executor_profiles_get``)."""
    ce = entry()
    from test_gpu_servers import FakeServer

    from tuning_factory.gpu_servers import server_settings
    state = tmp_path / "state"
    lab, nokey = server_start(tmp_path, "lab"), runpod_start(tmp_path, "pod-nokey", key=None, name="No key")
    start = ce.ControllerStart.model_validate(start_message(state, "desktop.23.1", profiles=[lab, nokey]))
    ce.apply_tracking_scope(start.tracking)
    controller, info = ce.build_controller(start)
    owner = controller.acquire_controller(start.owner_id)
    ce.write_current(state, ce.running_record(start.owner_id, info))
    controller.pool.shutdown()
    assert info["skipped_profiles"] == {"lab": "observation_missing", "pod-nokey": "credential_missing"}

    def status():
        result = action(state, "controller_status", profiles=native(lab, nokey))
        assert result["status"] == "ok", result
        return result["data"]

    assert status()["restart_required"]["profiles"] == []
    observed = datetime.now(timezone.utc)
    facts = FakeServer(tmp_path / "remote").facts(server_settings(lab["fields"]))
    other = wp.public_profile("lab", {**lab["fields"], "name": "Another revision"}, facts)
    wp.save_observation(state, other, facts, revision=other.revision, observed_at=observed)
    assert status()["restart_required"]["profiles"] == []   # an observation of another revision serves nothing
    public = wp.public_profile("lab", lab["fields"], facts)
    wp.save_observation(state, public, facts, revision=public.revision, observed_at=observed)
    data = status()
    assert data["restart_required"]["profiles"] == ["lab"]
    assert data["current"]["skipped_profiles"]["pod-nokey"] == "credential_missing"
    controller.store.release_owner(owner)


def test_the_controller_and_planning_skip_a_profile_for_the_same_reason(tmp_path, monkeypatch):
    """9.8 follow-up (Task 11.1 N5): the controller's start, ``controller_status`` and a real ``graph_plan`` decide an
    SSH profile's observation with one function (``workflow_planning.pin_observation``). Probe of 9.8 review 2: a key
    path under ``~``, observed with one home directory and started with another (same fields and revision)."""
    ce = entry()
    import test_workflow_planning as wpt
    from test_gpu_servers import FakeServer
    state = tmp_path / "state"
    homes = [tmp_path / "home-a", tmp_path / "home-b"]
    for home in homes:
        (home / "keys").mkdir(parents=True)

    def home(index):
        for name in ("USERPROFILE", "HOME"):
            monkeypatch.setenv(name, str(homes[index]))

    home(0)
    lab = wpt.server_profile(server_ssh_key_file="~/keys/lab-key")
    found = wpt.scenario(state, tmp_path, monkeypatch, profiles=("lab",))
    FakeServer(tmp_path / "remote").install(monkeypatch)
    inspected = wpt.run(state, "executor_profile_inspect", {"profile_id": "lab"}, profiles=[lab], clock=wpt.NOW)
    assert inspected["status"] == "ok", inspected
    start = ce.ControllerStart.model_validate(start_message(state, "desktop.27.1",
                                                            profiles=[{**lab, "secrets": {}}]))

    def decisions():
        planned = wpt.real_plan(state, found, profile=lab, clock=wpt.NOW + 60)
        plan_codes = [item["code"] for item in planned["issues"] if item["path"] == "/profiles/lab"]
        usable, skipped = ce.provider_profiles(start)
        return plan_codes, sorted(usable), skipped

    assert decisions() == ([], ["lab"], {})
    home(1)   # "~" now names another key file
    assert decisions() == (["observation_revision_differs"], [], {"lab": "observation_revision_differs"})
    # A controller started now skips it for that reason; once "~" names the observed key again, controller_status
    # reports that a restart would serve it (the reason is one the restart check re-decides).
    ce.apply_tracking_scope(start.tracking)
    controller, info = ce.build_controller(start)
    owner = controller.acquire_controller(start.owner_id)
    try:
        ce.write_current(state, ce.running_record(start.owner_id, info))
        controller.pool.shutdown()
        assert info["skipped_profiles"] == {"lab": "observation_revision_differs"}
        home(0)
        status = action(state, "controller_status", profiles=native({**lab, "secrets": {}}))
        assert status["status"] == "ok", status
        assert status["data"]["restart_required"]["profiles"] == ["lab"]
    finally:
        controller.store.release_owner(owner)


def test_a_leftover_stop_request_never_stops_a_new_controller(tmp_path):
    """Review 5: a stop request written before this start (a killed run's, possibly under the same fixed CLI owner
    ID) is removed at the start; a request made after the start still stops the controller."""
    ce = entry()
    state = tmp_path / "state"
    stop_file = state / "controller" / "stop-request.json"
    for leftover in ("cli.fixed", "desktop.24.0"):
        stop_file.parent.mkdir(parents=True, exist_ok=True)
        stop_file.write_text(json.dumps({"owner_id": leftover, "requested_at": time.time() - 60}), encoding="utf-8")
        start = ce.ControllerStart.model_validate(start_message(state, "cli.fixed", settings=FAST))
        stop, result = threading.Event(), []
        thread = threading.Thread(target=lambda: result.append(ce.serve(start, stop_event=stop)), daemon=True)
        thread.start()
        try:
            wait_for(lambda: owner_row(state)["owner_id"] == "cli.fixed" and owner_row(state)["expires"] > time.time()
                     and not stop_file.exists(), "the start to remove the leftover stop request")
            heartbeats_advance(state, 3)   # several stop-request polls later it still runs
            assert thread.is_alive() and result == [], leftover
            ce.request_stop(state, "cli.fixed")
            thread.join(WAIT)
            assert result == [0] and not stop_file.exists()
        finally:
            stop.set()
            thread.join(WAIT)


def test_a_failed_outbox_backfill_releases_the_owner(tmp_path, monkeypatch, capsys):
    """Review 6: ``acquire_controller`` takes the owner lease, then backfills the outbox. A failing backfill used to
    leave the lease held until its TTL with no status; it is released now and the failure recorded."""
    ce = entry()
    from tuning_factory.tracking_outbox import TrackingOutbox

    def broken(self, owner):
        raise RuntimeError("synthetic backfill failure")

    monkeypatch.setattr(TrackingOutbox, "backfill", broken)
    state = tmp_path / "state"
    start = ce.ControllerStart.model_validate(start_message(state, "desktop.25.1", settings=FAST))
    assert ce.serve(start, max_ticks=1) == ce.EXIT_INTERNAL
    row = owner_row(state)
    assert row["owner_id"] == "desktop.25.1" and row["expires"] == 0
    current = current_json(state)
    assert set(current) == {"ok", "code", "error_type", "owner_id", "at"}
    assert (current["ok"], current["code"], current["error_type"]) == (False, "controller_failed", "RuntimeError")
    assert "synthetic" not in json.dumps(current)
    assert capsys.readouterr().out == ""   # no ready line


def wait_for_event(event, thread, result, what, timeout=LOADED_WAIT):
    """Waits for ``event``, set by the code under test, with a bound generous enough for a loaded machine. A thread
    that returned first fails at once with its result; a real hang still fails at the bound."""
    deadline = time.monotonic() + timeout
    while not event.wait(0.05):
        if not thread.is_alive():
            raise AssertionError(f"the controller returned {result} before {what}")
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out after {timeout} s waiting for {what}")


def test_a_drain_fenced_by_a_successor_is_not_a_clean_stop(tmp_path, monkeypatch):
    """Review 9: when the owner lease is fenced during the drain, ``run_forever`` releases nothing; that is a lost
    lease (exit 1, ``owner_lost``) even if a successor already holds the owner row.

    Task 11.1 N6: the first tick is observed through the tick itself. It used to be read as "heartbeat > acquired",
    but the first tick renews the owner lease within milliseconds of acquiring it, and Python 3.12's ``time.time()``
    on Windows has the system timer's resolution (15.625 ms by default): equal timestamps then left the test waiting
    for the next heartbeat (200 s) until its 30 s bound, as seen once under load."""
    ce = entry()
    from tuning_factory.graph_controller import GraphController
    from tuning_factory.graph_store import GraphStore
    state = tmp_path / "state"
    start = ce.ControllerStart.model_validate(start_message(
        state, "desktop.26.1", settings={"lease_ttl_seconds": 600, "tick_seconds": 60, "lease_heartbeat_seconds": 200}))
    ticked, tick = threading.Event(), GraphController.tick

    def observed_tick(self, owner):
        try:
            return tick(self, owner)
        finally:
            ticked.set()

    monkeypatch.setattr(GraphController, "tick", observed_tick)
    stop, result = threading.Event(), []
    thread = threading.Thread(target=lambda: result.append(ce.serve(start, stop_event=stop)), daemon=True)
    thread.start()
    try:
        wait_for_event(ticked, thread, result, "the first tick")
        assert owner_row(state)["owner_id"] == "desktop.26.1"
        store = GraphStore(state / "jobs.db")
        with store.jobs.connection() as db:   # the lease expired while the machine slept ...
            db.execute("UPDATE controller_owner SET expires=?", (time.time() - 1,))
        store.acquire_owner("desktop.26.2", ttl_seconds=600)   # ... and a successor took the owner row
        stop.set()   # the drain's next tick finds the lease fenced
        thread.join(WAIT)
    finally:
        stop.set()
        thread.join(WAIT)
    assert result == [ce.EXIT_INTERNAL]
    assert owner_row(state)["owner_id"] == "desktop.26.2" and owner_row(state)["expires"] > time.time()
    assert current_json(state)["code"] == "owner_lost"


def test_the_git_revision_child_inherits_no_credentials(tmp_path):
    """Review 7: in non-frozen runs the manifest's ``git rev-parse`` child inherited the controller's MLflow token
    and OIDC values. It now gets this process's environment without any credential variable, and no stdin."""
    code = textwrap.dedent("""
        import json, subprocess
        from tuning_factory import manifest
        seen, real = {}, subprocess.run

        def run(*args, **kwargs):
            seen.update(kwargs)
            return real(*args, **kwargs)

        subprocess.run = run
        revision = manifest.code_revision()
        env = seen.get("env") or {}
        print(json.dumps({"names": sorted(env), "values": sorted(env.values()), "revision": revision,
                          "stdin_null": seen.get("stdin") == subprocess.DEVNULL}))
    """)
    env = child_env(tmp_path)
    secret_names = ("MLFLOW_TRACKING_TOKEN", "MLFLOW_OIDC_CLIENT_SECRET", "RUNPOD_API_KEY", "AWS_SECRET_ACCESS_KEY",
                    "HF_TOKEN", "AZURE_CLIENT_SECRET")
    env.update({name: INHERITED for name in secret_names})
    env.update({"MLFLOW_OIDC_TOKEN_URL": "https://idp.invalid/token", "MLFLOW_OIDC_CLIENT_ID": "client-id"})
    process = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, cwd=ROOT,
                             timeout=WAIT)
    assert process.returncode == 0, process.stderr
    seen = json.loads(process.stdout.strip().splitlines()[-1])
    assert seen["names"], "code_revision must pass an explicit environment"
    assert not set(seen["names"]) & set(CREDENTIAL_NAMES) and INHERITED not in seen["values"]
    assert {"PATH", "Path"} & set(seen["names"]) and seen["stdin_null"]
    assert len(seen["revision"]) == 40 or seen["revision"] == "uncommitted"
