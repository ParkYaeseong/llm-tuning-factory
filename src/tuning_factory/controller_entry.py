"""Task 9.4: the graph controller as a stdin-configured background sidecar (plan §4 "9.4"; D2-D5, D7, D14, D19).

``factory-bridge --controller`` (``scripts/desktop_entry.py``) reads exactly one ``factory-controller/1`` line of
at most 1 MiB from stdin, written by the native broker (Task 9.5, ``broker.rs``). The pipe then stays open: EOF
means drain and stop. The process owns one ``GraphController`` and nothing else in it uses MLflow, because MLflow's
bounded-request variables and its authentication are process-wide (Task 8 review):

- ``apply_tracking_scope`` pops every inherited credential variable, then applies the one MLflow scope once, before
  any thread starts (D7). RunPod keys live only in ``ProfileSecrets``; S3 and HF credentials never arrive here.
- ``build_controller`` wires the Task 8 controller exactly as Task 8 §7 says, with the real provider behind
  ``BoundedProvider`` (a time limit on opening a destination; provider error texts lose the keys it holds). The
  ``GraphController`` constructor installs ``GraphController.redact`` as ``JobStore.error_filter``, so no runner
  error text stores a credential.
- ``serve``: settings refusal (exit 4, before any owner lease), the owner lease (a duplicate prints
  ``{"ok": false, "code": "owner_held"}`` and exits 3 without writing a status file), ``current.json``, the EOF and
  stop-request watchers, ``run_forever``'s drain (D4, D14), and the final status. There is no kill-now.
- ``controller_status`` and ``controller_stop`` are workflow actions of the one-request sidecar: DB-only, read-only
  (a poll never creates or migrates ``jobs.db``) and secret-free.

Status files under ``state/controller/`` (D19) hold IDs, revisions, hashes, timestamps, fixed codes and fixed texts
only. Reconcile runs inline on the tick thread (Task 8.5), so a reconcile that hangs inside an open connection
stalls the tick while the heartbeat keeps the controller ``running``; only opening a destination is time-limited
here (see PROGRESS, Task 11).
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import sqlite3
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Annotated, BinaryIO, Literal

from pydantic import Field, SecretStr, field_validator, model_validator

from .auth import (
    INSECURE_TRACKING,
    OIDC_CLIENT_MISSING,
    current_auth_mode,
    require_complete_oidc,
    require_secure_artifacts,
    require_secure_tracking,
)
from .config import StrictModel
from .desktop_workflows import NativeProfile, WorkflowContext, WorkflowResult, ok, workflow_action
from .executor_profiles import ProfileSecrets, SettingsExecutorProvider
from .graph_controller import ControllerSettings, DispatchRouter, GraphController, controller_tracker
from .graph_store import OWNER_HELD, STALE_OWNER, GraphStore
from .projects import LocalId
from .registry import canonical, digest
from .tracking import (
    AUTH_MODES,
    AUTHENTICATED_HTTPS,
    DEFAULT_EXPERIMENT,
    public_tracking_settings,
    tracking_settings_sha256,
)
from .workflow_planning import (
    current_profile_revisions,
    load_observation,
    profile_revision,
    public_profile,
    root_snapshots_dir,
)

START_SCHEMA = "factory-controller/1"
EXIT_OK, EXIT_INTERNAL, EXIT_INVALID_START, EXIT_OWNER_HELD, EXIT_SETTINGS_REFUSED = 0, 1, 2, 3, 4
MAX_START_BYTES = 1024 * 1024        # = broker.rs MAX_START_BYTES: the start line, newline included
INVALID_START = "invalid controller start message"
UNSUPPORTED_ARGUMENTS = "unsupported sidecar arguments"
CREDENTIAL_ENV = ("RUNPOD_API_KEY", "MLFLOW_TRACKING_URI", "MLFLOW_TRACKING_TOKEN", "MLFLOW_TRACKING_USERNAME",
                  "MLFLOW_TRACKING_PASSWORD", "MLFLOW_OIDC_TOKEN_URL", "MLFLOW_OIDC_CLIENT_ID",
                  "MLFLOW_OIDC_CLIENT_SECRET", "FACTORY_MLFLOW_ARTIFACT_LOCATION", "FACTORY_OPERATOR",
                  "S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY", "S3_SESSION_TOKEN", "AWS_ACCESS_KEY_ID",
                  "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "HF_TOKEN", "HUGGING_FACE_HUB_TOKEN")
CONTROLLER_DIR = "controller"
CURRENT_FILE, BROKER_FILE, STOP_FILE = "current.json", "broker.json", "stop-request.json"
MAX_STATUS_BYTES = 1024 * 1024
# Task 8 review: resolve/recover need a time limit. Connecting and the runtime probe take well under a minute; a
# destination that has not opened after this long is retried after the controller's destination backoff.
DESTINATION_TIMEOUT_SECONDS = 300
DESTINATION_TIMEOUT = "the destination did not open within the controller's time limit; it is retried later"
SETTINGS_REFUSED = ("MLflow 설정이 컨트롤러의 보안 규칙에 맞지 않아 컨트롤러를 시작하지 않았습니다. "
                    "MLflow 설정을 확인한 뒤 다시 시작하세요.")
# Fixed refusal texts of the scope checks; anything else is recorded as SETTINGS_REFUSED (never an input value).
_SCOPE_REFUSALS = (INSECURE_TRACKING, OIDC_CLIENT_MISSING, AUTHENTICATED_HTTPS,
                   "tracking settings must not embed credentials")
# Skipped profiles: fixed reason codes, shown by controller_status through current.json.
INVALID_FIELDS, CREDENTIAL_MISSING = "invalid_fields", "credential_missing"
OBSERVATION_MISSING, OBSERVATION_DIFFERS, OBSERVATION_INVALID = (
    "observation_missing", "observation_revision_differs", "observation_invalid")
_PUBLIC_TRACKING = ("MLFLOW_TRACKING_URI", "FACTORY_MLFLOW_ARTIFACT_LOCATION", "FACTORY_OPERATOR", "auth_mode",
                    "MLFLOW_OIDC_TOKEN_URL", "MLFLOW_OIDC_CLIENT_ID")
_OIDC_VALUES = ("MLFLOW_OIDC_TOKEN_URL", "MLFLOW_OIDC_CLIENT_ID", "MLFLOW_OIDC_CLIENT_SECRET")
# main.rs filter_mlflow_auth: token keeps only the token, none keeps nothing, anything else withholds the token.
_WITHHELD = {"token": _OIDC_VALUES, "none": (*_OIDC_VALUES, "MLFLOW_TRACKING_TOKEN")}
_TRACKING_VIEW = "controller-tracking-view/1"
_ProfileId = Annotated[LocalId, Field(max_length=64)]   # NativeProfile.profile_id
_Text = Annotated[str, Field(max_length=2048)]           # main.rs: native strings are at most 2048 characters


# -- the start message ---------------------------------------------------------------------------

class TrackingScope(StrictModel):
    """The one MLflow scope of the controller process: keys = broker.rs ``CONTROLLER_TRACKING_FIELDS``."""
    MLFLOW_TRACKING_URI: _Text = ""
    FACTORY_MLFLOW_ARTIFACT_LOCATION: _Text = ""
    FACTORY_OPERATOR: _Text = ""
    auth_mode: Literal["", "oidc", "token", "none"] = ""
    MLFLOW_OIDC_TOKEN_URL: _Text = ""
    MLFLOW_OIDC_CLIENT_ID: _Text = ""
    MLFLOW_OIDC_CLIENT_SECRET: SecretStr = SecretStr("")
    MLFLOW_TRACKING_TOKEN: SecretStr = SecretStr("")

    @classmethod
    def from_environment(cls) -> TrackingScope:
        """The CLI's scope (Task 9.6): this process's MLflow variables, ``auth_mode`` from ``current_auth_mode``.
        An OIDC token URL without client credentials stays ``oidc``, so the start refuses it as v1 does."""
        values = {name: os.environ.get(name, "") for name in (*_PUBLIC_TRACKING[:3], *_PUBLIC_TRACKING[4:],
                                                              "MLFLOW_OIDC_CLIENT_SECRET", "MLFLOW_TRACKING_TOKEN")}
        mode = current_auth_mode()
        if mode == "none" and values["MLFLOW_OIDC_TOKEN_URL"]:
            mode = "oidc"
        return cls.model_validate({**values, "auth_mode": mode})


class ProfileStart(StrictModel):
    """One executor profile: its public native fields (``NativeProfile``'s allowlist) and its own RunPod key."""
    profile_id: _ProfileId
    fields: dict[str, str]
    secrets: dict[Literal["RUNPOD_API_KEY"], SecretStr] = Field(default_factory=dict)

    @model_validator(mode="after")
    def native_fields(self):
        try:
            NativeProfile.model_validate({"profile_id": self.profile_id, "fields": self.fields})
        except ValueError:
            raise ValueError("executor profile fields are not the native allowlist") from None
        return self


def _seconds(default, low, high):
    return Field(default=default, ge=low, le=high, strict=True)


class StartSettings(StrictModel):
    """Optional; the broker never sends it (tests and the CLI do). The Task 8 ``ControllerSettings`` constraints are
    checked here, so an inconsistent start is an invalid start (exit 2), never an internal error."""
    lease_ttl_seconds: float = _seconds(60, 2, 600)
    tick_seconds: float = _seconds(5, 0.05, 60)
    lease_heartbeat_seconds: float = _seconds(20, 0.5, 300)
    tracking_claim_ttl_seconds: float = _seconds(600, 3, 7200)
    stale_attempt_seconds: float = _seconds(120, 5, 3600)

    @model_validator(mode="after")
    def consistent(self):
        if self.lease_heartbeat_seconds * 2 > self.lease_ttl_seconds:
            raise ValueError("the lease heartbeat must be at most half the lease TTL")
        if self.lease_heartbeat_seconds * 3 > self.tracking_claim_ttl_seconds:
            raise ValueError("the tracking claim TTL must cover at least three lease heartbeats")
        return self

    def controller_settings(self) -> ControllerSettings:
        return ControllerSettings(lease_ttl_seconds=self.lease_ttl_seconds, tick_seconds=self.tick_seconds,
                                  lease_heartbeat_seconds=self.lease_heartbeat_seconds,
                                  tracking_claim_ttl_seconds=self.tracking_claim_ttl_seconds,
                                  stale_attempt_seconds=self.stale_attempt_seconds)


class ControllerStart(StrictModel):
    """The ``factory-controller/1`` line. The wire name ``schema`` is the only accepted spelling; ``repr`` and
    ``str`` name only the owner and the profile IDs."""
    schema_: Literal["factory-controller/1"] = Field(alias="schema")
    state_root: str = Field(min_length=1, max_length=4096)
    owner_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")   # graph_store.OWNER_ID
    tracking: TrackingScope = Field(default_factory=TrackingScope)
    profiles: tuple[ProfileStart, ...] = Field(default=(), max_length=32)
    settings: StartSettings = Field(default_factory=StartSettings)

    @field_validator("state_root")
    @classmethod
    def local_absolute(cls, value):
        """An absolute local path: no UNC or device prefix, no ``..`` and no control character (as broker.rs)."""
        if (any(ord(char) < 32 or ord(char) == 127 for char in value) or re.match(r"[\\/]{2}", value)
                or ".." in re.split(r"[\\/]", value) or not Path(value).is_absolute()):
            raise ValueError("the state root must be a local absolute path")
        return value

    @model_validator(mode="after")
    def unique_profiles(self):
        # Windows file names and Credential Manager entries ignore case and trailing dots (9.1 M9).
        if len({item.profile_id.casefold().rstrip(".") for item in self.profiles}) != len(self.profiles):
            raise ValueError("duplicate executor profile ID")
        return self

    def __repr__(self):
        return (f"ControllerStart(owner_id={self.owner_id!r}, "
                f"profiles={[item.profile_id for item in self.profiles]!r})")

    __str__ = __repr__


def _unique_keys(pairs):
    data = {}
    for key, value in pairs:
        if key in data:
            raise ValueError("duplicate JSON key")
        data[key] = value
    return data


def _no_constant(name):
    raise ValueError("non-finite JSON number")


def _parse_start(stream: BinaryIO) -> ControllerStart:
    line = stream.readline(MAX_START_BYTES + 1)
    if not isinstance(line, bytes) or len(line) > MAX_START_BYTES or not line.endswith(b"\n"):
        raise ValueError(INVALID_START)   # empty (EOF), unterminated, or longer than 1 MiB
    data = json.loads(line.decode("utf-8"), object_pairs_hook=_unique_keys, parse_constant=_no_constant)
    if not isinstance(data, dict):
        raise ValueError(INVALID_START)
    return ControllerStart.model_validate(data)


def read_start(stream: BinaryIO) -> ControllerStart:
    """Exactly one UTF-8 JSON line of at most ``MAX_START_BYTES``. Anything else raises ``ValueError(INVALID_START)``
    without a cause or context, so no input value reaches a message or a traceback. Later lines are never read."""
    try:
        return _parse_start(stream)
    except Exception:
        pass
    raise ValueError(INVALID_START) from None


# -- the one MLflow scope (D7) ------------------------------------------------------------------

def _settings_text(exc: Exception) -> str:
    return str(exc) if str(exc) in _SCOPE_REFUSALS else SETTINGS_REFUSED


def apply_tracking_scope(scope: TrackingScope) -> None:
    """Pop every inherited credential variable, set this scope once (filtered by ``auth_mode`` as main.rs
    ``filter_mlflow_auth`` does; ``none`` drops all four MLflow auth values even if sent), then apply the MLflow
    HTTPS rule: ``require_secure_tracking``, ``require_secure_artifacts``, ``require_complete_oidc`` and the D11
    public view. A refusal raises ``ValueError`` with a fixed text and leaves no credential variable set."""
    for name in CREDENTIAL_ENV:
        os.environ.pop(name, None)
    values = {name: getattr(scope, name) for name in _PUBLIC_TRACKING if name != "auth_mode"}
    values["MLFLOW_OIDC_CLIENT_SECRET"] = scope.MLFLOW_OIDC_CLIENT_SECRET.get_secret_value()
    values["MLFLOW_TRACKING_TOKEN"] = scope.MLFLOW_TRACKING_TOKEN.get_secret_value()
    for name in _WITHHELD.get(scope.auth_mode, ("MLFLOW_TRACKING_TOKEN",)):
        values.pop(name)
    try:
        for name, value in values.items():
            if value:
                os.environ[name] = value
        uri, location = values["MLFLOW_TRACKING_URI"], values["FACTORY_MLFLOW_ARTIFACT_LOCATION"]
        require_secure_tracking(uri or None)
        require_secure_artifacts(location or None)
        require_complete_oidc()
        if uri.strip():   # exactly what the controller's lazy tracker will report (D11)
            public_tracking_settings(uri, DEFAULT_EXPERIMENT, location or None)
    except Exception:
        for name in CREDENTIAL_ENV:
            os.environ.pop(name, None)
        raise


def _text(values: Mapping, name: str) -> str:
    value = values.get(name)
    return value if isinstance(value, str) else ""


def tracking_view_sha256(values: Mapping) -> str:
    """The public tracking view of a settings mapping, hashed (it is compared, never stored in clear): the URI, the
    artifact location, the operator, ``auth_mode`` and, when the mode uses OIDC, the OIDC token URL and client ID.
    Never a credential. The same function hashes the start's scope and a ``controller_status`` request's settings."""
    mode = _text(values, "auth_mode")
    view = {name: _text(values, name) for name in _PUBLIC_TRACKING[:4]}
    if mode not in ("token", "none"):
        view.update({name: _text(values, name) for name in _OIDC_VALUES[:2]})
    return digest(canonical({"schema": _TRACKING_VIEW, **view}))


def _tracking_record(scope: TrackingScope) -> dict:
    """What ``current.json`` keeps of the scope: the view's hash, the saved ``auth_mode`` and the auth mode this
    process actually applied (``current_auth_mode`` after ``apply_tracking_scope``)."""
    return {"view_sha256": tracking_view_sha256({name: getattr(scope, name) for name in _PUBLIC_TRACKING}),
            "auth_mode": scope.auth_mode, "effective_auth_mode": current_auth_mode()}


# -- profiles and the controller ----------------------------------------------------------------

def provider_profiles(start: ControllerStart) -> tuple[dict[str, ProfileSecrets], dict[str, str]]:
    """``(usable, skipped)``: ``ProfileSecrets`` by profile ID, and ``{profile_id: reason}`` for the others.

    RunPod profiles are built offline with ``public_profile`` and need their own key; SSH profiles need a stored
    observation of the same revision (its facts build the profile). Reasons: ``invalid_fields``,
    ``credential_missing``, ``observation_missing``, ``observation_revision_differs``, ``observation_invalid``.
    """
    state = Path(start.state_root)
    usable, skipped = {}, {}
    for item in start.profiles:
        profile_id, fields = item.profile_id, dict(item.fields)
        try:
            revision = profile_revision(profile_id, fields)
        except ValueError:
            skipped[profile_id] = INVALID_FIELDS
            continue
        settings = dict(fields)
        if fields.get("provider") == "existing_runpod":
            key = item.secrets.get("RUNPOD_API_KEY")
            value = key.get_secret_value() if key is not None else ""
            if not value:
                skipped[profile_id] = CREDENTIAL_MISSING
                continue
            try:
                public = public_profile(profile_id, fields)
            except ValueError:
                skipped[profile_id] = INVALID_FIELDS
                continue
            settings["RUNPOD_API_KEY"] = value
        else:
            try:
                observation = load_observation(state, profile_id)
            except ValueError:
                skipped[profile_id] = OBSERVATION_INVALID
                continue
            if observation is None:
                skipped[profile_id] = OBSERVATION_MISSING
                continue
            if observation["revision"] != revision:
                skipped[profile_id] = OBSERVATION_DIFFERS
                continue
            try:
                public = public_profile(profile_id, fields, observation["facts"])
            except ValueError:
                skipped[profile_id] = INVALID_FIELDS
                continue
            if public != observation["profile"]:
                skipped[profile_id] = OBSERVATION_INVALID
                continue
        usable[profile_id] = ProfileSecrets(public=public, settings=settings)
    return usable, skipped


def _sanitized(exc: BaseException, redact: Callable[[str], str]) -> Exception | None:
    """A replacement for ``exc`` without the provider's credentials, or None when its text holds none. A fixed
    refusal (a ``ValueError``) stays a ``ValueError``, so the controller still shows it as written."""
    if not isinstance(exc, Exception):
        return None
    text = str(exc)
    clean = redact(text)
    if clean == text:
        return None
    return ValueError(clean) if isinstance(exc, ValueError) else RuntimeError(f"{type(exc).__name__}: {clean}")


class _BoundedOpen:
    """Enter a provider context on a helper thread and give up after ``timeout`` seconds (``TimeoutError``). A
    destination that opens after the caller gave up is closed on that thread at once, never left open."""

    def __init__(self, factory, timeout: float, redact: Callable[[str], str]):
        self._factory, self._timeout, self._redact = factory, timeout, redact
        self._context = None

    def __enter__(self):
        done, lock, box = threading.Event(), threading.Lock(), {}

        def open_destination():
            try:
                context = self._factory()
                value = context.__enter__()
            except BaseException as exc:
                with lock:
                    box["error"] = exc
                done.set()
                return
            with lock:
                late = box.get("abandoned", False)
                if not late:
                    box["context"], box["value"] = context, value
            done.set()
            if late:
                with contextlib.suppress(BaseException):
                    context.__exit__(None, None, None)

        threading.Thread(target=open_destination, name="controller-destination-open", daemon=True).start()
        done.wait(self._timeout)
        with lock:
            if "context" not in box and "error" not in box:
                box["abandoned"] = True
                raise TimeoutError(DESTINATION_TIMEOUT)
            error = box.get("error")
        if error is not None:
            clean = _sanitized(error, self._redact)
            if clean is not None:
                raise clean from None
            raise error
        self._context = box["context"]
        return box["value"]

    def __exit__(self, exc_type, exc, traceback):
        try:
            return self._context.__exit__(exc_type, exc, traceback)
        except BaseException as error:
            clean = None if error is exc else _sanitized(error, self._redact)
            if clean is None:
                raise
            raise clean from None


class BoundedProvider:
    """The real ``ExecutorProvider`` with a time limit on opening a destination (``resolve``/``recover``) and
    provider-redacted error texts. ``redact`` and ``status`` are the provider's own."""

    def __init__(self, provider, *, timeout_seconds: float = DESTINATION_TIMEOUT_SECONDS):
        self.provider, self.timeout_seconds = provider, timeout_seconds

    def __repr__(self):
        return f"BoundedProvider({self.provider!r}, timeout_seconds={self.timeout_seconds!r})"

    def redact(self, text) -> str:
        return self.provider.redact(text)

    def status(self) -> dict:
        return self.provider.status()

    def resolve(self, *args, **kwargs):
        return _BoundedOpen(lambda: self.provider.resolve(*args, **kwargs), self.timeout_seconds, self.redact)

    def recover(self, *args, **kwargs):
        return _BoundedOpen(lambda: self.provider.recover(*args, **kwargs), self.timeout_seconds, self.redact)


def build_controller(start: ControllerStart, *, clock: Callable[[], float] = time.time) -> tuple[GraphController,
                                                                                                    dict]:
    """The Task 8 controller wired as Task 8 §7 says, and the public facts ``current.json`` records about it.

    Call it after ``apply_tracking_scope``: the tracker reads the applied scope. The real attempt runner (it
    imports paramiko) is imported here, so the polling sidecar that serves ``controller_status`` never loads it.
    """
    from .attempt_runner import OperationAttemptRunner
    from .simulated_dispatch import DirectoryRootSnapshots, SimulatedOperationDispatcher
    state = Path(start.state_root)
    store = GraphStore(state / "jobs.db", clock=clock)
    usable, skipped = provider_profiles(start)
    provider = BoundedProvider(SettingsExecutorProvider(usable, state, clock=clock))
    dispatcher = DispatchRouter(
        OperationAttemptRunner(store, root=state),
        SimulatedOperationDispatcher(store, root=state, roots=DirectoryRootSnapshots(root_snapshots_dir(state))),
        store=store)
    uri = start.tracking.MLFLOW_TRACKING_URI
    tracker = controller_tracker(uri) if uri.strip() else None
    controller = GraphController(store, provider, dispatcher, tracker, clock,
                                 settings=start.settings.controller_settings())
    info = {"profiles": {item.profile_id: profile_revision(item.profile_id, item.fields) for item in start.profiles},
            "skipped_profiles": dict(sorted(skipped.items())),
            "tracking": _tracking_record(start.tracking),
            # LazyMLflowTracker.public_settings never builds the client (D11): the hash plans must pin.
            "tracking_settings_sha256": tracking_settings_sha256(tracker.public_settings()) if tracker else None}
    return controller, info


# -- status files (D19) --------------------------------------------------------------------------

def controller_dir(state_dir: Path) -> Path:
    return Path(state_dir) / CONTROLLER_DIR


def _write_json(path: Path, data: dict) -> None:
    """Atomic: a reader sees the complete old or new file. Windows refuses a replace while another process has the
    file open, so the replace is retried briefly."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(canonical(data))
            stream.flush()
            os.fsync(stream.fileno())
        for attempt in range(40):
            try:
                os.replace(temporary, path)
                return
            except PermissionError:
                if attempt == 39:
                    raise
                time.sleep(0.05)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _read_json(path: Path):
    try:
        with open(path, "rb") as stream:
            raw = stream.read(MAX_STATUS_BYTES + 1)
        if len(raw) > MAX_STATUS_BYTES:
            return None
        data = json.loads(raw)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def write_current(state_dir: Path, record: dict) -> None:
    _write_json(controller_dir(state_dir) / CURRENT_FILE, record)


def read_current(state_dir: Path) -> dict | None:
    return _read_json(controller_dir(state_dir) / CURRENT_FILE)


def running_record(owner_id: str, info: dict, *, clock: Callable[[], float] = time.time) -> dict:
    """``current.json`` of a controller that holds the owner lease: IDs, revisions, hashes and fixed codes."""
    return {"ok": True, "owner_id": owner_id, "pid": os.getpid(), "started_at": clock(), **info}


def _update_current(state_dir: Path, owner_id: str, changes: dict) -> None:
    """Only while ``current.json`` still describes this owner: a successor's status is never overwritten."""
    current = read_current(state_dir)
    if current is not None and current.get("owner_id") == owner_id:
        write_current(state_dir, {**current, **changes})


def request_stop(state_dir: Path, owner_id: str) -> None:
    """D4: ask the controller ``owner_id`` to drain and stop (``state/controller/stop-request.json``, atomic)."""
    _write_json(controller_dir(state_dir) / STOP_FILE, {"owner_id": owner_id, "requested_at": time.time()})


def stop_requested(state_dir: Path, owner_id: str) -> bool:
    data = _read_json(controller_dir(state_dir) / STOP_FILE)
    return bool(data) and data.get("owner_id") == owner_id


def _remove_stop_request(state_dir: Path, owner_id: str) -> None:
    if stop_requested(state_dir, owner_id):
        with contextlib.suppress(OSError):
            (controller_dir(state_dir) / STOP_FILE).unlink()


def _db_view(state_dir: Path, now: float) -> tuple[dict, int]:
    """``graph_status(...)["controller"]`` and the number of RUNNING or PAUSED graph runs, read through a read-only
    connection: a poll never creates, migrates or write-locks ``jobs.db``, and never reads every run's status."""
    path = Path(state_dir) / "jobs.db"
    owner, active = None, 0
    if path.is_file():
        db = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=10)
        try:
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "controller_owner" in tables:
                owner = db.execute("SELECT owner_id,heartbeat,expires FROM controller_owner WHERE id=1").fetchone()
            if "graph_runs" in tables:
                active = db.execute("SELECT COUNT(*) FROM graph_runs WHERE status IN ('RUNNING','PAUSED')"
                                    ).fetchone()[0]
        finally:
            db.close()
    # Exactly graph_controller.graph_status's controller block (a parity test pins it).
    if owner is None or owner[2] <= 0:
        controller = {"owner_id": owner[0] if owner else None, "state": "absent", "heartbeat_age_seconds": None}
    else:
        controller = {"owner_id": owner[0], "state": "running" if owner[2] > now else "stale",
                      "heartbeat_age_seconds": round(max(0.0, now - owner[1]), 3)}
    return controller, int(active)


def _live_owner(state_dir: Path, now: float) -> bool | None:
    try:
        return _db_view(state_dir, now)[0]["state"] == "running"
    except Exception:
        return None


def _record_unowned(state_dir: Path, record: dict, now: float) -> None:
    """``current.json`` for a start that holds no owner lease (refused or failed before it): written only when no
    live controller owns this state directory, so a running controller's status is never replaced."""
    if _live_owner(state_dir, now) is False:
        with contextlib.suppress(OSError):
            write_current(state_dir, record)


# -- serving -------------------------------------------------------------------------------------

def _emit(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=True) + "\n")
    sys.stdout.flush()


@contextlib.contextmanager
def _quiet_stdout():
    """After the ready line nothing is printed: stray library output goes to the null device."""
    previous = sys.stdout
    with open(os.devnull, "w", encoding="utf-8") as null:
        sys.stdout = null
        try:
            yield
        finally:
            sys.stdout = previous


def _detach_lifeline(stream: BinaryIO) -> BinaryIO:
    """Move the broker's pipe off standard input after the start line, and return it as a raw, non-inheritable
    stream (the lifeline). Standard input becomes the null device.

    On Windows a thread blocked in a synchronous read of a pipe makes every child process that inherits that pipe
    as its standard input hang (``git rev-parse`` for a manifest's code revision did); and a buffered reader's lock
    held by that thread at interpreter shutdown is a fatal error. With the pipe on its own descriptor, children get
    the null device and the reader thread holds no lock of ``sys.stdin``."""
    try:
        descriptor = stream.fileno()
    except (AttributeError, OSError, ValueError):
        return stream
    lifeline = os.dup(descriptor)   # non-inheritable: no child process ever holds the lifeline
    null = os.open(os.devnull, os.O_RDONLY)
    try:
        os.dup2(null, descriptor)   # on Windows this also updates the process's standard input handle
    finally:
        os.close(null)
    return os.fdopen(lifeline, "rb", buffering=0)


def _drain_on_eof(stdin: BinaryIO, stop_event: threading.Event) -> None:
    """D2: the broker writes nothing after the start line; EOF (the broker exited or closed the pipe) means stop.
    Anything else read is discarded in bounded chunks; a read error counts as EOF. A real stream is read through
    its descriptor (see ``_detach_lifeline``); EOF on a pipe persists, so nothing buffered can hide it."""
    try:
        descriptor = stdin.fileno()
    except (AttributeError, OSError, ValueError):
        descriptor = None
    try:
        if descriptor is None:
            while stdin.read(65536):
                pass
        else:
            while os.read(descriptor, 65536):
                pass
    except Exception:
        pass
    finally:
        stop_event.set()


def _poll_stop_request(state_dir: Path, owner_id: str, stop_event: threading.Event, interval: float) -> None:
    while not stop_event.wait(interval):
        if stop_requested(state_dir, owner_id):
            stop_event.set()


def _drained(store: GraphStore, owner, stop_event: threading.Event) -> bool:
    """True when ``run_forever`` drained and released the owner lease; False when it returned because the lease was
    fenced (lost after a sleep, or taken over), which it does only without a stop or without a release."""
    if not stop_event.is_set():
        return False
    try:
        row = store.owner_state()
    except Exception:
        return False
    if row is None:
        return False
    return row["owner_id"] != owner.owner_id or row["expires"] == 0


def serve(start: ControllerStart, *, stdin: BinaryIO | None = None, stop_event: threading.Event | None = None,
          clock: Callable[[], float] = time.time, max_ticks: int | None = None) -> int:
    """Run one controller until EOF on ``stdin``, a stop request naming this owner, or ``stop_event``; then drain.

    Exit codes (broker.rs maps them into ``broker.json``): 0 drained stop, 1 internal error or a lost owner lease,
    3 ``owner_held`` (another controller runs; nothing written), 4 settings refused (before any owner lease). With
    ``max_ticks`` (the CLI's tests) it ticks that many times, then drains as after a stop.
    """
    state = Path(start.state_root)
    stop_event = stop_event or threading.Event()
    controller = owner = None
    serving = False
    try:
        state.mkdir(parents=True, exist_ok=True)
        try:
            apply_tracking_scope(start.tracking)
        except ValueError as exc:
            _record_unowned(state, {"ok": False, "code": "settings_refused", "error": _settings_text(exc),
                                    "owner_id": start.owner_id, "at": clock()}, clock())
            _emit({"ok": False, "code": "settings_refused"})
            return EXIT_SETTINGS_REFUSED
        controller, info = build_controller(start, clock=clock)
        try:
            owner = controller.acquire_controller(start.owner_id)
        except ValueError as exc:
            if str(exc) != OWNER_HELD:
                raise
            _emit({"ok": False, "code": "owner_held"})
            return EXIT_OWNER_HELD
        write_current(state, running_record(owner.owner_id, info, clock=clock))
        _emit({"ok": True, "owner_id": owner.owner_id, "pid": os.getpid()})
        with _quiet_stdout():
            if stdin is not None:   # daemon thread, never joined: a blocked read never delays the shutdown
                threading.Thread(target=_drain_on_eof, args=(stdin, stop_event), name="controller-stdin",
                                 daemon=True).start()
            threading.Thread(target=_poll_stop_request, name="controller-stop-request", daemon=True,
                             args=(state, owner.owner_id, stop_event, controller.settings.tick_seconds)).start()
            serving = True
            for _ in range(max_ticks or 0):
                if stop_event.is_set():
                    break
                try:
                    controller.tick(owner)
                except ValueError as exc:
                    if str(exc) == STALE_OWNER:
                        break   # run_forever sees the fenced owner and releases nothing
                except Exception:
                    pass        # run_forever's drain tick reports anything that persists
                stop_event.wait(controller.settings.tick_seconds)
            if max_ticks is not None:
                stop_event.set()
            controller.run_forever(stop_event)   # drains in-flight attempts, then releases the owner unless fenced
        drained = _drained(controller.store, owner, stop_event)
        with contextlib.suppress(OSError):
            _update_current(state, owner.owner_id, {"stopped_at": clock(), "code": "stopped" if drained
                                                    else "owner_lost"})
        _remove_stop_request(state, owner.owner_id)
        return EXIT_OK if drained else EXIT_INTERNAL
    except Exception as exc:
        if owner is not None:
            with contextlib.suppress(Exception):
                _update_current(state, owner.owner_id, {"ok": False, "code": "controller_failed",
                                                        "error_type": type(exc).__name__, "stopped_at": clock()})
            if not serving:
                with contextlib.suppress(Exception):
                    controller.store.release_owner(owner)
        else:
            with contextlib.suppress(Exception):
                _record_unowned(state, {"ok": False, "code": "controller_failed", "error_type": type(exc).__name__,
                                        "owner_id": start.owner_id, "at": clock()}, clock())
        return EXIT_INTERNAL
    finally:
        if controller is not None and not serving:
            with contextlib.suppress(Exception):
                controller.pool.shutdown()


def main() -> int:
    """``desktop_entry --controller``: read the start line, then serve. An invalid start prints one fixed line."""
    stream = getattr(sys.stdin, "buffer", None)
    try:
        if stream is None:
            raise ValueError(INVALID_START)
        start = read_start(stream)
    except ValueError:
        _emit({"ok": False, "code": "invalid_start", "error": INVALID_START})
        return EXIT_INVALID_START
    try:
        lifeline = _detach_lifeline(stream)
    except OSError:
        return EXIT_INTERNAL
    return serve(start, stdin=lifeline)


# -- workflow actions (the one-request sidecar; Rust sends them without Credential Manager access) --------------

class ControllerStatusRequest(StrictModel):
    pass


class ControllerStopRequest(StrictModel):
    pass


def _profiles_restart(recorded, current: Mapping[str, str]) -> list[str]:
    """Profile IDs whose native revision differs from the revision the controller snapshotted (added, changed or
    deleted). Revisions never include a key, so a rotated key needs no restart."""
    started = ({key: value for key, value in recorded.items() if isinstance(key, str) and isinstance(value, str)}
               if isinstance(recorded, dict) else {})
    return sorted(profile_id for profile_id in set(started) | set(current)
                  if started.get(profile_id) != current.get(profile_id))


def _tracking_restart(recorded, settings: Mapping) -> bool | None:
    """Whether the saved public tracking settings differ from what the controller applied, from public fields only
    (a poll carries no ``*_saved`` flag and no vault data). True when the view differs, or when an explicit
    ``auth_mode`` differs from the applied auth mode (e.g. a token saved after the start). The legacy empty
    ``auth_mode`` skips that second check: its applied mode depends on saved credentials a poll cannot see, so it
    never reports a permanent false restart. None when ``current.json`` has no recorded view."""
    if (not isinstance(recorded, dict) or not isinstance(recorded.get("view_sha256"), str)
            or recorded.get("effective_auth_mode") not in AUTH_MODES):
        return None
    if tracking_view_sha256(settings) != recorded["view_sha256"]:
        return True
    requested = _text(settings, "auth_mode")
    return requested in AUTH_MODES and requested != recorded["effective_auth_mode"]


@workflow_action("controller_status", ControllerStatusRequest)
def controller_status(context: WorkflowContext, request: ControllerStatusRequest) -> WorkflowResult:
    """The controller's DB heartbeat state (``graph_status``'s ``controller``), the public status files, and what a
    restart would change (D5). ``restart_required`` is filled only for the running owner that wrote
    ``current.json``."""
    state = Path(context.state_dir)
    controller, active = _db_view(state, context.clock())
    current, broker = read_current(state), _read_json(controller_dir(state) / BROKER_FILE)
    owner_id = controller["owner_id"]
    restart = {"profiles": [], "tracking": False}
    if (controller["state"] == "running" and current is not None and current.get("ok") is True
            and current.get("owner_id") == owner_id):
        restart = {"profiles": _profiles_restart(current.get("profiles"), current_profile_revisions(context)),
                   "tracking": _tracking_restart(current.get("tracking"), context.settings)}
    return ok({"controller": controller, "current": current, "broker": broker,
               "stop_requested": bool(owner_id) and stop_requested(state, owner_id),
               "restart_required": restart, "runs": {"active": active}})


@workflow_action("controller_stop", ControllerStopRequest)
def controller_stop(context: WorkflowContext, request: ControllerStopRequest) -> WorkflowResult:
    """D4: a stop request for the running controller, which drains and releases its owner lease. No kill-now."""
    state = Path(context.state_dir)
    controller, _ = _db_view(state, context.clock())
    if controller["state"] != "running":
        return ok({"requested": False, "reason": "not_running"})
    request_stop(state, controller["owner_id"])
    return ok({"requested": True, "owner_id": controller["owner_id"]})
