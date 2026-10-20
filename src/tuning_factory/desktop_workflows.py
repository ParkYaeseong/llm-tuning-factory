"""Strict, least-privilege workflow channel of the desktop sidecar (Task 9).

One request per process, like the legacy channel, but every request is a frozen strict model, settings are the
public native view (credentials refused), secrets are scoped per action, and results use one issue envelope
(``WorkflowResult``). The channel never sets ``os.environ``, never builds ``DesktopService`` and never contacts a
provider, SSH host, HF or MLflow from the project, recipe and data actions.

Action handlers live in the module ``ACTION_MODULES`` names for them and register with ``workflow_action``;
the table is closed, so later sub-tasks add handlers without editing this module.
"""

import importlib
import json
import re
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Annotated, Literal, NamedTuple, get_args

from pydantic import BaseModel, Field, JsonValue, ValidationError, field_validator, model_validator

from .config import AIConfig, StrictModel
from .dataset_import import ImportMapping, ImportService
from .dataset_splits import SplitPlan, commit_split, preview_split
from .projects import DatasetBinding, DatasetRef, LocalId, ProjectSpec, ProjectStore, Revision, Sha256
from .recipes import RecipeSpec, RecipeStore, recipe_from_config
from .registry import Registry, digest

WORKFLOW_CHANNEL = "workflow"
WORKFLOW_ACTIONS: tuple[str, ...] = (
    # 9.1
    "projects_list", "project_create", "project_get", "project_save", "project_import_legacy",
    "recipes_list", "recipe_get", "recipe_save", "recipe_clone",
    "dataset_import_source", "dataset_import_preview", "dataset_import_register",
    "dataset_split_preview", "dataset_split_commit",
    # 9.2
    "executor_profile_inspect", "model_root_snapshot", "model_roots_list", "benchmark_save", "benchmarks_list",
    "graph_validate", "graph_plan", "graph_plan_get",
    # 9.3
    "graph_submit", "graph_runs_list", "graph_run_get", "graph_pause", "graph_resume", "graph_cancel",
    "tracking_rearm", "executor_profile_edit_check",
    # 9.4
    "controller_status", "controller_stop",
    # 9.7
    "model_root_targets",
)
_OWNERS = (("tuning_factory.desktop_workflows", WORKFLOW_ACTIONS[:14]),
           ("tuning_factory.workflow_planning", WORKFLOW_ACTIONS[14:22]),
           ("tuning_factory.workflow_runs", WORKFLOW_ACTIONS[22:30]),
           ("tuning_factory.controller_entry", WORKFLOW_ACTIONS[30:32]),
           ("tuning_factory.hf_targets", WORKFLOW_ACTIONS[32:]))
# Fixed here for every sub-task: an action is served only by the module named for it.
ACTION_MODULES: Mapping[str, str] = MappingProxyType(
    {name: module for module, names in _OWNERS for name in names})
SECRET_SCOPES: Mapping[str, frozenset[str]] = MappingProxyType({
    "executor_profile_inspect": frozenset({"RUNPOD_API_KEY"}),
    "model_root_snapshot": frozenset({"HF_TOKEN"}),
    "model_root_targets": frozenset({"HF_TOKEN"}),
})
SECRET_FIELDS = ("RUNPOD_API_KEY", "MLFLOW_OIDC_CLIENT_SECRET", "MLFLOW_TRACKING_TOKEN", "S3_ACCESS_KEY_ID",
                 "S3_SECRET_ACCESS_KEY", "S3_SESSION_TOKEN", "HF_TOKEN")          # = main.rs SECRETS
# Credential names refused as settings or data keys: the vault names plus their environment spellings.
CREDENTIAL_NAMES = frozenset(name.casefold() for name in (
    *SECRET_FIELDS, "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "HUGGING_FACE_HUB_TOKEN",
    "MLFLOW_TRACKING_PASSWORD"))
KEY_MARKERS = ("PRIVATE KEY", "PuTTY-User-Key-File-", "Private-Lines:")             # = main.rs pasted_key
KEY_FILE_FIELDS = ("ssh_key_file", "server_ssh_key_file")                         # = main.rs KEY_FILE_FIELDS
RUNPOD_PROFILE_FIELDS = ("provider", "name", "pod_id", "ssh_fingerprint", "ssh_key_file")
SERVER_PROFILE_FIELDS = ("provider", "name", "server_ssh_host", "server_ssh_port", "server_ssh_username",
                         "server_ssh_fingerprint", "server_remote_root", "server_ssh_key_file",
                         "server_total_hourly_cost_usd", "server_selected_gpu_uuids")
PROFILE_FIELDS: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {"existing_runpod": RUNPOD_PROFILE_FIELDS, "ssh_server": SERVER_PROFILE_FIELDS})
# Applied with fullmatch to a field name and to each of its snake/camel-case words (see command_like_field).
FORBIDDEN_FIELD = re.compile(r"(?i)command|cmd|exec|executable|script|shell|argv|program|entrypoint")
CREDENTIALS_REFUSED = "workflow requests must not carry credentials"
SCOPE_REFUSED = "this workflow action does not accept these credentials"
UNSUPPORTED_ACTION = "unsupported workflow action"
INVALID_ENVELOPE = "invalid workflow request"
NOT_AVAILABLE = "이 기능은 아직 설치되지 않았습니다."
LOCAL_FILE_ONLY = "이 PC의 로컬 파일 경로만 가져올 수 있습니다."
FILE_UNREADABLE = "이 PC에서 파일을 읽을 수 없습니다. 경로와 읽기 권한을 확인하세요."
SOURCE_UNKNOWN = "가져온 원본을 찾을 수 없습니다. 파일을 다시 가져오세요."
DATASET_UNKNOWN = "등록된 데이터 버전을 찾을 수 없습니다."
PREVIEW_UNKNOWN = "분할 미리보기를 찾을 수 없습니다. 분할을 다시 미리보세요."
LEGACY_PROFILE_MISSING = "저장된 기존 학습 설정 프로필을 찾을 수 없습니다."
LEGACY_PROFILE_INVALID = "저장된 기존 학습 설정 프로필을 읽을 수 없습니다."
LEGACY_DATASET_MISSING = ("기존 프로필의 데이터 버전을 프로젝트에 연결하지 못했습니다. "
                          "데이터를 등록한 뒤 프로젝트에서 직접 연결하세요.")
SPLIT_PREVIEW_CHANGED = "split preview hash mismatch or dataset origins changed"
# registry.immutable_write: the same path with other bytes. A version conflict only in dataset_import_register.
IMMUTABLE_EXISTS = "immutable version already exists"
CONFLICTS: Mapping[str, str] = MappingProxyType({   # exact domain texts (F12, F13, F18) -> code; prefix match
    "definition revision conflict": "revision_conflict",
    "request ID reused with different project details": "request_reused",
    "request ID reused with different plan": "request_reused",
    "import preview changed; inspect the mapping again": "preview_changed",
    "unknown or changed import preview": "preview_changed",
    SPLIT_PREVIEW_CHANGED: "preview_changed",
    "immutable dataset version already registered": "version_exists",
    "only a running graph can pause": "run_state", "only a paused graph can resume": "run_state",
    "canceled graph cannot resume": "run_state", "a finished graph cannot be canceled": "run_state",
    "only a failed or uncertain tracking event can be re-armed": "tracking_state",
})
INVALID_CODES: Mapping[str, str] = MappingProxyType({   # exact domain texts -> invalid code; prefix match
    "dataset exceeds 32 MiB limit": "size_limit", "import has validation issues": "import_issues",
    "unknown definition": "unknown_definition",          # also covers "unknown definition revision"
    "unknown graph run": "run_unknown", "unknown tracking event": "tracking_event_unknown",
    LOCAL_FILE_ONLY: "local_file_only",
})
_DEFINITIONS_FILE = "workflow-revisions.sqlite3"     # projects._RevisionStore's database name
_DEVICE_NAMES = frozenset({"con", "prn", "aux", "nul", "conin$", "conout$",
                           *(f"{port}{digit}" for port in ("com", "lpt") for digit in "0123456789¹²³")})


_COMMAND_WORD = re.compile(r"(?i)(?:" + FORBIDDEN_FIELD.pattern.removeprefix("(?i)") + r")(?:s|line|\d+)?")


def command_like_field(name: str) -> bool:
    """True if a field name, the name without separators, or one of its snake/camel-case words is a command
    word, optionally with a plural, ``line`` or digit suffix (D12): ``run_commands``, ``cmdline``, ``entry_point``,
    ``argv0``. Words, not substrings: ``description`` and 9.3's ``approve_execution`` are data.
    """
    words = re.split(r"[^A-Za-z0-9]+|(?<=[a-z0-9])(?=[A-Z])", name)
    joined = re.sub(r"[^A-Za-z0-9]+", "", name)
    return any(word and _COMMAND_WORD.fullmatch(word) for word in (name, joined, *words))


def _models_in(annotation, depth=0):
    if depth > 20:
        return
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        yield annotation
        return
    for argument in get_args(annotation):
        yield from _models_in(argument, depth + 1)


def model_field_names(model: type[BaseModel], _seen=None) -> frozenset[str]:
    """Every field name and alias of a model and of the models nested in its fields."""
    seen = set() if _seen is None else _seen
    if model in seen:
        return frozenset()
    seen.add(model)
    names = set()
    for name, info in model.model_fields.items():
        names.add(name)
        names.update(alias for alias in (info.alias, info.validation_alias) if isinstance(alias, str))
        for nested in _models_in(info.annotation):
            names |= model_field_names(nested, seen)
    return frozenset(names)


class WorkflowIssue(StrictModel):
    code: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    path: str = Field(default="", max_length=512)             # RFC 6901 pointer into the request
    message: str = Field(min_length=1, max_length=2000)
    node_id: str | None = None
    row_number: int | None = Field(default=None, ge=1)        # 1-based source record (CSV header excluded)


class WorkflowResult(StrictModel):
    status: Literal["ok", "invalid", "conflict"]
    issues: tuple[WorkflowIssue, ...] = ()
    data: JsonValue = None


def _credential_name(key) -> str:
    """Compare names as ``RUNPOD_API_KEY``: case, surrounding whitespace and ``-`` versus ``_`` do not matter."""
    return str(key).strip().replace("-", "_").casefold()


def _pasted_key(value) -> bool:
    return isinstance(value, str) and any(marker in value for marker in KEY_MARKERS)


def _credential_entry(key, value) -> bool:
    """A credential name, or a ``*_saved`` flag that is not a boolean (it could carry the secret itself)."""
    name = _credential_name(key)
    return name in CREDENTIAL_NAMES or (name.endswith("_saved") and name.removesuffix("_saved") in CREDENTIAL_NAMES
                                        and type(value) is not bool)


def _refuse_credentials(values: Mapping) -> None:
    """Refuse credentials in a public settings or profile view. Pasted private keys are checked in the key-file
    fields only, as main.rs ``pasted_key`` does: a display name may say anything."""
    for key, value in values.items():
        if _credential_entry(key, value) or (_credential_name(key) in KEY_FILE_FIELDS and _pasted_key(value)):
            raise ValueError(CREDENTIALS_REFUSED)


class NativeProfile(StrictModel):
    """Public executor profile view sent by the native store: allowlisted fields only, never a secret value."""
    profile_id: Annotated[LocalId, Field(max_length=64)]
    fields: dict[str, str]
    RUNPOD_API_KEY_saved: bool = False

    @model_validator(mode="after")
    def allowlisted(self):
        allowed = PROFILE_FIELDS.get(self.fields.get("provider", ""))
        if allowed is None:
            raise ValueError("unsupported executor profile provider")
        if set(self.fields) - set(allowed):
            raise ValueError("executor profile field is not allowed for this provider")
        if any(len(value) > 2048 for value in self.fields.values()):
            raise ValueError("executor profile values are limited to 2048 characters")
        _refuse_credentials(self.fields)
        return self


class WorkflowEnvelope(StrictModel):
    channel: Literal["workflow"]
    root: str = Field(min_length=1, max_length=4096)          # <app_root>/state
    settings: dict[str, str | bool] = Field(default_factory=dict)
    profiles: tuple[NativeProfile, ...] = Field(default=(), max_length=32)
    secrets: dict[str, str] = Field(default_factory=dict)
    action: str = Field(max_length=64)
    data: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("root")
    @classmethod
    def absolute_root(cls, value):
        if "\x00" in value or not Path(value).is_absolute():
            raise ValueError("the state root must be an absolute path")
        return value

    @model_validator(mode="after")
    def unique_profiles(self):
        # Windows file names and Credential Manager entries ignore case and trailing dots.
        if len({profile.profile_id.casefold().rstrip(".") for profile in self.profiles}) != len(self.profiles):
            raise ValueError("duplicate executor profile ID")
        return self


@dataclass(frozen=True, repr=False)
class WorkflowContext:
    state_dir: Path
    settings: Mapping[str, str | bool] = field(default_factory=dict)
    profiles: Mapping[str, NativeProfile] = field(default_factory=dict)
    secrets: Mapping[str, str] = field(default_factory=dict)
    clock: Callable[[], float] = time.time

    def __repr__(self):
        # Never the settings or secret values; secret names and profile IDs only.
        return (f"WorkflowContext(state_dir={str(self.state_dir)!r}, profiles={sorted(self.profiles)!r}, "
                f"secrets={sorted(self.secrets)!r})")

    __str__ = __repr__

    @property
    def workflows_dir(self) -> Path:
        return Path(self.state_dir) / "workflows"

    @property
    def definitions_exist(self) -> bool:
        return (self.workflows_dir / _DEFINITIONS_FILE).is_file()

    def registry(self) -> Registry:
        return Registry(Path(self.state_dir) / "registry")

    def projects(self) -> ProjectStore:
        return ProjectStore(self.workflows_dir)

    def recipes(self) -> RecipeStore:
        return RecipeStore(self.workflows_dir)

    def imports(self) -> ImportService:
        return ImportService(Path(self.state_dir) / "registry")


class _Registered(NamedTuple):
    request: type[StrictModel]
    handler: Callable[[WorkflowContext, StrictModel], WorkflowResult]


_HANDLERS: dict[str, _Registered] = {}


def _check_request_model(request) -> None:
    if not (isinstance(request, type) and issubclass(request, StrictModel)):
        raise TypeError("workflow requests must be strict models")
    config = request.model_config
    if config.get("extra") != "forbid" or config.get("frozen") is not True:
        raise TypeError("workflow requests must be frozen and refuse extra fields")
    commands = sorted(name for name in model_field_names(request) if command_like_field(name))
    if commands:
        raise TypeError("workflow requests must not have command-like fields: " + ", ".join(commands))


def workflow_action(name: str, request: type[StrictModel]):
    """Register the handler of one closed-table action; only the module ACTION_MODULES names may do so."""
    if name not in ACTION_MODULES:
        raise ValueError(UNSUPPORTED_ACTION)
    _check_request_model(request)

    def register(handler):
        if getattr(handler, "__module__", None) != ACTION_MODULES[name]:
            raise ValueError(f"workflow action {name} belongs to {ACTION_MODULES[name]}")
        existing = _HANDLERS.get(name)
        if existing is not None and existing.handler.__qualname__ != handler.__qualname__:
            raise ValueError(f"workflow action {name} is already registered")
        _HANDLERS[name] = _Registered(request, handler)
        return handler
    return register


def registered_requests() -> Mapping[str, type[StrictModel]]:
    return MappingProxyType({name: registered.request for name, registered in _HANDLERS.items()})


def issue(code: str, message: str, path: str = "", *, node_id: str | None = None,
          row_number: int | None = None) -> WorkflowIssue:
    return WorkflowIssue(code=code, path=path[:512], message=(str(message) or code)[:2000], node_id=node_id,
                         row_number=row_number)


def ok(data=None, *, warnings: Iterable[WorkflowIssue] = ()) -> WorkflowResult:
    warnings = tuple(warnings)
    if warnings:
        data = {} if data is None else data
        if not isinstance(data, dict):
            raise TypeError("warnings need an object result")
        data = {**data, "warnings": [warning.model_dump(mode="json") for warning in warnings]}
    return WorkflowResult(status="ok", data=data)


def invalid(*issues: WorkflowIssue, data=None) -> WorkflowResult:
    if not issues:
        raise TypeError("an invalid result needs an issue")
    return WorkflowResult(status="invalid", issues=issues, data=data)


def conflict(*issues: WorkflowIssue) -> WorkflowResult:
    if not issues:
        raise TypeError("a conflict result needs an issue")
    return WorkflowResult(status="conflict", issues=issues)


def _pointer_part(part) -> str:
    return str(part).replace("~", "~0").replace("/", "~1")


def _pointer(location, data) -> str:
    """RFC 6901 pointer of a pydantic location, following the request data to drop union-member tags."""
    if data is None:
        return "".join("/" + _pointer_part(part) for part in location)
    parts, node = [], data
    for index, part in enumerate(location):
        if isinstance(node, Mapping) and isinstance(part, str) and part in node:
            parts.append(part)
            node = node[part]
        elif isinstance(node, list) and type(part) is int and 0 <= part < len(node):
            parts.append(part)
            node = node[part]
        elif isinstance(node, Mapping) and index == len(location) - 1:
            parts.append(part)          # a missing field
        # Otherwise a union member or validator tag such as "constrained-str": not part of the request.
    return "".join("/" + _pointer_part(part) for part in parts)


def issues_from_validation(exc: ValidationError, prefix: str = "", *, data=None) -> tuple[WorkflowIssue, ...]:
    """One issue per pydantic error; pydantic's ``msg`` never echoes the input value."""
    return tuple(issue("extra_field" if error["type"] == "extra_forbidden" else "invalid_value",
                       error["msg"], prefix + _pointer(error["loc"], data)) for error in exc.errors())


def _text(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        return "; ".join(dict.fromkeys(error["msg"] for error in exc.errors()))
    return str(exc)


def classify(text: str) -> tuple[str, str]:
    """``(status, code)`` of a domain refusal text: the 409 table, the invalid table, else ``refused``."""
    for prefix, code in CONFLICTS.items():
        if text.startswith(prefix):
            return "conflict", code
    for prefix, code in INVALID_CODES.items():
        if text.startswith(prefix):
            return "invalid", code
    return "invalid", "refused"


def refusal(exc: ValueError, *, path: str = "", node_id: str | None = None) -> WorkflowResult:
    """A domain ValueError as a result: CONFLICTS -> conflict, INVALID_CODES -> invalid, else invalid/refused."""
    text = _text(exc)
    status, code = classify(text)
    found = issue(code, text, path, node_id=node_id)
    return conflict(found) if status == "conflict" else invalid(found)


def _refused(exc: ValueError, paths: Mapping[str, str] | None = None, default: str = "") -> WorkflowResult:
    _, code = classify(_text(exc))
    return refusal(exc, path=(paths or {}).get(code, default))


def local_file(path: str) -> Path:
    """A local absolute file path. UNC and device paths (``\\\\server``, ``//server``, ``\\\\?\\``, ``\\\\.\\``),
    DOS device names (``C:\\data\\CON``, ``NUL.csv``, `` NUL``), any ``:`` after the drive (``nul:``, ``COM1:``,
    alternate data streams) and surrounding whitespace are refused because reading them is network or device
    access. String checks only."""
    if (not isinstance(path, str) or not path or path != path.strip() or "\x00" in path
            or re.match(r"[\\/]{2}", path)):
        raise ValueError(LOCAL_FILE_ONLY)
    if ":" in re.sub(r"^[A-Za-z]:", "", path):   # device suffixes (nul:, COM1:) and alternate data streams
        raise ValueError(LOCAL_FILE_ONLY)
    if any(part.split(".")[0].strip().casefold() in _DEVICE_NAMES for part in re.split(r"[\\/]", path)):
        raise ValueError(LOCAL_FILE_ONLY)
    value = Path(path)
    if not value.is_absolute():
        raise ValueError(LOCAL_FILE_ONLY)
    return value


def _credential_keys(value, path="") -> list[str]:
    """Pointers of credentials in request data that passed model validation (it is persisted): credential-named
    keys and non-boolean ``*_saved`` flags (free-form JSON such as the graph draft), and pasted private keys in any
    string value."""
    found = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            pointer = path + "/" + _pointer_part(key)
            if _credential_entry(key, item):
                found.append(pointer)
            else:
                found.extend(_credential_keys(item, pointer))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(_credential_keys(item, f"{path}/{index}"))
    elif _pasted_key(value):
        found.append(path)
    return found


def _scrub(value, secrets: tuple[str, ...]):
    if isinstance(value, str):
        for secret in secrets:
            for form in (secret, repr(secret)[1:-1]):
                value = value.replace(form, "[redacted]")
        return value
    if isinstance(value, dict):
        return {_scrub(key, secrets): _scrub(item, secrets) for key, item in value.items()}
    if isinstance(value, list):
        return [_scrub(item, secrets) for item in value]
    return value


def _resolve(action: str) -> _Registered | None:
    registered = _HANDLERS.get(action)
    if registered is None:
        module = ACTION_MODULES[action]
        try:
            importlib.import_module(module)
        except ModuleNotFoundError as exc:
            if exc.name != module:
                raise          # the module exists but one of its dependencies is missing: a real defect
            return None
        registered = _HANDLERS.get(action)
    return registered


def dispatch_action(context: WorkflowContext, action: str, data: Mapping) -> WorkflowResult:
    """Run one workflow action (the desktop channel and the CLI). Envelope refusals raise ValueError."""
    _refuse_credentials(context.settings)
    if action not in ACTION_MODULES:
        raise ValueError(UNSUPPORTED_ACTION)
    scope = SECRET_SCOPES.get(action, frozenset())
    if any(name not in scope for name in context.secrets):
        raise ValueError(SCOPE_REFUSED)
    registered = _resolve(action)
    if registered is None:
        return invalid(issue("not_available", NOT_AVAILABLE))
    data = dict(data)
    try:
        request = registered.request.model_validate(data)
    except ValidationError as exc:
        return invalid(*issues_from_validation(exc, data=data))
    credentials = _credential_keys(data)
    if credentials:
        return invalid(*(issue("credential_field", CREDENTIALS_REFUSED, pointer) for pointer in credentials))
    try:
        result = registered.handler(context, request)
    except ValueError as exc:
        result = refusal(exc)
    if not isinstance(result, WorkflowResult):
        raise TypeError("workflow handlers must return a WorkflowResult")
    secrets = tuple(value for value in context.secrets.values() if len(value) >= 8)
    if secrets:
        # Defence in depth for the scoped actions (SECRET_SCOPES): no secret value leaves in a result.
        result = WorkflowResult.model_validate(_scrub(result.model_dump(mode="json"), secrets))
    return result


def _envelope_error(exc: ValidationError) -> str:
    fields = sorted({str(error["loc"][0]) for error in exc.errors()
                     if error["loc"] and error["loc"][0] in WorkflowEnvelope.model_fields})
    return INVALID_ENVELOPE + (" (" + ", ".join(fields) + ")" if fields else "")


def handle(request: Mapping) -> dict:
    """The workflow channel: one envelope in, ``WorkflowResult.model_dump(mode="json")`` out."""
    if not isinstance(request, Mapping):
        raise ValueError(INVALID_ENVELOPE)
    if isinstance(request.get("settings"), Mapping):
        _refuse_credentials(request["settings"])
    if isinstance(request.get("profiles"), (list, tuple)):
        for profile in request["profiles"]:
            if isinstance(profile, Mapping) and isinstance(profile.get("fields"), Mapping):
                _refuse_credentials(profile["fields"])
    try:
        envelope = WorkflowEnvelope.model_validate(dict(request))
    except ValidationError as exc:
        # Envelope errors are native bugs; the text names envelope fields only, never an input value.
        raise ValueError(_envelope_error(exc)) from None
    context = WorkflowContext(
        state_dir=Path(envelope.root), settings=MappingProxyType(dict(envelope.settings)),
        profiles=MappingProxyType({profile.profile_id: profile for profile in envelope.profiles}),
        secrets=MappingProxyType(dict(envelope.secrets)))
    return dispatch_action(context, envelope.action, envelope.data).model_dump(mode="json")


# ---- request models of 9.1 ----

RequestId = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")]


class Empty(StrictModel):
    pass


class ProjectCreate(StrictModel):
    request_id: RequestId
    name: str = Field(min_length=1, max_length=200)
    purpose: str = Field(default="", max_length=4000)
    task_type: str = Field(default="", max_length=200)


class ProjectRef(StrictModel):
    project_id: LocalId
    revision: Sha256 | None = None


class ProjectSave(StrictModel):
    spec: ProjectSpec
    expected_revision: Sha256          # creation only via project_create


class LegacyImport(StrictModel):
    request_id: RequestId
    ai_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")


class RecipeRef(StrictModel):
    recipe_id: LocalId
    revision: Sha256 | None = None


class RecipeSave(StrictModel):
    spec: RecipeSpec
    expected_revision: Sha256 | None   # required key; null creates


class RecipeClone(StrictModel):
    recipe_id: LocalId
    revision: Sha256 | None = None
    name: str | None = Field(None, min_length=1, max_length=200)
    new_recipe_id: LocalId | None = None
    version: LocalId = "1"


class ImportSource(StrictModel):
    path: str = Field(min_length=1, max_length=1024)


class ImportPreview(StrictModel):
    source_id: Sha256
    mapping: ImportMapping


class ImportRegister(StrictModel):
    source_id: Sha256
    mapping: ImportMapping
    preview_sha256: Sha256
    dataset_id: LocalId
    version: LocalId
    synthetic: bool = Field(default=False, strict=True)
    export_allowed: bool = Field(default=False, strict=True)


class SplitPreview(StrictModel):
    plan: SplitPlan


class SplitCommit(StrictModel):
    plan_sha256: Sha256


# ---- handlers of 9.1 ----

def _reserved(dataset_id: str) -> bool:
    from .desktop import reserved_tutorial_id
    return reserved_tutorial_id({"dataset_id": dataset_id})


def _practice_reserved(path: str) -> WorkflowIssue:
    from .desktop import TUTORIAL_REFUSAL
    return issue("practice_reserved", TUTORIAL_REFUSAL, path)


def _unknown_path(exc: ValueError, id_path: str, revision_path: str) -> str:
    return revision_path if str(exc).startswith("unknown definition revision") else id_path


def _project(revision: Revision) -> dict:
    return {"project": revision.spec.model_dump(mode="json"), "revision_sha256": revision.revision_sha256}


def _recipe(revision: Revision) -> dict:
    return {"recipe": revision.spec.model_dump(mode="json"), "revision_sha256": revision.revision_sha256}


@workflow_action("projects_list", Empty)
def projects_list(context: WorkflowContext, request: Empty) -> WorkflowResult:
    if not context.definitions_exist:   # a read never creates the definitions database
        return ok({"projects": []})
    projects = []
    for revision in context.projects().list():
        spec = revision.spec
        nodes = spec.graph.get("nodes")
        projects.append({"project_id": spec.project_id, "name": spec.name, "purpose": spec.purpose,
                         "task_type": spec.task_type, "revision_sha256": revision.revision_sha256,
                         "legacy_origin": spec.legacy_origin,
                         "datasets": [binding.model_dump(mode="json") for binding in spec.datasets],
                         "graph_nodes": len(nodes) if isinstance(nodes, list) else 0})
    return ok({"projects": projects})


@workflow_action("project_create", ProjectCreate)
def project_create(context: WorkflowContext, request: ProjectCreate) -> WorkflowResult:
    try:
        created = context.projects().create(request.request_id, request.name, request.purpose, request.task_type)
    except ValueError as exc:
        return _refused(exc, {"request_reused": "/request_id"})
    return ok(_project(created))


@workflow_action("project_get", ProjectRef)
def project_get(context: WorkflowContext, request: ProjectRef) -> WorkflowResult:
    if not context.definitions_exist:
        return invalid(issue("unknown_definition", "unknown definition", "/project_id"))
    try:
        revision = context.projects().get(request.project_id, request.revision)
    except ValueError as exc:
        return _refused(exc, {"unknown_definition": _unknown_path(exc, "/project_id", "/revision")})
    return ok(_project(revision))


@workflow_action("project_save", ProjectSave)
def project_save(context: WorkflowContext, request: ProjectSave) -> WorkflowResult:
    if not context.definitions_exist:
        return invalid(issue("unknown_definition", "unknown definition", "/spec/project_id"))
    store = context.projects()
    try:
        store.get(request.spec.project_id)   # projects are created only by project_create
        saved = store.save(request.spec, request.expected_revision)
    except ValueError as exc:
        return _refused(exc, {"unknown_definition": "/spec/project_id", "revision_conflict": "/expected_revision"})
    return ok(_project(saved))


@workflow_action("project_import_legacy", LegacyImport)
def project_import_legacy(context: WorkflowContext, request: LegacyImport) -> WorkflowResult:
    """A draft project and recipe from a legacy ``save_profile`` profile; legacy files are only read."""
    path = Path(context.state_dir) / "profiles" / f"{request.ai_id}.json"
    try:
        raw = path.read_bytes()
    except OSError:
        return invalid(issue("legacy_profile_missing", LEGACY_PROFILE_MISSING, "/ai_id"))
    try:
        config = AIConfig.model_validate_json(raw)
    except ValueError:
        return invalid(issue("legacy_profile_invalid", LEGACY_PROFILE_INVALID, "/ai_id"))
    if config.ai_id != request.ai_id:   # e.g. another spelling on a case-insensitive file system
        return invalid(issue("legacy_profile_missing", LEGACY_PROFILE_MISSING, "/ai_id"))
    try:
        template = recipe_from_config(config)
        recipe = RecipeSpec.model_validate(
            {**template.model_dump(), "recipe_id": "legacy-" + digest(request.request_id.encode())[:24]})
    except ValueError as exc:
        return _refused(exc, default="/ai_id")
    registry, bindings, expected = context.registry(), [], []
    profile = config.evaluation_profile
    for binding_id, dataset_id, version in (("train", config.dataset_id, config.dataset_version),
                                            ("validation", profile.dataset_id, profile.dataset_version)):
        if not dataset_id:
            continue
        expected.append(binding_id)
        try:
            record = registry.get_dataset(dataset_id, version)
        except FileNotFoundError:
            continue   # reported below from the stored project's bindings
        except ValueError as exc:
            return _refused(exc, default="/ai_id")
        bindings.append(DatasetBinding(binding_id=binding_id, role=binding_id, dataset=DatasetRef(
            dataset_id=dataset_id, version=version, sha256=record["sha256"])))
    projects, recipes = context.projects(), context.recipes()
    try:
        created = projects.create(request.request_id, config.ai_id, "", config.workflow_id)
        try:
            stored_recipe = recipes.get(recipe.recipe_id)
        except ValueError as exc:
            if not str(exc).startswith("unknown definition"):
                raise
            stored_recipe = recipes.save(recipe, expected_revision=None)
        if stored_recipe.spec != recipe:
            raise ValueError("request ID reused with different project details")
        current = projects.get(created.spec.project_id)
        if current.revision_sha256 == created.revision_sha256:
            desired = ProjectSpec.model_validate({**created.spec.model_dump(), "legacy_origin": config.ai_id,
                                                  "datasets": [binding.model_dump() for binding in bindings]})
            try:
                current = projects.save(desired, created.revision_sha256)
            except ValueError as exc:
                if not str(exc).startswith("definition revision conflict"):
                    raise
                current = projects.get(created.spec.project_id)   # another writer saved first: return its revision
        # Otherwise this import already completed (or the project was edited since): return it as it is.
    except ValueError as exc:
        return _refused(exc, {"request_reused": "/request_id", "revision_conflict": "/request_id"},
                        default="/ai_id")
    bound = {binding.binding_id for binding in current.spec.datasets}
    # From the returned project, so a retry reports the same gap as the first import.
    warnings = [issue("legacy_dataset_missing", LEGACY_DATASET_MISSING, "/ai_id")
                for binding_id in expected if binding_id not in bound]
    return ok({**_project(current), "recipe": stored_recipe.spec.model_dump(mode="json"),
               "recipe_revision_sha256": stored_recipe.revision_sha256,
               "datasets": [binding.model_dump(mode="json") for binding in current.spec.datasets]},
              warnings=warnings)


@workflow_action("recipes_list", Empty)
def recipes_list(context: WorkflowContext, request: Empty) -> WorkflowResult:
    if not context.definitions_exist:
        return ok({"recipes": []})
    return ok({"recipes": [{"recipe_id": revision.spec.recipe_id, "version": revision.spec.version,
                            "name": revision.spec.name, "description": revision.spec.description,
                            "training_stage": revision.spec.training_stage,
                            "training_method": revision.spec.training_method,
                            "input_kind": revision.spec.input_kind, "revision_sha256": revision.revision_sha256}
                           for revision in context.recipes().list()]})


@workflow_action("recipe_get", RecipeRef)
def recipe_get(context: WorkflowContext, request: RecipeRef) -> WorkflowResult:
    if not context.definitions_exist:
        return invalid(issue("unknown_definition", "unknown definition", "/recipe_id"))
    try:
        revision = context.recipes().get(request.recipe_id, request.revision)
    except ValueError as exc:
        return _refused(exc, {"unknown_definition": _unknown_path(exc, "/recipe_id", "/revision")})
    return ok(_recipe(revision))


@workflow_action("recipe_save", RecipeSave)
def recipe_save(context: WorkflowContext, request: RecipeSave) -> WorkflowResult:
    try:
        saved = context.recipes().save(request.spec, request.expected_revision)
    except ValueError as exc:
        return _refused(exc, {"revision_conflict": "/expected_revision"})
    return ok(_recipe(saved))


@workflow_action("recipe_clone", RecipeClone)
def recipe_clone(context: WorkflowContext, request: RecipeClone) -> WorkflowResult:
    if not context.definitions_exist:
        return invalid(issue("unknown_definition", "unknown definition", "/recipe_id"))
    try:
        cloned = context.recipes().clone(request.recipe_id, name=request.name, new_recipe_id=request.new_recipe_id,
                                         version=request.version, revision=request.revision)
    except ValueError as exc:
        return _refused(exc, {"unknown_definition": _unknown_path(exc, "/recipe_id", "/revision"),
                              "revision_conflict": "/new_recipe_id"})
    return ok(_recipe(cloned))


@workflow_action("dataset_import_source", ImportSource)
def dataset_import_source(context: WorkflowContext, request: ImportSource) -> WorkflowResult:
    try:
        path = local_file(request.path)
    except ValueError as exc:
        return refusal(exc, path="/path")
    try:
        source = context.imports().snapshot_local(path)
    except OSError:
        return invalid(issue("file_unreadable", FILE_UNREADABLE, "/path"))
    except ValueError as exc:
        return refusal(exc, path="/path")
    return ok(source)


@workflow_action("dataset_import_preview", ImportPreview)
def dataset_import_preview(context: WorkflowContext, request: ImportPreview) -> WorkflowResult:
    try:
        result = context.imports().inspect(request.source_id, request.mapping)
    except OSError:
        return invalid(issue("source_unknown", SOURCE_UNKNOWN, "/source_id"))
    except ValueError as exc:
        return refusal(exc, path="/source_id")
    rows = tuple(issue(found["code"], found["message"], "/mapping",
                       row_number=found["ordinal"] + 1 if found["ordinal"] is not None else None)
                 for found in result["issues"])
    # The preview data is returned even with issues, so the mapping screen can show samples beside them.
    return invalid(*rows, data=result) if rows else ok(result)


@workflow_action("dataset_import_register", ImportRegister)
def dataset_import_register(context: WorkflowContext, request: ImportRegister) -> WorkflowResult:
    if _reserved(request.dataset_id):   # the practice ID is legacy-only (tutorial contract)
        return invalid(_practice_reserved("/dataset_id"))
    try:
        result = context.imports().register(request.source_id, request.mapping, request.preview_sha256,
                                            request.dataset_id, request.version, synthetic=request.synthetic,
                                            export_allowed=request.export_allowed)
    except OSError:
        return invalid(issue("source_unknown", SOURCE_UNKNOWN, "/source_id"))
    except ValueError as exc:
        if str(exc).startswith(IMMUTABLE_EXISTS):   # the provenance pointer of this ID and version differs
            return conflict(issue("version_exists", str(exc), "/version"))
        return _refused(exc, {"preview_changed": "/preview_sha256", "import_issues": "/preview_sha256",
                              "version_exists": "/version", "size_limit": "/source_id"})
    return ok({"dataset": result["dataset"], "preview_sha256": result["preview_sha256"]})


@workflow_action("dataset_split_preview", SplitPreview)
def dataset_split_preview(context: WorkflowContext, request: SplitPreview) -> WorkflowResult:
    reserved = tuple(_practice_reserved(f"/plan/outputs/{_pointer_part(role)}/dataset_id")
                     for role, output in sorted(request.plan.outputs.items()) if _reserved(output.dataset_id))
    if reserved:
        return invalid(*reserved)
    try:
        manifest = preview_split(context.registry(), request.plan)
    except OSError:
        return invalid(issue("dataset_unknown", DATASET_UNKNOWN, "/plan/dataset"))
    except ValueError as exc:
        return refusal(exc, path="/plan")
    return ok({"plan_sha256": manifest["plan_sha256"], "counts": manifest["counts"],
               "outputs": manifest["outputs"]})


@workflow_action("dataset_split_commit", SplitCommit)
def dataset_split_commit(context: WorkflowContext, request: SplitCommit) -> WorkflowResult:
    registry = context.registry()
    try:
        saved = json.loads((registry.root / "split_previews" / f"{request.plan_sha256}.json").read_bytes())
        outputs = [output["dataset_id"] for output in saved["plan"]["outputs"].values()]
        if not all(isinstance(output, str) for output in outputs):
            raise TypeError("split output IDs must be strings")
    except OSError:
        return invalid(issue("preview_unknown", PREVIEW_UNKNOWN, "/plan_sha256"))
    except (ValueError, KeyError, TypeError, AttributeError):
        return conflict(issue("preview_changed", SPLIT_PREVIEW_CHANGED, "/plan_sha256"))
    if any(_reserved(output) for output in outputs):
        return invalid(_practice_reserved("/plan_sha256"))   # defence in depth for previews written elsewhere
    try:
        manifest = commit_split(registry, request.plan_sha256)
    except OSError:
        return invalid(issue("dataset_unknown", DATASET_UNKNOWN, "/plan_sha256"))
    except ValueError as exc:
        return refusal(exc, path="/plan_sha256")
    return ok({"plan_sha256": manifest["plan_sha256"], "outputs": manifest["outputs"]})
