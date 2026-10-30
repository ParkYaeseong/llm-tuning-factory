"""Graph planning on the desktop workflow channel (Task 9.2).

Executor profiles get content-addressed revisions of their public native fields (``profile_revision``), and one
function turns those fields into the pinned public profile with Task 8's D11 transport hash (``public_profile``).
Only three actions may touch the network, and only in production (tests use fakes):

- ``executor_profile_inspect`` (secret scope ``RUNPOD_API_KEY``): one read-only RunPod ``status`` GET, or the fixed
  read-only SSH probe. The public observation is stored in ``state/workflows/observations/``.
- ``executor_profile_inventory`` (Task 9.8, no secret: SSH reads a local key file path): the 6A inventory probe of
  the legacy ``server_inspect`` for an SSH profile that may have no GPU selection yet. It stores nothing.
- ``model_root_snapshot`` (secret scope ``HF_TOKEN``, explicit ``download_approved``): ``config.json`` and the
  tokenizer files of one pinned revision, never weights, published into the ``DirectoryRootSnapshots`` layout
  ``state/workflows/roots/<owner>/<name>/<revision>/`` and indexed in ``state/workflows/root-index/``.

Task 9.8 also lists the stored observations (``observations_list``) and keeps each project's planning inputs (node
time limits, estimates and the simulation choice, never a secret) in ``state/workflows/planning-inputs/``, with a
compare-and-swap on the project revision (``planning_inputs_get``/``planning_inputs_save``).

``graph_validate`` and ``graph_plan`` make no network call. They read the definitions database, the registry, the
benchmark store, the root index, HF-root target evidence (``state/workflows/root-targets/``, which Task 9.7 fills)
and the stored observations; ``graph_plan`` writes only its immutable plan under ``state/workflows/plans/`` (and,
for a real plan, the public tracking settings under their hash). Planning defaults to simulation. A real plan needs
observations of the current profile revisions at most an hour old, every transport hash and a tracking destination
hashed exactly as the controller's tracker reports it (D11, D13).

Domain texts of Tasks 1-8 pass through unchanged (English); this module's own texts are Korean (F25e). Errors never
echo a request value: pydantic failures contribute only their ``msg``.
"""

import json
import os
import re
import shutil
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import quote, urlsplit

import httpx
from pydantic import Field, TypeAdapter, ValidationError

from .benchmarks import BenchmarkSpec
from .config import StrictModel
from .desktop_workflows import (
    _DEVICE_NAMES,
    KEY_FILE_FIELDS,
    Empty,
    NativeProfile,
    WorkflowContext,
    WorkflowResult,
    conflict,
    invalid,
    issue,
    issues_from_validation,
    local_file,
    ok,
    refusal,
    workflow_action,
)
from .executor_profiles import (
    ExecutorProfile,
    PublicExecutorProfile,
    ResourceFacts,
    ResourceObservation,
    observation_key,
    runpod_facts,
    runpod_transport_sha256,
    validate_resource_binding,
)
from .gpu_servers import SSHServerFacts, server_settings
from .graph_planner import (
    BenchmarkRevision,
    ExecutionChoices,
    GraphPlan,
    ModelContext,
    ModelTargetEvidence,
    NodeRefusal,
    NodeResources,
    TrackingDestination,
    _check_plan,
    plan_graph,
    snapshot_dataset,
    validate_sample_ids,
)
from .graph_schema import (
    EvaluateNode,
    GraphSpec,
    MaterializeNode,
    MergeNode,
    NodeOutputRef,
    SelectNode,
    TrainNode,
    validate_graph,
)
from .model_artifacts import (
    HFRootRef,
    ModelArtifactRef,
    ModelInput,
    ModelLoadRef,
    _relative,
    is_processor_file,
    is_tokenizer_file,
    tokenizer_fingerprint,
)
from .model_hub import ModelHub, ModelProfile
from .projects import LocalId, ProjectSpec, Revision, Sha256
from .recipes import RecipeSpec
from .registry import canonical, digest, immutable_write
from .tracking import DEFAULT_EXPERIMENT, public_tracking_settings, tracking_settings_sha256
from .training import check_generation_benchmark

OBSERVATION_MAX_AGE_SECONDS = 3600
TRACKING_PROFILE_ID = "mlflow"
ESTIMATE_PREFIX = "estimate-"
ESTIMATE_SCOPE = "estimate"
MAX_ROOT_FILE_BYTES = 64 * 1024 * 1024
MAX_ROOT_TOTAL_BYTES = 128 * 1024 * 1024
MAX_ROOT_FILES = 256
PROFILE_SCHEMA = "executor-profile/1"
OBSERVATION_SCHEMA = "executor-observation/1"
# Never requested, even when a repository lists such a file under chat_templates/ (the snapshot is then refused).
WEIGHT_SUFFIXES = (".safetensors", ".bin", ".pt", ".pth", ".gguf", ".ckpt", ".h5", ".msgpack", ".onnx", ".npz",
                   ".pkl")
CORE_TOKENIZER_FILES = frozenset({"tokenizer.json", "tokenizer.model", "spiece.model", "vocab.json", "vocab.txt"})
HF_ORIGIN = "https://huggingface.co"
HF_CONNECTION = "Hugging Face metadata connection failed; check connectivity"   # ModelHub.get's text
PLAN_MISMATCH = "plan hash mismatch"                                               # graph_planner._check_plan's

OBSERVATION_STALE = "실제 실행 계획에는 1시간 이내에 확인한 자원 정보가 필요합니다. 실행 프로필에서 자원을 다시 확인하세요."
TRACKING_REQUIRED = "실제 실행 계획에는 MLflow 서버 주소가 필요합니다."
BASE_TARGETS_MISSING = ("이 단계는 기반 모델의 모듈 구조 정보가 필요하지만 아직 지원하지 않습니다(이어 학습·병합·전체 모델 확정). "
                        "기반 모델을 바꾸거나 이 단계를 빼고 다시 계획하세요.")
SIMULATED_NEEDS_TARGETS = ("모의 실행에는 기반 모델의 모듈 구조 정보가 필요합니다(lora_target: all 또는 전체·freeze 학습). "
                           "계획은 만들 수 있지만 모의 실행은 이 단계에서 멈춥니다. 대상 모듈을 직접 고르면 모의 실행할 수 있습니다.")
PROFILE_UNKNOWN = "실행 프로필을 찾을 수 없습니다. 실행 프로필을 먼저 저장하세요."
PROFILE_FIELDS_INVALID = "실행 프로필 값이 올바르지 않습니다."
PROFILE_ID_RESERVED = "이 실행 프로필 ID는 Windows 장치 이름이라 쓸 수 없습니다."
SSH_FACTS_REQUIRED = "SSH 서버 프로필은 확인한 서버 정보로만 만들 수 있습니다."
CREDENTIAL_MISSING = "이 실행 프로필에 저장된 RunPod API 키가 없습니다. 실행 프로필에서 키를 저장하세요."
OBSERVATION_MISSING = "이 실행 프로필의 자원 정보를 아직 확인하지 않았습니다. 실행 프로필에서 자원을 확인하세요."
OBSERVATION_INVALID = "저장된 자원 확인 기록을 읽을 수 없습니다. 실행 프로필에서 자원을 다시 확인하세요."
OBSERVATION_CONFLICT = ("대소문자나 끝의 점만 다른 실행 프로필의 자원 확인 기록이 이미 있습니다. "
                        "프로필 ID를 구분되게 바꾸세요.")
OBSERVATION_REVISION_DIFFERS = "자원을 확인한 뒤 실행 프로필이 바뀌었습니다. 실행 프로필에서 자원을 다시 확인하세요."
ESTIMATE_REAL = "계획용 추정 자원은 모의 실행 계획에만 쓸 수 있습니다."
ESTIMATE_COLLISION = "추정 자원 이름이 저장된 실행 프로필 ID와 같습니다. 다른 이름을 쓰세요."
ESTIMATE_DUPLICATE = "같은 이름의 추정 자원이 두 번 있습니다."
NODE_RESOURCE_MISSING = "이 단계의 최대 실행 시간을 정하세요."
NODE_RESOURCE_UNKNOWN = "그래프에 없는 단계나 선택 단계의 실행 시간입니다."
NODE_RESOURCE_DUPLICATE = "같은 단계의 실행 시간이 두 번 있습니다."
RECIPE_MISSING = "이 단계의 레시피 버전을 찾을 수 없습니다."
BENCHMARK_MISSING = "이 평가 단계의 벤치마크를 찾을 수 없습니다. 벤치마크를 먼저 저장하세요."
BENCHMARK_UNKNOWN = "저장된 벤치마크를 찾을 수 없습니다."
BENCHMARK_STORED_DIFFERS = "같은 지문으로 저장된 벤치마크 파일이 손상되었거나 바뀌었습니다."
DATASET_UNKNOWN = "등록된 데이터 버전을 찾을 수 없습니다."
EXPORT_NOT_ALLOWED = "이 PC 밖으로 보낼 수 있다고 표시하지 않은 데이터는 실제 실행에 쓸 수 없습니다."
MODEL_ROOT_MISSING = "기반 모델 {root}의 설정·토크나이저 스냅샷이 없습니다. 모델 스냅샷을 먼저 받으세요."
MODEL_ROOT_INVALID = "저장된 기반 모델 색인을 읽을 수 없습니다. 모델 스냅샷을 다시 받으세요."
ROOT_TARGETS_INVALID = "저장된 기반 모델의 모듈 구조 정보를 읽을 수 없습니다."
EXTERNAL_ARTIFACT = "이전 실행의 모델 산출물을 새 그래프의 입력으로 쓰는 기능은 아직 지원하지 않습니다."
DOWNLOAD_APPROVAL_REQUIRED = "모델 설정·토크나이저 파일을 받으려면 다운로드 승인이 필요합니다. 가중치는 받지 않습니다."
REPOSITORY_UNSUPPORTED = "이 PC의 폴더 이름으로 쓸 수 없는 모델 저장소 이름입니다."
MODEL_UNSUPPORTED = "이 모델은 학습 기반 모델로 지원하지 않습니다."
SNAPSHOT_REFUSED = "모델 저장소의 설정·토크나이저 파일 목록이 안전하지 않아 받지 않았습니다."
ROOT_FILE_TOO_LARGE = "모델 설정·토크나이저 파일이 크기 제한(파일당 64 MiB, 합계 128 MiB)을 넘습니다."
CONFIG_CHANGED = "받은 config.json이 확인한 모델 설정과 다릅니다."
TOKENIZER_INCOMPLETE = "모델 저장소에 완전한 토크나이저 파일이 없습니다."
ROOT_SNAPSHOT_CONFLICT = "이 모델 버전의 스냅샷 폴더가 이미 있지만 내용이 다릅니다. 폴더를 지우고 다시 받으세요."
PLAN_UNKNOWN = "저장된 실행 계획을 찾을 수 없습니다. 다시 계획하세요."
PLAN_STORED_DIFFERS = "같은 해시로 저장된 실행 계획 파일이 손상되었거나 바뀌었습니다."
TRACKING_STORED_DIFFERS = "저장된 MLflow 공개 설정 파일이 손상되었거나 바뀌었습니다."
KEY_PATH_RELATIVE = ("SSH 키 파일은 이 PC의 로컬 절대 경로로 지정하세요. 상대 경로는 실행 위치에 따라 다른 파일을 가리키고, "
                     "네트워크(UNC)·장치 경로는 쓸 수 없습니다.")
REMOTE_COMMAND_FAILED = "서버 확인 명령이 실패했습니다. 서버에 직접 접속해 GPU와 python3 상태를 확인하세요."
INSECURE_REDIRECT = "모델 파일 서버가 HTTPS가 아닌 주소로 연결을 넘겨 받지 않았습니다."
INVENTORY_SSH_ONLY = "GPU 목록 확인은 SSH 서버 프로필에서만 할 수 있습니다. RunPod 프로필은 자원 확인을 쓰세요."
PLANNING_INPUTS_SCHEMA = "planning-inputs/1"
PLANNING_INPUTS_INVALID = "저장된 계획 입력을 읽을 수 없습니다. 다시 입력해 저장하세요."
PLANNING_INPUTS_STALE = "계획 입력을 저장하기 전에 프로젝트가 바뀌었습니다. 최신 프로젝트를 불러온 뒤 다시 저장하세요."

_HEX64 = re.compile(r"[0-9a-f]{64}")
_PROFILE_ID = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9_.-]{0,63}")   # NativeProfile.profile_id
_PROFILE = TypeAdapter(PublicExecutorProfile)
_FACTS = TypeAdapter(ResourceObservation)
_DATASET_CODES = frozenset({"dataset", "dataset_role", "input_overlap"})
_ARTIFACT = "artifact"     # an effective base that is a model artifact: its evidence arrives at run time


def _reason(exc: Exception) -> str:
    """A refusal text that never echoes input: pydantic contributes only its ``msg`` strings."""
    if isinstance(exc, ValidationError):
        return "; ".join(dict.fromkeys(error["msg"] for error in exc.errors())) or type(exc).__name__
    return str(exc) or type(exc).__name__


def _now(context: WorkflowContext) -> datetime:
    return datetime.fromtimestamp(context.clock(), tz=timezone.utc)


def _text_setting(settings: Mapping, name: str) -> str:
    value = settings.get(name)
    return value if isinstance(value, str) else ""


def _replace(path: Path, data: bytes) -> None:
    """Write ``path`` atomically (a complete old or new file is visible, never a partial one)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


# -- profiles, revisions and destination hashes --------------------------------------------------

def profile_key(profile_id: str) -> str:
    """How profile IDs compare (9.1 M9): casefold plus ``rstrip(".")``, as Windows file names do. Files keyed by a
    profile ID use this key, so two IDs that differ only that way share a file and are refused there."""
    if not isinstance(profile_id, str) or not _PROFILE_ID.fullmatch(profile_id):
        raise ValueError(PROFILE_FIELDS_INVALID)
    key = profile_id.casefold().rstrip(".")
    if key.split(".")[0] in _DEVICE_NAMES:
        raise ValueError(PROFILE_ID_RESERVED)
    return key


def profile_revision(profile_id: str, fields: Mapping[str, str]) -> str:
    """The content revision of a native profile: its ID and every public field, name included (D6).

    The per-provider field allowlist of ``NativeProfile`` applies, so a credential is never hashed: it is refused.
    """
    try:
        native = NativeProfile.model_validate({"profile_id": profile_id, "fields": dict(fields)})
    except (ValidationError, TypeError, ValueError):
        raise ValueError(PROFILE_FIELDS_INVALID) from None
    return digest(canonical({"schema": PROFILE_SCHEMA, "profile_id": native.profile_id, "fields": dict(native.fields)}))


def check_key_paths(fields: Mapping[str, str]) -> None:
    """Key paths must be local absolute paths (after ``~``): the D11 transport hashes hash the resolved path, so a
    relative one would resolve against the working directory and the controller would refuse the pinned transport
    for good. UNC and device paths are refused as ``desktop_workflows.local_file`` refuses them (D12), and a ``~``
    without a resolvable home is refused too, rather than raising RuntimeError."""
    for name in KEY_FILE_FIELDS:
        value = fields.get(name)
        if not isinstance(value, str) or not value.strip():
            continue
        try:
            local_file(str(Path(value).expanduser()) if value.startswith("~") else value)
        except (ValueError, RuntimeError):
            raise ValueError(KEY_PATH_RELATIVE) from None


def public_profile(profile_id: str, fields: Mapping[str, str],
                   facts: ResourceObservation | None = None) -> PublicExecutorProfile:
    """The pinned public profile of native fields, with the D11 transport hash; never a key path or a credential.

    RunPod profiles are built offline (``facts``, when given, must be the same Pod). SSH profiles are built from
    observed server facts (``SSHServerSettings.profile``); without facts this raises.
    """
    revision = profile_revision(profile_id, fields)
    check_key_paths(fields)
    fields = dict(fields)
    try:
        if fields.get("provider") == "existing_runpod":
            profile = ExecutorProfile(profile_id=profile_id, revision=revision, name=fields.get("name", ""),
                                      pod_id=fields.get("pod_id", ""), credential_scope="local",
                                      transport_settings_sha256=runpod_transport_sha256(fields))
            if facts is not None:
                validate_resource_binding(profile, _FACTS.validate_python(facts.model_dump()))
            return profile
        if facts is None:
            raise ValueError(SSH_FACTS_REQUIRED)
        observed = _FACTS.validate_python(facts.model_dump())
        if not isinstance(observed, SSHServerFacts):
            raise ValueError("executor/resource destination mismatch")
        return server_settings({"provider": "ssh_server", **fields}).profile(
            observed, profile_id=profile_id, revision=revision, name=fields.get("name", ""), credential_scope="local")
    except ValidationError as exc:
        raise ValueError(_reason(exc)) from None


def current_profile_revisions(context: WorkflowContext) -> dict[str, str]:
    """``{profile_id: revision}`` of the native profiles this request carries (used by 9.3 and 9.4)."""
    return {profile_id: profile_revision(profile_id, profile.fields)
            for profile_id, profile in sorted(context.profiles.items())}


def public_auth_mode(settings: Mapping[str, object]) -> Literal["oidc", "token", "none"]:
    """The auth mode the controller's ``current_auth_mode`` will see, from the public view's ``*_saved`` flags.

    main.rs ``filter_mlflow_auth`` withholds per ``auth_mode``: ``token`` keeps only the token, ``none`` keeps
    nothing, anything else keeps the OIDC values and withholds the token.
    """
    mode = _text_setting(settings, "auth_mode")
    if mode == "none":
        return "none"
    if mode == "token":
        return "token" if settings.get("MLFLOW_TRACKING_TOKEN_saved") is True else "none"
    oidc = _text_setting(settings, "MLFLOW_OIDC_TOKEN_URL") and settings.get("MLFLOW_OIDC_CLIENT_SECRET_saved") is True
    return "oidc" if oidc else "none"


def _oidc_incomplete(settings: Mapping[str, object]) -> bool:
    """``auth.require_complete_oidc`` as the controller would apply it to the filtered settings."""
    if _text_setting(settings, "auth_mode") in {"token", "none"}:
        return False
    return bool(_text_setting(settings, "MLFLOW_OIDC_TOKEN_URL")) and not (
        _text_setting(settings, "MLFLOW_OIDC_CLIENT_ID") and settings.get("MLFLOW_OIDC_CLIENT_SECRET_saved") is True)


def _tracking_public(settings: Mapping[str, object], auth_mode: str) -> dict | None:
    uri = _text_setting(settings, "MLFLOW_TRACKING_URI")
    if not uri.strip():
        return None
    location = _text_setting(settings, "FACTORY_MLFLOW_ARTIFACT_LOCATION") or None
    # Exactly what the controller's LazyMLflowTracker.public_settings() reports (D11, D13).
    return public_tracking_settings(uri, DEFAULT_EXPERIMENT, location, auth_mode=auth_mode)


def tracking_destination(settings: Mapping[str, object], *, auth_mode: str) -> TrackingDestination | None:
    """The pinned MLflow destination: ``tracking_settings_sha256(public_tracking_settings(...))`` (D11, D13)."""
    public = _tracking_public(settings, auth_mode)
    if public is None:
        return None
    sha = tracking_settings_sha256(public)
    return TrackingDestination(profile_id=TRACKING_PROFILE_ID, revision=sha, public_settings_sha256=sha)


# -- observations -----------------------------------------------------------------------------------

def observations_dir(state_dir: Path) -> Path:
    return Path(state_dir) / "workflows" / "observations"


def _observation_path(state_dir: Path, profile_id: str) -> Path:
    return observations_dir(state_dir) / f"{profile_key(profile_id)}.json"


def _observation_body(profile_id, revision, observed_at, profile, facts) -> dict:
    return {"schema": OBSERVATION_SCHEMA, "profile_id": profile_id, "revision": revision,
            "observed_at": observed_at.isoformat(), "profile": profile.model_dump(mode="json"),
            "facts": facts.model_dump(mode="json")}


def _stored_profile_id(path: Path) -> str | None:
    try:
        data = json.loads(path.read_bytes())
    except (OSError, ValueError):
        return None
    value = data.get("profile_id") if isinstance(data, dict) else None
    return value if isinstance(value, str) else None


def save_observation(state_dir: Path, profile: PublicExecutorProfile, facts: ResourceObservation, *,
                     revision: str, observed_at: datetime) -> None:
    """Store the public observation of one profile (``state/workflows/observations/<key>.json``, atomic).

    It holds the public profile, the canonical facts, the profile revision and the observation time: never a field
    value, key path or credential. The latest observation replaces the previous one.
    """
    try:
        profile = _PROFILE.validate_python(profile.model_dump())
        facts = _FACTS.validate_python(facts.model_dump())
    except ValidationError:
        raise ValueError(OBSERVATION_INVALID) from None
    validate_resource_binding(profile, facts)
    if profile.revision != revision:
        raise ValueError("observation revision differs from its public profile")
    if not isinstance(observed_at, datetime) or observed_at.utcoffset() is None:
        raise ValueError("resource observations require an explicit timezone")
    path = _observation_path(state_dir, profile.profile_id)
    existing = _stored_profile_id(path)
    if existing is not None and existing != profile.profile_id:
        raise ValueError(OBSERVATION_CONFLICT)
    body = _observation_body(profile.profile_id, revision, observed_at, profile, facts)
    _replace(path, canonical({**body, "sha256": digest(canonical(body))}))


def _observation_code(exc: ValueError) -> str:
    return "observation_conflict" if str(exc) == OBSERVATION_CONFLICT else "observation_invalid"


def load_observation(state_dir: Path, profile_id: str) -> dict | None:
    """``{profile_id, revision, observed_at, profile, facts}`` (parsed models) or None when never observed.

    A record of another profile ID with the same key (case or trailing dots) raises ``OBSERVATION_CONFLICT``; a
    damaged record raises ``OBSERVATION_INVALID``.
    """
    path = _observation_path(state_dir, profile_id)
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError:
        raise ValueError(OBSERVATION_INVALID) from None
    try:
        data = json.loads(raw)
        if not isinstance(data, dict) or data.get("schema") != OBSERVATION_SCHEMA:
            raise ValueError(OBSERVATION_INVALID)
        stored_id = data["profile_id"]
    except (ValueError, KeyError):
        raise ValueError(OBSERVATION_INVALID) from None
    if stored_id != profile_id:
        raise ValueError(OBSERVATION_CONFLICT)
    try:
        profile = _PROFILE.validate_python(data["profile"])
        facts = _FACTS.validate_python(data["facts"])
        observed_at = datetime.fromisoformat(data["observed_at"])
        revision = data["revision"]
        body = _observation_body(stored_id, revision, observed_at, profile, facts)
        if (observed_at.utcoffset() is None or profile.profile_id != stored_id or profile.revision != revision
                or {**body, "sha256": data.get("sha256")} != data or digest(canonical(body)) != data["sha256"]):
            raise ValueError(OBSERVATION_INVALID)
        validate_resource_binding(profile, facts)
    except (ValueError, KeyError, TypeError):
        raise ValueError(OBSERVATION_INVALID) from None
    return {"profile_id": stored_id, "revision": revision, "observed_at": observed_at, "profile": profile,
            "facts": facts}


# -- HF roots: snapshots, index and target evidence ---------------------------------------------

def root_snapshots_dir(state_dir: Path) -> Path:
    """``DirectoryRootSnapshots`` source: ``<owner>/<name>/<revision>/`` with config.json and tokenizer files."""
    return Path(state_dir) / "workflows" / "roots"


def _root(value) -> HFRootRef:
    return HFRootRef.model_validate(value.model_dump() if hasattr(value, "model_dump") else value)


def _root_key(root: HFRootRef) -> str:
    return digest(canonical(_root(root).model_dump(mode="json")))


def _root_label(root: HFRootRef) -> str:
    return f"{root.repo_id}@{root.revision}"


def root_index_path(state_dir: Path, root: HFRootRef) -> Path:
    return Path(state_dir) / "workflows" / "root-index" / f"{_root_key(root)}.json"


def root_targets_path(state_dir: Path, root: HFRootRef) -> Path:
    """Where HF-root target evidence lives, beside the root index (the seam Task 9.7 fills)."""
    return Path(state_dir) / "workflows" / "root-targets" / f"{_root_key(root)}.json"


def load_root(state_dir: Path, root: HFRootRef) -> ModelContext | None:
    """The indexed ``ModelContext(root, profile)`` of a snapshotted root, or None."""
    root = _root(root)
    try:
        raw = root_index_path(state_dir, root).read_bytes()
    except FileNotFoundError:
        return None
    except OSError:
        raise ValueError(MODEL_ROOT_INVALID) from None
    try:
        entry = ModelContext.model_validate_json(raw)
    except ValidationError:
        raise ValueError(MODEL_ROOT_INVALID) from None
    if entry.root != root or entry.profile is None:
        raise ValueError(MODEL_ROOT_INVALID)
    return entry


def save_root(state_dir: Path, entry: ModelContext) -> ModelContext:
    """Index a snapshotted root; the first entry of a root wins (a pinned revision never changes)."""
    entry = ModelContext.model_validate(entry.model_dump())
    if entry.profile is None:
        raise ValueError(MODEL_ROOT_INVALID)
    try:
        immutable_write(root_index_path(state_dir, entry.root), canonical(entry.model_dump(mode="json")))
    except ValueError:
        existing = load_root(state_dir, entry.root)
        if existing is None:
            raise ValueError(MODEL_ROOT_INVALID) from None
        return existing
    return entry


def model_roots(state_dir: Path) -> list[ModelContext]:
    directory = Path(state_dir) / "workflows" / "root-index"
    if not directory.is_dir():
        return []
    found = []
    for path in sorted(directory.glob("*.json")):
        try:
            entry = ModelContext.model_validate_json(path.read_bytes())
        except (OSError, ValueError):
            continue
        if entry.profile is not None and path.stem == _root_key(entry.root):
            found.append(entry)
    return found


def save_root_targets(state_dir: Path, evidence: ModelTargetEvidence) -> None:
    """Store inspected target evidence of an HF root (immutable; Task 9.7's inspector writes here)."""
    evidence = ModelTargetEvidence.model_validate(evidence.model_dump())
    if not isinstance(evidence.base, HFRootRef):
        raise ValueError("HF-root target evidence requires an HF root base")
    try:
        immutable_write(root_targets_path(state_dir, evidence.base), canonical(evidence.model_dump(mode="json")))
    except ValueError:
        raise ValueError(ROOT_TARGETS_INVALID) from None


def load_root_targets(state_dir: Path, root: HFRootRef) -> ModelTargetEvidence | None:
    """Target evidence of exactly this root, or None. Planning passes it as ``ExecutionChoices.base_targets``."""
    root = _root(root)
    try:
        raw = root_targets_path(state_dir, root).read_bytes()
    except FileNotFoundError:
        return None
    except OSError:
        raise ValueError(ROOT_TARGETS_INVALID) from None
    try:
        evidence = ModelTargetEvidence.model_validate_json(raw)
    except ValidationError:
        raise ValueError(ROOT_TARGETS_INVALID) from None
    if evidence.base != root:
        raise ValueError(ROOT_TARGETS_INVALID)
    return evidence


# -- benchmarks --------------------------------------------------------------------------------------

class BenchmarkStore:
    """Benchmarks content-addressed by ``BenchmarkSpec.fingerprint()`` in ``state/workflows/benchmarks/`` (D17)."""

    def __init__(self, root: Path):
        self.root = Path(root)

    def _path(self, revision_sha256: str) -> Path:
        if not isinstance(revision_sha256, str) or not _HEX64.fullmatch(revision_sha256):
            raise ValueError(BENCHMARK_UNKNOWN)
        return self.root / f"{revision_sha256}.json"

    def save(self, spec: BenchmarkSpec) -> BenchmarkRevision:
        spec = BenchmarkSpec.model_validate(spec.model_dump())
        revision = BenchmarkRevision(spec=spec, revision_sha256=spec.fingerprint())
        try:
            immutable_write(self._path(revision.revision_sha256), canonical(revision.model_dump(mode="json")))
        except ValueError:
            # The same fingerprint over other bytes: a damaged or edited file, never a second version.
            raise ValueError(BENCHMARK_STORED_DIFFERS) from None
        return revision

    def get(self, revision_sha256: str) -> BenchmarkRevision:
        path = self._path(revision_sha256)
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            raise ValueError(BENCHMARK_UNKNOWN) from None
        try:
            revision = BenchmarkRevision.model_validate_json(raw)   # re-validates the fingerprint
        except ValidationError:
            raise ValueError(BENCHMARK_STORED_DIFFERS) from None
        if revision.revision_sha256 != revision_sha256:
            raise ValueError(BENCHMARK_STORED_DIFFERS)
        return revision

    def list(self) -> list[BenchmarkRevision]:
        if not self.root.is_dir():
            return []
        found = []
        for path in sorted(self.root.glob("*.json")):
            try:
                found.append(self.get(path.stem))
            except ValueError:
                continue    # a damaged file is refused when a graph pins it
        return found


def _benchmarks(context: WorkflowContext) -> BenchmarkStore:
    return BenchmarkStore(Path(context.state_dir) / "workflows" / "benchmarks")


# -- stored plans and their public summary -------------------------------------------------------

def plans_dir(state_dir: Path) -> Path:
    return Path(state_dir) / "workflows" / "plans"


def _tracking_path(state_dir: Path, sha: str) -> Path:
    return Path(state_dir) / "workflows" / "tracking" / f"{sha}.json"


def _save_plan(state_dir: Path, plan: GraphPlan, public_tracking: dict | None) -> None:
    try:
        if public_tracking is not None:
            immutable_write(_tracking_path(state_dir, tracking_settings_sha256(public_tracking)),
                            canonical(public_tracking))
    except ValueError:
        raise ValueError(TRACKING_STORED_DIFFERS) from None
    try:
        immutable_write(plans_dir(state_dir) / f"{plan.sha256}.json", canonical(plan.model_dump(mode="json")))
    except ValueError:
        raise ValueError(PLAN_STORED_DIFFERS) from None


def stored_plan(state_dir: Path, plan_sha256: str) -> GraphPlan:
    """A stored plan, verified by its hash on every read (used by 9.3)."""
    if not isinstance(plan_sha256, str) or not _HEX64.fullmatch(plan_sha256):
        raise ValueError(PLAN_UNKNOWN)
    try:
        raw = (plans_dir(state_dir) / f"{plan_sha256}.json").read_bytes()
    except FileNotFoundError:
        raise ValueError(PLAN_UNKNOWN) from None
    try:
        plan = _check_plan(GraphPlan.model_validate_json(raw))
    except ValueError:
        raise ValueError(PLAN_MISMATCH) from None
    if plan.sha256 != plan_sha256:
        raise ValueError(PLAN_MISMATCH)
    return plan


def _stored_tracking(state_dir: Path | None, sha: str) -> dict | None:
    if state_dir is None:
        return None
    try:
        public = json.loads(_tracking_path(state_dir, sha).read_bytes())
        return public if tracking_settings_sha256(public) == sha else None
    except (OSError, ValueError):
        return None


def _cost_source(resource) -> str:
    if isinstance(resource.facts, SSHServerFacts):
        return resource.facts.cost_source
    executor = resource.executor
    if executor.credential_scope == ESTIMATE_SCOPE and executor.profile_id.startswith(ESTIMATE_PREFIX):
        return "estimate"
    return "runpod_api"


_RECIPE_FIELDS = ("recipe_id", "version", "name", "training_stage", "training_method")


def _node_references(node, recipes: Mapping, bindings: Mapping, benchmarks: Mapping) -> dict:
    """Task 9.8: what a plan node uses, for the approval view. ``recipe``: a train node's pinned recipe revision
    (``revision_sha256`` and its public identity), else None. ``datasets``: a train node's bound dataset (with its
    ``binding_id``) or an evaluate node's benchmark dataset (with its ``benchmark_revision``), else empty."""
    recipe, datasets = None, []
    if isinstance(node, TrainNode):
        spec = recipes.get(node.recipe_revision)
        recipe = {**{name: getattr(spec, name, None) for name in _RECIPE_FIELDS},
                  "revision_sha256": node.recipe_revision}
        ref = bindings.get(node.dataset_binding)
        if ref is not None:
            datasets.append({"binding_id": node.dataset_binding, "benchmark_revision": None,
                             **ref.model_dump(mode="json")})
    elif isinstance(node, EvaluateNode):
        ref = benchmarks.get(node.benchmark_revision)
        if ref is not None:
            datasets.append({"binding_id": None, "benchmark_revision": node.benchmark_revision,
                             **ref.model_dump(mode="json")})
    return {"recipe": recipe, "datasets": datasets}


def plan_summary(plan: GraphPlan, state_dir: Path | None = None) -> dict:
    """The public approval view of a plan (used by 9.3 and the CLI), with each node's recipe and data (9.8).

    It never contains a key path, an API key or any setting other than the public tracking settings, which are read
    from the hash-verified store under ``state_dir`` (None without it).
    """
    plan = _check_plan(plan)
    choices = plan.execution_choices
    destination = choices.tracking_destination
    recipes = {item.revision_sha256: item.spec for item in choices.recipes}
    bindings = {item.binding_id: item.dataset for item in plan.project_revision.spec.datasets}
    benchmarks = {item.revision_sha256: item.spec.dataset for item in choices.benchmarks}
    return {
        "plan_sha256": plan.sha256, "simulation": plan.simulation,
        "project_id": plan.project_revision.spec.project_id, "project_revision": plan.project_revision.revision_sha256,
        "max_estimated_cost_usd": plan.max_estimated_cost_usd, "max_duration_seconds": plan.max_duration_seconds,
        "max_concurrent_gpu_count": plan.max_concurrent_gpu_count,
        "retry_policy": plan.graph.retry_policy.model_dump(mode="json"),
        "nodes": [{"node_id": entry.node.id, "kind": entry.node.kind, "label": entry.node.label,
                   "max_estimated_cost_usd": entry.max_estimated_cost_usd,
                   "max_duration_seconds": entry.max_duration_seconds,
                   "allowed": [{"profile_id": resource.executor.profile_id, "revision": resource.executor.revision,
                                "resource_id": resource.executor.exclusivity_key, "gpu_type": resource.facts.gpu_type,
                                "gpu_count": resource.facts.gpu_count,
                                "total_hourly_cost_usd": resource.facts.total_hourly_cost_usd,
                                "cost_source": _cost_source(resource)} for resource in entry.allowed_resources],
                   **_node_references(entry.node, recipes, bindings, benchmarks)}
                  for entry in plan.nodes],
        "datasets": [{**item.dataset.model_dump(mode="json"), "synthetic": item.synthetic,
                      "export_allowed": item.export_allowed} for item in choices.datasets],
        "tracking": None if destination is None else {
            "profile_id": destination.profile_id, "public_settings_sha256": destination.public_settings_sha256,
            "settings": _stored_tracking(state_dir, destination.public_settings_sha256)},
    }


# -- request models ------------------------------------------------------------------------------

class ProfileInspect(StrictModel):
    profile_id: LocalId


class RootSnapshot(StrictModel):
    repo_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}/[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$")
    revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    download_approved: bool = Field(strict=True)


class BenchmarkSave(StrictModel):
    spec: BenchmarkSpec


class GraphValidate(StrictModel):
    project_id: LocalId
    revision: Sha256 | None = None


class NodeResourceChoice(StrictModel):
    node_id: LocalId
    timeout_seconds: int = Field(strict=True, ge=60, le=604800)
    runtime_profile: Literal["primary", "ppo"] = "primary"


class PlanningEstimate(StrictModel):
    profile_id: str = Field(pattern=r"^estimate-[A-Za-z0-9_.-]{1,56}$")
    gpu_type: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9 ._()-]*$")
    gpu_count: int = Field(strict=True, ge=1, le=8)
    total_hourly_cost_usd: float = Field(gt=0, le=1000)


class GraphPlanRequest(StrictModel):
    project_id: LocalId
    project_revision: Sha256 | None = None
    simulation: bool = Field(default=True, strict=True)
    node_resources: tuple[NodeResourceChoice, ...] = Field(default=(), max_length=64)
    estimates: tuple[PlanningEstimate, ...] = Field(default=(), max_length=16)


class PlanRef(StrictModel):
    plan_sha256: Sha256


class NodeTimeLimit(StrictModel):
    node_id: Annotated[LocalId, Field(max_length=128)]
    timeout_minutes: int = Field(strict=True, ge=1, le=10080)    # graph_plan's 60-604800 s, in whole minutes


class PlanningInputsRef(StrictModel):
    project_id: LocalId


class PlanningInputsSave(StrictModel):
    """What ``graph_plan`` needs beyond the saved project and no backend kept before 9.8: never a secret."""
    project_id: LocalId
    project_revision: Sha256                     # compare-and-swap: the project's current revision
    simulation: bool = Field(default=True, strict=True)
    node_time_limits: tuple[NodeTimeLimit, ...] = Field(default=(), max_length=64)
    estimates: tuple[PlanningEstimate, ...] = Field(default=(), max_length=16)


class _StoredPlanningInputs(StrictModel):
    project_id: LocalId
    project_revision: Sha256
    simulation: bool = Field(strict=True)
    node_time_limits: tuple[NodeTimeLimit, ...] = Field(max_length=64)
    estimates: tuple[PlanningEstimate, ...] = Field(max_length=16)
    saved_at: datetime


# -- executor_profile_inspect ----------------------------------------------------------------------

@workflow_action("executor_profile_inspect", ProfileInspect)
def executor_profile_inspect(context: WorkflowContext, request: ProfileInspect) -> WorkflowResult:
    """A read-only observation of one native profile, stored publicly (D13). RunPod: ``status`` only, no SSH."""
    native = context.profiles.get(request.profile_id)
    if native is None:
        return invalid(issue("executor_profile_missing", PROFILE_UNKNOWN, "/profile_id"))
    fields = dict(native.fields)
    runpod = fields.get("provider") == "existing_runpod"
    try:   # every public field is checked offline, before any request
        stored = _stored_profile_id(_observation_path(context.state_dir, request.profile_id))
        revision = profile_revision(request.profile_id, fields)
        check_key_paths(fields)
        offline = public_profile(request.profile_id, fields) if runpod else None
        if not runpod:
            server_settings({"provider": "ssh_server", **fields})
    except (ValueError, TypeError) as exc:
        return invalid(issue("executor_profile_invalid", _reason(exc), "/profile_id"))
    if stored is not None and stored != request.profile_id:
        return invalid(issue("observation_conflict", OBSERVATION_CONFLICT, "/profile_id"))
    key = context.secrets.get("RUNPOD_API_KEY")
    if runpod and not key:
        return invalid(issue("credential_missing", CREDENTIAL_MISSING, "/profile_id"))
    now = _now(context)
    from . import desktop_runtime, execution
    try:
        if runpod:
            controller = execution.RunPodController(key)
            try:
                pod = controller.status(fields["pod_id"])
            finally:
                client = getattr(controller, "client", None)
                if client is not None:
                    client.close()
            desktop_runtime.validate_pod(pod)
            facts = runpod_facts(pod, observed_at=now)
            profile = offline
            validate_resource_binding(profile, facts)
        else:
            with desktop_runtime.connection({"provider": "ssh_server", **fields}) as (observed, _):
                facts = _FACTS.validate_python(observed.model_dump())
            profile = public_profile(request.profile_id, fields, facts)
    except desktop_runtime.RemoteCommandFailed:   # its text carries remote output: a fixed text only
        return invalid(issue("remote_command_failed", REMOTE_COMMAND_FAILED))
    except (execution.ExecutionUncertain, TimeoutError, ValueError) as exc:
        return invalid(issue("inspection_failed", _reason(exc)))
    except Exception as exc:   # provider and transport errors: the type name only, never their text
        return invalid(issue("inspection_failed", type(exc).__name__))
    try:
        save_observation(context.state_dir, profile, facts, revision=revision, observed_at=now)
    except ValueError as exc:
        return invalid(issue(_observation_code(exc), _reason(exc), "/profile_id"))
    return ok({"profile": profile.model_dump(mode="json"), "facts": facts.model_dump(mode="json"),
               "observed_at": now.isoformat(), "revision": revision})


# -- model_root_snapshot / model_roots_list ------------------------------------------------------

class _Refused(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def https_only(request: httpx.Request) -> None:
    """An httpx request hook: every hop, redirects included, stays on HTTPS (checked before it is sent)."""
    if request.url.scheme != "https":
        raise _Refused("insecure_redirect", INSECURE_REDIRECT)


METADATA_LIMIT = 4 * 1024 * 1024
METADATA_TOO_LARGE = "Hugging Face metadata exceeds the inspection size limit"   # ModelHub.get's text


class _BoundedHub(ModelHub):
    """``ModelHub`` whose metadata reads go through ``hf_targets._open`` and ``_read`` (Task 11.5, R2 M3): at most
    ``MAX_REDIRECTS`` redirects, each closed with its body unread, and at most ``METADATA_LIMIT`` body bytes (one
    chunk more refuses) instead of a whole body read before the check."""

    def get(self, url, optional=False, **kwargs):
        from .hf_targets import ENCODED_RESPONSE, _identity, _open, _read
        if kwargs:
            raise TypeError("the snapshot client reads plain metadata URLs only")
        try:
            response = _open(self.client, url, code="snapshot_failed")
            try:
                if optional and response.status_code == 404:
                    return None
                if response.status_code != 200:
                    raise ValueError(f"Hugging Face metadata request failed (HTTP {response.status_code}); "
                                     "check model access")
                _identity(response, _Refused("snapshot_failed", ENCODED_RESPONSE))   # the cap counts wire bytes
                body = _read(response, METADATA_LIMIT, ValueError(METADATA_TOO_LARGE))
            finally:
                response.close()
        except httpx.HTTPError:
            raise ValueError(HF_CONNECTION) from None
        return httpx.Response(200, content=body, request=response.request)


def _hub(token: str | None, *, transport: httpx.BaseTransport | None = None) -> ModelHub:
    """The Hugging Face client; an Authorization header only when ``HF_TOKEN`` was scoped to this request.

    Redirects are followed by ``hf_targets._open``, never by httpx: its ``next_request`` drops the header on
    redirects to another origin (the CDN), and the request hook refuses a hop to plain HTTP. Responses are never
    content-decoded (``identity``; an encoded answer is refused), so every cap counts the bytes received."""
    headers = {"Accept-Encoding": "identity"}
    if token:
        headers["Authorization"] = "Bearer " + token
    return _BoundedHub(client=httpx.Client(timeout=20, follow_redirects=False, headers=headers, transport=transport,
                                           event_hooks={"request": [https_only]}))


def _selected_files(info) -> tuple[str, ...]:
    """config.json plus every tokenizer and processor file the revision lists; anything weight-like or unsafe
    refuses all."""
    siblings = info.get("siblings") if isinstance(info, dict) else None
    if not isinstance(siblings, list):
        raise _Refused("snapshot_refused", SNAPSHOT_REFUSED)
    names = set()
    for item in siblings:
        name = item.get("rfilename") if isinstance(item, dict) else None
        if not isinstance(name, str):
            raise _Refused("snapshot_refused", SNAPSHOT_REFUSED)
        if name != "config.json" and not is_tokenizer_file(name) and not is_processor_file(name):
            continue
        try:
            _relative(name)
        except ValueError:
            raise _Refused("snapshot_refused", SNAPSHOT_REFUSED) from None
        if name.casefold().endswith(WEIGHT_SUFFIXES):
            raise _Refused("snapshot_refused", SNAPSHOT_REFUSED)
        names.add(name)
    if ("config.json" not in names or len(names) > MAX_ROOT_FILES
            or len({name.casefold() for name in names}) != len(names)):
        raise _Refused("snapshot_refused", SNAPSHOT_REFUSED)
    if "tokenizer_config.json" not in names or not names & CORE_TOKENIZER_FILES:
        raise _Refused("tokenizer_incomplete", TOKENIZER_INCOMPLETE)
    return tuple(sorted(names))


def _download(hub: ModelHub, url: str, target: Path, budget: int) -> int:
    """One file into ``target`` within the per-file and remaining total caps; the byte count. Read through
    ``hf_targets._open`` and ``_read`` (Task 11.5, R2 M3): redirect bodies stay unread and the body is capped."""
    from .hf_targets import ENCODED_RESPONSE, _identity, _open, _read
    limit = min(MAX_ROOT_FILE_BYTES, budget)
    too_large = _Refused("size_limit", ROOT_FILE_TOO_LARGE)
    try:
        response = _open(hub.client, url, code="snapshot_failed")
        try:
            if response.status_code != 200:
                raise ValueError(f"Hugging Face file request failed (HTTP {response.status_code}); "
                                 "check model access")
            _identity(response, _Refused("snapshot_failed", ENCODED_RESPONSE))
            declared = response.headers.get("content-length", "").strip()
            if declared.isdigit() and int(declared) > limit:
                raise too_large
            body = _read(response, limit, too_large)
        finally:
            response.close()
    except httpx.HTTPError:
        raise ValueError(HF_CONNECTION) from None
    with target.open("xb") as stream:
        stream.write(body)
        stream.flush()
        os.fsync(stream.fileno())
    return len(body)


def _publish(temporary: Path, final: Path, root: HFRootRef, roots: Path) -> bool:
    """Rename the complete download into place; an existing directory is reused only if it is this root."""
    from .simulated_dispatch import DirectoryRootSnapshots
    final.parent.mkdir(parents=True, exist_ok=True)
    if not final.exists():
        try:
            os.rename(temporary, final)
            return True
        except OSError:
            if not final.exists():
                raise
    if DirectoryRootSnapshots(roots).snapshot(root) is None:
        raise _Refused("root_snapshot_conflict", ROOT_SNAPSHOT_CONFLICT)
    return False


@workflow_action("model_root_snapshot", RootSnapshot)
def model_root_snapshot(context: WorkflowContext, request: RootSnapshot) -> WorkflowResult:
    """An explicit, user-approved metadata download of one pinned HF revision (D14): never weights."""
    if request.download_approved is not True:
        return invalid(issue("approval_required", DOWNLOAD_APPROVAL_REQUIRED, "/download_approved"))
    owner, name = request.repo_id.split("/")
    if any(part != part.rstrip(". ") or part.split(".")[0].casefold() in _DEVICE_NAMES for part in (owner, name)):
        return invalid(issue("repository_unsupported", REPOSITORY_UNSUPPORTED, "/repo_id"))
    roots = root_snapshots_dir(context.state_dir)
    hub = _hub(context.secrets.get("HF_TOKEN") or None)
    temporary = None
    try:
        inspected = hub.inspect(request.repo_id, request.revision)
        if not inspected.get("supported") or not inspected.get("profile"):
            reasons = [reason for reason in inspected.get("reasons") or () if isinstance(reason, str)]
            raise _Refused("model_unsupported", "; ".join(reasons) or MODEL_UNSUPPORTED)
        profile = ModelProfile.model_validate(inspected["profile"])
        info = hub.get(f"{HF_ORIGIN}/api/models/{request.repo_id}/revision/{request.revision}").json()
        if not isinstance(info, dict) or info.get("sha") != request.revision:
            raise ValueError("Hugging Face revision lookup did not return the requested immutable commit")
        names = _selected_files(info)
        roots.mkdir(parents=True, exist_ok=True)
        temporary = Path(tempfile.mkdtemp(prefix=".download-", dir=roots))
        used = 0
        for file in names:
            target = temporary.joinpath(*file.split("/"))
            target.parent.mkdir(parents=True, exist_ok=True)
            url = f"{HF_ORIGIN}/{request.repo_id}/resolve/{request.revision}/{quote(file, safe='/')}"
            used += _download(hub, url, target, MAX_ROOT_TOTAL_BYTES - used)
        if digest((temporary / "config.json").read_bytes()) != profile.config_sha256:
            raise _Refused("config_changed", CONFIG_CHANGED)
        try:
            fingerprint = tokenizer_fingerprint(temporary)
        except ValueError:
            raise _Refused("tokenizer_incomplete", TOKENIZER_INCOMPLETE) from None
        root = HFRootRef(repo_id=request.repo_id, revision=request.revision, config_sha256=profile.config_sha256,
                         tokenizer_fingerprint=fingerprint)
        if _publish(temporary, roots / owner / name / request.revision, root, roots):
            temporary = None
        entry = save_root(context.state_dir, ModelContext(root=root, profile=profile))
    except _Refused as exc:
        return invalid(issue(exc.code, str(exc), "/repo_id"))
    except ValueError as exc:
        return invalid(issue("snapshot_failed", _reason(exc), "/repo_id"))
    except (OSError, TypeError, AttributeError, KeyError) as exc:   # unexpected metadata shapes: the type only
        return invalid(issue("snapshot_failed", type(exc).__name__, "/repo_id"))
    finally:
        hub.client.close()
        if temporary is not None:
            shutil.rmtree(temporary, ignore_errors=True)
    return ok({"root": entry.root.model_dump(mode="json"), "profile": entry.profile.model_dump(mode="json"),
               "files": list(names)})


@workflow_action("model_roots_list", Empty)
def model_roots_list(context: WorkflowContext, request: Empty) -> WorkflowResult:
    from .simulated_dispatch import DirectoryRootSnapshots
    snapshots = DirectoryRootSnapshots(root_snapshots_dir(context.state_dir))
    found = []
    for entry in model_roots(context.state_dir):
        try:
            targets = load_root_targets(context.state_dir, entry.root) is not None
        except ValueError:
            targets = False
        found.append({"root": entry.root.model_dump(mode="json"), "profile": entry.profile.model_dump(mode="json"),
                      "snapshot_ready": snapshots.snapshot(entry.root) is not None, "targets_ready": targets})
    return ok({"roots": found})


# -- benchmark_save / benchmarks_list ------------------------------------------------------------

@workflow_action("benchmark_save", BenchmarkSave)
def benchmark_save(context: WorkflowContext, request: BenchmarkSave) -> WorkflowResult:
    spec = request.spec
    try:   # what the worker (and plan_graph) would refuse later, with the worker's exact text
        check_generation_benchmark(spec)
    except ValueError as exc:
        return invalid(issue("benchmark_unsupported", str(exc), "/spec"))
    try:
        snapshot = snapshot_dataset(context.registry(), spec.dataset)
    except (OSError, ValueError, KeyError):
        return invalid(issue("dataset_unknown", DATASET_UNKNOWN, "/spec/dataset"))
    try:
        validate_sample_ids(spec, snapshot)
    except ValueError as exc:
        return invalid(issue("sample_id_invalid", _reason(exc), "/spec/ordered_sample_ids"))
    try:
        revision = _benchmarks(context).save(spec)
    except ValueError as exc:
        return invalid(issue("benchmark_invalid", _reason(exc), "/spec"))
    return ok({"revision_sha256": revision.revision_sha256, "spec": revision.spec.model_dump(mode="json")})


@workflow_action("benchmarks_list", Empty)
def benchmarks_list(context: WorkflowContext, request: Empty) -> WorkflowResult:
    return ok({"benchmarks": [{"revision_sha256": item.revision_sha256, "spec": item.spec.model_dump(mode="json")}
                              for item in _benchmarks(context).list()]})


# -- graph analysis shared by graph_validate and graph_plan --------------------------------------

@dataclass
class _Analysis:
    project: Revision[ProjectSpec]
    graph: GraphSpec
    index: dict[str, int] = field(default_factory=dict)
    recipes: dict[str, Revision[RecipeSpec]] = field(default_factory=dict)
    benchmarks: dict[str, BenchmarkRevision] = field(default_factory=dict)
    models: dict[str, ModelContext] = field(default_factory=dict)          # root JSON -> context
    targets: dict[str, ModelTargetEvidence] = field(default_factory=dict)  # root JSON -> evidence
    issues: list = field(default_factory=list)
    warnings: list = field(default_factory=list)

    def path(self, node_id: str) -> str:
        return f"/graph/nodes/{self.index[node_id]}"


def _load_project(context: WorkflowContext, project_id: str, revision: str | None, revision_path: str):
    if not context.definitions_exist:   # a read never creates the definitions database
        return None, invalid(issue("unknown_definition", "unknown definition", "/project_id"))
    try:
        return context.projects().get(project_id, revision), None
    except ValueError as exc:
        path = revision_path if str(exc).startswith("unknown definition revision") else "/project_id"
        return None, refusal(exc, path=path)


def _model_values(node) -> tuple:
    if isinstance(node, TrainNode):
        return node.model_input, node.reward_binding
    if isinstance(node, MergeNode):
        return node.models
    if isinstance(node, (EvaluateNode, MaterializeNode)):
        return (node.model,)
    return ()


def _references(node) -> tuple[list[HFRootRef], bool]:
    """HF roots a node loads directly, and whether it loads an external model artifact (D16)."""
    roots, external = [], False
    for value in _model_values(node):
        loads = (value.policy, value.frozen_reference, value.reward_model) if isinstance(value, ModelInput) else (value,)
        for load in loads:
            for ref in ((load.base, load.adapter) if isinstance(load, ModelLoadRef) else (load,)):
                if isinstance(ref, HFRootRef):
                    roots.append(ref)
                elif isinstance(ref, ModelArtifactRef):
                    external = True
    return roots, external


def _effective_recipe(node: TrainNode, recipes: Mapping[str, Revision[RecipeSpec]]) -> RecipeSpec | None:
    revision = recipes.get(node.recipe_revision)
    if revision is None:
        return None
    try:   # graph_planner._recipe: the node's overrides replace whole option groups
        return RecipeSpec.model_validate({**revision.spec.model_dump(), **node.overrides.model_dump(exclude_none=True)})
    except ValueError:
        return None


def _base_key(ref) -> str:
    return ref.model_dump_json() if isinstance(ref, HFRootRef) else _ARTIFACT


class _Lineage:
    """Effective bases of adapter outputs, walked back through the graph (D15).

    A model a node input resolves to is ``(is_adapter, bases)``: an adapter's effective bases, or the identity of a
    full model. LoRA/QLoRA train outputs keep their policy source's bases (a continuation keeps the parent's; a new
    adapter's base is the full model it loads); merges combine their adapter inputs; selections offer every
    candidate. Full/freeze train and materialize outputs are model artifacts whose evidence arrives at run time.
    """

    def __init__(self, analysis: _Analysis):
        self.nodes = {node.id: node for node in analysis.graph.nodes}
        self.recipes = analysis.recipes
        self.cache: dict[str, tuple] = {}

    def sources(self, value, active=frozenset()) -> tuple:
        if isinstance(value, NodeOutputRef):
            return self.outputs(value.node_id, active)
        if isinstance(value, ModelInput):
            value = value.policy
        if isinstance(value, ModelLoadRef):
            return ((value.adapter is not None, frozenset({_base_key(value.base)})),)
        if isinstance(value, ModelArtifactRef):
            return ((False, frozenset({_ARTIFACT})),)
        return ()

    def outputs(self, node_id: str, active=frozenset()) -> tuple:
        if node_id in self.cache:
            return self.cache[node_id]
        node = self.nodes.get(node_id)
        if node is None or node_id in active:
            return ()
        active = active | {node_id}
        if isinstance(node, TrainNode):
            recipe = _effective_recipe(node, self.recipes)
            if recipe is None:
                result = ()
            elif recipe.training_method in {"full", "freeze"}:
                result = ((False, frozenset({_ARTIFACT})),)
            else:
                result = tuple((True, bases) for _, bases in self.sources(node.model_input, active))
        elif isinstance(node, MergeNode):
            result = ((True, self.adapter_bases(node.models, active)),)
        elif isinstance(node, MaterializeNode):
            result = ((False, frozenset({_ARTIFACT})),)
        elif isinstance(node, SelectNode):
            result = tuple(item for ref in node.evaluations if isinstance(self.nodes.get(ref.node_id), EvaluateNode)
                           for item in self.sources(self.nodes[ref.node_id].model, active))
        else:
            result = ()
        self.cache[node_id] = result
        return result

    def adapter_bases(self, values, active=frozenset()) -> frozenset:
        return frozenset().union(*(bases for value in values for adapter, bases in self.sources(value, active)
                                   if adapter))


def _target_checks(analysis: _Analysis, simulation: bool | None) -> None:
    """D15 with the 9.7 seam: a node whose compile needs HF-root target evidence is refused unless the root has it."""
    lineage = _Lineage(analysis)
    for node in analysis.graph.nodes:
        needs = frozenset()
        if isinstance(node, MergeNode):
            needs = lineage.adapter_bases(node.models)
        elif isinstance(node, MaterializeNode):
            needs = lineage.adapter_bases((node.model,))
        elif isinstance(node, TrainNode) and isinstance(node.model_input, NodeOutputRef):
            recipe = _effective_recipe(node, analysis.recipes)
            if recipe is not None and recipe.training_method in {"lora", "qlora"}:
                needs = lineage.adapter_bases((node.model_input,))   # an adapter parent continues
        if any(key != _ARTIFACT and key not in analysis.targets for key in needs):
            analysis.issues.append(issue("base_targets_missing", BASE_TARGETS_MISSING, analysis.path(node.id),
                                         node_id=node.id))
        if (simulation and isinstance(node, TrainNode) and isinstance(node.model_input, ModelInput)
                and node.model_input.policy.adapter is None and isinstance(node.model_input.policy.base, HFRootRef)
                and _base_key(node.model_input.policy.base) not in analysis.targets):
            recipe = _effective_recipe(node, analysis.recipes)
            if recipe is not None and (recipe.training_method in {"full", "freeze"} or (
                    recipe.training_method in {"lora", "qlora"} and recipe.hyperparameters.lora_target == "all")):
                analysis.warnings.append(issue("simulated_execution_needs_targets", SIMULATED_NEEDS_TARGETS,
                                               analysis.path(node.id), node_id=node.id))


def _graph_schema_issues(exc: ValidationError, raw) -> tuple:
    """Pointers into the draft; pydantic's follow-on "too short" for a container whose item already failed (a node
    with an error no longer counts toward ``nodes``' minimum) is dropped."""
    errors = exc.errors()
    locations = [tuple(error["loc"]) for error in errors]
    found = issues_from_validation(exc, "/graph", data=raw)
    return tuple(item for error, item in zip(errors, found, strict=True)
                 if not (error["type"] == "too_short" and any(
                     len(other) > len(error["loc"]) and other[:len(error["loc"])] == tuple(error["loc"])
                     for other in locations)))


def _load_root_context(state: Path, root: HFRootRef, analysis: _Analysis) -> tuple[str, str] | None:
    """Index the root's ``ModelContext`` and its target evidence into ``analysis``; the issue if that fails."""
    try:
        entry = load_root(state, root)
        if entry is None:
            return "model_root_missing", MODEL_ROOT_MISSING.format(root=_root_label(root))
        evidence = load_root_targets(state, root)
    except ValueError as exc:
        code = "root_targets_invalid" if str(exc) == ROOT_TARGETS_INVALID else "model_root_invalid"
        return code, f"{_root_label(root)}: {exc}"
    analysis.models[_base_key(root)] = entry
    if evidence is not None:
        analysis.targets[_base_key(root)] = evidence
    return None


def _analyze(context: WorkflowContext, project: Revision[ProjectSpec], *, simulation: bool | None):
    """Definition checks of a saved project: graph schema, recipes, ``validate_graph``, benchmarks, HF roots,
    external artifacts (D16) and target evidence (D15). Every failure is an issue with its pointer and node."""
    raw = project.spec.graph
    try:
        graph = GraphSpec.model_validate(raw)
    except ValidationError as exc:
        return None, invalid(*_graph_schema_issues(exc, raw))
    analysis = _Analysis(project=project, graph=graph)
    for index, node in enumerate(graph.nodes):
        analysis.index.setdefault(node.id, index)
    state = Path(context.state_dir)
    store, missing = context.recipes(), {}
    for index, node in enumerate(graph.nodes):
        if not isinstance(node, TrainNode) or node.recipe_revision in analysis.recipes:
            continue
        revision = node.recipe_revision
        if revision not in missing:
            try:
                analysis.recipes[revision] = store.get_by_revision(revision)
                continue
            except ValueError as exc:
                missing[revision] = exc
        unknown = str(missing[revision]).startswith("unknown definition")
        analysis.issues.append(issue("recipe_missing" if unknown else "recipe_invalid",
                                     RECIPE_MISSING if unknown else _reason(missing[revision]),
                                     f"/graph/nodes/{index}/recipe_revision", node_id=node.id))
    for found in validate_graph(project.spec, tuple(analysis.recipes.values()), context.registry()):
        if any(revision in found.message for revision in missing):
            continue   # the same missing recipe, already reported as recipe_missing
        path = (analysis.path(found.node_id) if found.node_id in analysis.index
                else "/datasets" if found.code in _DATASET_CODES else "/graph")
        analysis.issues.append(issue(found.code, found.message, path, node_id=found.node_id))
    benchmarks = _benchmarks(context)
    for index, node in enumerate(graph.nodes):
        if isinstance(node, EvaluateNode) and node.benchmark_revision not in analysis.benchmarks:
            try:
                analysis.benchmarks[node.benchmark_revision] = benchmarks.get(node.benchmark_revision)
            except ValueError:
                analysis.issues.append(issue("benchmark_missing", BENCHMARK_MISSING,
                                             f"/graph/nodes/{index}/benchmark_revision", node_id=node.id))
    failed = {}
    for index, node in enumerate(graph.nodes):
        roots, external = _references(node)
        reported = set()
        for root in roots:
            key = _base_key(root)
            if key in analysis.models or key in reported:
                continue
            if key not in failed:
                failed[key] = _load_root_context(state, root, analysis)
            if failed[key] is not None:
                reported.add(key)
                analysis.issues.append(issue(*failed[key], f"/graph/nodes/{index}", node_id=node.id))
        if external:
            analysis.issues.append(issue("external_artifact_unsupported", EXTERNAL_ARTIFACT, f"/graph/nodes/{index}",
                                         node_id=node.id))
    _target_checks(analysis, simulation)
    return analysis, None


@workflow_action("graph_validate", GraphValidate)
def graph_validate(context: WorkflowContext, request: GraphValidate) -> WorkflowResult:
    """Offline definition checks while editing; no executor, resource or tracking checks."""
    project, refused = _load_project(context, request.project_id, request.revision, "/revision")
    if refused is not None:
        return refused
    analysis, refused = _analyze(context, project, simulation=None)
    if refused is not None:
        return refused
    if analysis.issues:
        return invalid(*analysis.issues)
    return ok({"project_id": project.spec.project_id, "project_revision": project.revision_sha256,
               "node_count": len(analysis.graph.nodes)})


# -- graph_plan ------------------------------------------------------------------------------------

def _estimate(item: PlanningEstimate, now: datetime) -> tuple[ExecutorProfile, ResourceFacts]:
    """A simulation-only planning estimate: a pinned stand-in Pod named after the estimate, never contacted."""
    profile = ExecutorProfile(profile_id=item.profile_id, revision=digest(canonical(item.model_dump(mode="json"))),
                              name=item.profile_id, pod_id=item.profile_id, credential_scope=ESTIMATE_SCOPE)
    facts = ResourceFacts(pod_id=item.profile_id, gpu_type=item.gpu_type, gpu_count=item.gpu_count,
                          total_hourly_cost_usd=item.total_hourly_cost_usd, observed_at=now)
    return profile, facts


def pin_observation(profile_id: str, fields: Mapping[str, str], observation: dict) -> PublicExecutorProfile | None:
    """The profile a plan pins from a stored observation of these native fields, or None when it cannot: the
    observation is of another revision, or the profile no longer rebuilds from the current fields with the observed
    facts (for example a ``~`` key path whose home directory changed). Fields a plan refuses raise ``ValueError``
    (``check_key_paths``, ``profile_revision``). ``graph_plan`` and ``observations_list`` decide with this one
    function, so a list's ``fresh_for_real_plan`` is what the plan will decide (9.8 review 2)."""
    check_key_paths(fields)
    if observation["revision"] != profile_revision(profile_id, fields):
        return None
    try:   # rebuilt from the current fields: the D11 transport hash the provider will check
        return public_profile(profile_id, fields, observation["facts"])
    except ValueError:
        return None


def observation_fresh(age_seconds: float) -> bool:
    """Whether a real plan accepts an observation this old (D13): at most an hour, and not from the future."""
    return 0 <= age_seconds <= OBSERVATION_MAX_AGE_SECONDS


def _native_executor(context: WorkflowContext, profile_id: str, native: NativeProfile, now: datetime, real: bool,
                     analysis: _Analysis):
    path = f"/profiles/{profile_id}"
    try:   # before the observation, so a bad key path is reported even when nothing was observed yet
        check_key_paths(native.fields)
    except ValueError as exc:
        analysis.issues.append(issue("executor_profile_invalid", _reason(exc), path))
        return None
    try:
        observation = load_observation(context.state_dir, profile_id)
    except ValueError as exc:
        analysis.issues.append(issue(_observation_code(exc), _reason(exc), path))
        return None
    if observation is None:
        analysis.issues.append(issue("observation_missing", OBSERVATION_MISSING, path))
        return None
    try:
        pinned = pin_observation(profile_id, native.fields, observation)
    except ValueError as exc:
        analysis.issues.append(issue("executor_profile_invalid", _reason(exc), path))
        return None
    if pinned is None:
        if real:
            analysis.issues.append(issue("observation_revision_differs", OBSERVATION_REVISION_DIFFERS, path))
            return None
        analysis.warnings.append(issue("observation_revision_differs", OBSERVATION_REVISION_DIFFERS, path))
        pinned = observation["profile"]   # simulation: the destination as it was observed
    if real and not observation_fresh((now - observation["observed_at"]).total_seconds()):
        analysis.issues.append(issue("observation_stale", OBSERVATION_STALE, path))
        return None
    return pinned, observation["facts"]


def _executors(context: WorkflowContext, request: GraphPlanRequest, analysis: _Analysis, now: datetime):
    real = not request.simulation
    native_keys = {profile_id.casefold().rstrip(".") for profile_id in context.profiles}
    estimates, seen = {}, set()
    for index, item in enumerate(request.estimates):
        path, key = f"/estimates/{index}", item.profile_id.casefold().rstrip(".")
        if real:
            analysis.issues.append(issue("estimate_real", ESTIMATE_REAL, path))
        elif key in native_keys:
            analysis.issues.append(issue("estimate_collision", ESTIMATE_COLLISION, path + "/profile_id"))
        elif key in seen:
            analysis.issues.append(issue("estimate_duplicate", ESTIMATE_DUPLICATE, path + "/profile_id"))
        else:
            estimates[item.profile_id] = item
        seen.add(key)
    estimate_ids = {item.profile_id for item in request.estimates}
    profiles, facts, resolved = {}, {}, {}
    for index, node in enumerate(analysis.graph.nodes):
        if isinstance(node, SelectNode):
            continue
        for position, profile_id in enumerate(node.executor_profile_ids):
            if profile_id not in resolved:
                if profile_id in estimates:
                    resolved[profile_id] = _estimate(estimates[profile_id], now)
                elif profile_id in estimate_ids:
                    resolved[profile_id] = None              # reported once, at the estimate
                elif profile_id not in context.profiles:
                    resolved[profile_id] = "missing"
                else:
                    resolved[profile_id] = _native_executor(context, profile_id, context.profiles[profile_id], now,
                                                            real, analysis)
                if isinstance(resolved[profile_id], tuple):
                    profile, observed = resolved[profile_id]
                    profiles[profile_id] = profile
                    key = observation_key(observed)
                    if key not in facts or facts[key].observed_at < observed.observed_at:
                        facts[key] = observed   # aliases of one destination share its newest observation
            if resolved[profile_id] == "missing":
                analysis.issues.append(issue("executor_profile_missing", PROFILE_UNKNOWN,
                                             f"/graph/nodes/{index}/executor_profile_ids/{position}", node_id=node.id))
    return profiles, tuple(facts.values())


def _node_resources(request: GraphPlanRequest, analysis: _Analysis) -> dict[str, NodeResources]:
    planned = [node.id for node in analysis.graph.nodes if not isinstance(node, SelectNode)]
    chosen = {}
    for index, item in enumerate(request.node_resources):
        if item.node_id not in planned:
            analysis.issues.append(issue("node_resource_unknown", NODE_RESOURCE_UNKNOWN,
                                         f"/node_resources/{index}/node_id"))
        elif item.node_id in chosen:
            analysis.issues.append(issue("node_resource_duplicate", NODE_RESOURCE_DUPLICATE,
                                         f"/node_resources/{index}/node_id"))
        else:
            chosen[item.node_id] = NodeResources(node_id=item.node_id, timeout_seconds=item.timeout_seconds,
                                                 runtime_profile=item.runtime_profile)
    for node_id in dict.fromkeys(planned):
        if node_id not in chosen:
            analysis.issues.append(issue("node_resource_missing", NODE_RESOURCE_MISSING, "/node_resources",
                                         node_id=node_id))
    return chosen


def _tracking(settings: Mapping[str, object], analysis: _Analysis):
    """Real plans: the MLflow HTTPS rule and OIDC completeness as the controller will apply them, then D11."""
    from .auth import INSECURE_TRACKING, OIDC_CLIENT_MISSING
    if not _text_setting(settings, "MLFLOW_TRACKING_URI").strip():
        analysis.issues.append(issue("tracking_required", TRACKING_REQUIRED, "/tracking"))
        return None, None
    mode = public_auth_mode(settings)
    uri = _text_setting(settings, "MLFLOW_TRACKING_URI")
    location = _text_setting(settings, "FACTORY_MLFLOW_ARTIFACT_LOCATION")
    found = []
    try:
        insecure = mode != "none" and (urlsplit(uri).scheme != "https"
                                       or bool(location) and urlsplit(location).scheme == "http")
    except ValueError as exc:
        insecure = False
        found.append(issue("tracking_invalid", _reason(exc), "/tracking"))
    if insecure:
        found.append(issue("tracking_insecure", INSECURE_TRACKING, "/tracking"))
    if _oidc_incomplete(settings):
        found.append(issue("tracking_incomplete", OIDC_CLIENT_MISSING, "/tracking"))
    if found:
        analysis.issues.extend(found)
        return None, None
    try:
        public = _tracking_public(settings, mode)
        destination = tracking_destination(settings, auth_mode=mode)
    except ValueError as exc:
        analysis.issues.append(issue("tracking_invalid", _reason(exc), "/tracking"))
        return None, None
    return destination, public


def _dataset_key(ref) -> tuple[str, str, str]:
    return ref.dataset_id, ref.version, ref.sha256


def _datasets(context: WorkflowContext, analysis: _Analysis) -> dict:
    registry, snapshots = context.registry(), {}
    reported = any(item.code == "dataset" for item in analysis.issues)
    for item in analysis.project.spec.datasets:
        key = _dataset_key(item.dataset)
        if key in snapshots:
            continue
        try:
            snapshots[key] = snapshot_dataset(registry, item.dataset)
        except (OSError, ValueError, KeyError) as exc:
            if not reported:
                message = _reason(exc) if isinstance(exc, ValueError) else DATASET_UNKNOWN
                analysis.issues.append(issue("dataset", message, "/datasets"))
                reported = True
    return snapshots


def _data_checks(analysis: _Analysis, snapshots: Mapping) -> None:
    """Real plans: practice data never runs for real, and only exportable data may leave the PC."""
    from .desktop import PRACTICE_SUBMIT_REFUSAL, reserved_tutorial_id
    bindings = {item.binding_id: item.dataset for item in analysis.project.spec.datasets}
    for index, node in enumerate(analysis.graph.nodes):
        used = []
        if isinstance(node, TrainNode) and node.dataset_binding in bindings:
            used.append((f"/graph/nodes/{index}/dataset_binding", bindings[node.dataset_binding]))
        if isinstance(node, EvaluateNode) and node.benchmark_revision in analysis.benchmarks:
            used.append((f"/graph/nodes/{index}/benchmark_revision",
                         analysis.benchmarks[node.benchmark_revision].spec.dataset))
        for path, ref in used:
            snapshot = snapshots.get(_dataset_key(ref))
            if reserved_tutorial_id({"dataset_id": ref.dataset_id}):
                analysis.issues.append(issue("practice_data", PRACTICE_SUBMIT_REFUSAL, path, node_id=node.id))
            elif snapshot is not None and not snapshot.export_allowed:
                analysis.issues.append(issue("export_not_allowed", EXPORT_NOT_ALLOWED, path, node_id=node.id))


def _blocked(analysis: _Analysis) -> WorkflowResult:
    warnings = [warning.model_dump(mode="json") for warning in analysis.warnings]
    return invalid(*analysis.issues, data={"warnings": warnings} if warnings else None)


@workflow_action("graph_plan", GraphPlanRequest)
def graph_plan(context: WorkflowContext, request: GraphPlanRequest) -> WorkflowResult:
    """An offline plan of a saved project (simulation by default), stored immutably by its hash."""
    project, refused = _load_project(context, request.project_id, request.project_revision, "/project_revision")
    if refused is not None:
        return refused
    analysis, refused = _analyze(context, project, simulation=request.simulation)
    if refused is not None:
        return refused
    real = not request.simulation
    now = _now(context)
    profiles, facts = _executors(context, request, analysis, now)
    resources = _node_resources(request, analysis)
    destination, public_tracking = _tracking(context.settings, analysis) if real else (None, None)
    snapshots = _datasets(context, analysis)
    if real:
        _data_checks(analysis, snapshots)
    if analysis.issues:
        return _blocked(analysis)
    choices = ExecutionChoices(recipes=tuple(analysis.recipes.values()),
                               benchmarks=tuple(analysis.benchmarks.values()), datasets=tuple(snapshots.values()),
                               models=tuple(analysis.models.values()), executors=tuple(profiles.values()),
                               node_resources=tuple(resources.values()), base_targets=tuple(analysis.targets.values()),
                               tracking_destination=destination)
    try:
        plan = plan_graph(project, choices, facts, simulation=request.simulation)
    except NodeRefusal as exc:   # one node's refusal points at its card (Task 11.5 re-review m1)
        known = exc.node_id in analysis.index
        analysis.issues.append(issue("plan_refused", _reason(exc), analysis.path(exc.node_id) if known else "",
                                     node_id=exc.node_id if known else None))
        return _blocked(analysis)
    except ValueError as exc:
        analysis.issues.append(issue("plan_refused", _reason(exc)))
        return _blocked(analysis)
    try:
        _save_plan(context.state_dir, plan, public_tracking)
    except ValueError as exc:
        analysis.issues.append(issue("plan_invalid", str(exc)))
        return _blocked(analysis)
    return ok(plan_summary(plan, context.state_dir), warnings=analysis.warnings)


@workflow_action("graph_plan_get", PlanRef)
def graph_plan_get(context: WorkflowContext, request: PlanRef) -> WorkflowResult:
    try:
        plan = stored_plan(context.state_dir, request.plan_sha256)
    except ValueError as exc:
        return invalid(issue("plan_unknown" if str(exc) == PLAN_UNKNOWN else "plan_invalid", str(exc),
                             "/plan_sha256"))
    return ok(plan_summary(plan, context.state_dir))


# -- 9.8: stored observations, the SSH GPU inventory and planning inputs ----------------------------

def _observation_entry(context: WorkflowContext, path: Path, now: float) -> dict:
    """One stored observation as public facts; ``{profile_id, error}`` for a record that does not verify."""
    stored = _stored_profile_id(path)
    try:
        if stored is None or profile_key(stored) != path.stem:
            raise ValueError(OBSERVATION_INVALID)
        observation = load_observation(context.state_dir, stored)
        if observation is None:    # removed meanwhile
            raise ValueError(OBSERVATION_INVALID)
    except ValueError:
        return {"profile_id": stored if stored is not None and _PROFILE_ID.fullmatch(stored) else path.stem,
                "error": OBSERVATION_INVALID}
    profile, facts = observation["profile"], observation["facts"]
    native = context.profiles.get(stored)
    try:   # whether the native profile still has the observed revision (None: no such profile any more)
        current = None if native is None else profile_revision(stored, native.fields) == observation["revision"]
    except ValueError:
        current = False
    try:   # graph_plan's own rule (pin_observation), so the flag below is what a real plan will decide
        pinned = None if native is None else pin_observation(stored, native.fields, observation)
    except ValueError:
        pinned = None
    age = now - observation["observed_at"].timestamp()
    ssh = isinstance(facts, SSHServerFacts)
    return {"profile_id": stored, "revision": observation["revision"],
            "observed_at": observation["observed_at"].isoformat(), "age_seconds": round(age, 3),
            "provider": profile.provider, "name": profile.name, "resource_id": facts.exclusivity_key,
            "gpu_type": facts.gpu_type, "gpu_count": facts.gpu_count,
            "total_hourly_cost_usd": facts.total_hourly_cost_usd,
            "cost_source": facts.cost_source if ssh else "runpod_api",
            "selected_gpu_uuids": list(facts.selected_gpu_uuids) if ssh else [],
            "profile_current": current,
            # What a real plan accepts (D13): an observation it can pin for the current fields, at most an hour old.
            "fresh_for_real_plan": pinned is not None and observation_fresh(age)}


@workflow_action("observations_list", Empty)
def observations_list(context: WorkflowContext, request: Empty) -> WorkflowResult:
    """Each profile's last stored observation (Task 10 Q-B): public facts only, never a field value, key path or
    credential. A read: it creates nothing."""
    directory = observations_dir(context.state_dir)
    if not directory.is_dir():
        return ok({"observations": []})
    now = context.clock()
    found = [_observation_entry(context, path, now) for path in sorted(directory.glob("*.json")) if path.is_file()]
    return ok({"observations": sorted(found, key=lambda item: item["profile_id"])})


@workflow_action("executor_profile_inventory", ProfileInspect)
def executor_profile_inventory(context: WorkflowContext, request: ProfileInspect) -> WorkflowResult:
    """The GPU listing of an SSH profile that may have no GPU selection yet (Task 10 Q-C), for GPU checkboxes.

    It reuses the legacy ``server_inspect`` probe (6A): the fixed Linux identity read, then the restricted
    ``nvidia-smi --query-gpu=uuid,name,memory.total,mig.mode.current`` query. A saved selection is checked offline
    and reported, never required (the probe runs without it, so a GPU that is gone never hides the others). Nothing
    is stored and no secret is needed: SSH reads the local key file path. RunPod profiles are refused."""
    native = context.profiles.get(request.profile_id)
    if native is None:
        return invalid(issue("executor_profile_missing", PROFILE_UNKNOWN, "/profile_id"))
    fields = dict(native.fields)
    if fields.get("provider") != "ssh_server":
        return invalid(issue("inventory_ssh_only", INVENTORY_SSH_ONLY, "/profile_id"))
    if not fields.get("server_selected_gpu_uuids", "").strip():
        fields.pop("server_selected_gpu_uuids", None)   # the native store keeps "" before any selection
    try:   # every public field is checked offline, before any connection
        revision = profile_revision(request.profile_id, native.fields)
        check_key_paths(fields)
        config = server_settings({**fields, "provider": "ssh_server"})
    except (ValueError, TypeError) as exc:
        return invalid(issue("executor_profile_invalid", _reason(exc), "/profile_id"))
    probe = {name: value for name, value in fields.items() if name != "server_selected_gpu_uuids"}
    from . import desktop_runtime, execution
    try:
        inspection = desktop_runtime.inspect_server({**probe, "provider": "ssh_server"})
    except desktop_runtime.RemoteCommandFailed:   # its text carries remote output: a fixed text only
        return invalid(issue("remote_command_failed", REMOTE_COMMAND_FAILED))
    except (execution.ExecutionUncertain, TimeoutError, ValueError) as exc:
        return invalid(issue("inspection_failed", _reason(exc)))
    except Exception as exc:   # transport errors: the type name only, never their text
        return invalid(issue("inspection_failed", type(exc).__name__))
    return ok({"profile_id": request.profile_id, "revision": revision,
               "observed_at": inspection.observed_at.isoformat(), "server_id": inspection.server_id,
               "resource_id": inspection.exclusivity_key,
               "inventory": [gpu.model_dump(mode="json") for gpu in inspection.inventory],
               "selected_gpu_uuids": list(config.selected_gpu_uuids)})


def planning_inputs_dir(state_dir: Path) -> Path:
    return Path(state_dir) / "workflows" / "planning-inputs"


def _planning_inputs_path(state_dir: Path, project_id: str) -> Path:
    # Hashed: a file name that neither case-insensitive file systems nor Windows device names can alias.
    return planning_inputs_dir(state_dir) / f"{digest(project_id.encode())}.json"


def load_planning_inputs(state_dir: Path, project_id: str) -> _StoredPlanningInputs | None:
    """The stored planning inputs of a project, or None; a damaged record raises ``PLANNING_INPUTS_INVALID``."""
    try:
        raw = _planning_inputs_path(state_dir, project_id).read_bytes()
    except FileNotFoundError:
        return None
    except OSError:
        raise ValueError(PLANNING_INPUTS_INVALID) from None
    try:
        data = json.loads(raw)
        if not isinstance(data, dict) or data.get("schema") != PLANNING_INPUTS_SCHEMA:
            raise ValueError(PLANNING_INPUTS_INVALID)
        stored = _StoredPlanningInputs.model_validate({key: value for key, value in data.items() if key != "schema"})
    except (ValueError, TypeError):
        raise ValueError(PLANNING_INPUTS_INVALID) from None
    if stored.project_id != project_id or stored.saved_at.utcoffset() is None:
        raise ValueError(PLANNING_INPUTS_INVALID)
    return stored


def _planning_inputs_view(project: Revision[ProjectSpec], stored: _StoredPlanningInputs | None) -> dict:
    inputs = None if stored is None else {
        "project_revision": stored.project_revision, "simulation": stored.simulation,
        "node_time_limits": [item.model_dump(mode="json") for item in stored.node_time_limits],
        "estimates": [item.model_dump(mode="json") for item in stored.estimates],
        "saved_at": stored.saved_at.isoformat()}
    return {"project_id": project.spec.project_id, "current_project_revision": project.revision_sha256,
            "inputs": inputs}


@workflow_action("planning_inputs_get", PlanningInputsRef)
def planning_inputs_get(context: WorkflowContext, request: PlanningInputsRef) -> WorkflowResult:
    """A project's stored planning inputs (null before the first save) beside its current revision: inputs saved for
    an earlier revision are returned as they are, with their own ``project_revision``. A read: it creates nothing."""
    project, refused = _load_project(context, request.project_id, None, "/project_id")
    if refused is not None:
        return refused
    try:
        stored = load_planning_inputs(context.state_dir, request.project_id)
    except ValueError as exc:
        return invalid(issue("planning_inputs_invalid", str(exc), "/project_id"))
    return ok(_planning_inputs_view(project, stored))


@workflow_action("planning_inputs_save", PlanningInputsSave)
def planning_inputs_save(context: WorkflowContext, request: PlanningInputsSave) -> WorkflowResult:
    """Replace a project's planning inputs atomically, only for its current revision (``revision_conflict``
    otherwise). Time limits name the draft graph's non-select nodes once each; estimates are unique by casefold plus
    ``rstrip(".")``, as ``graph_plan`` compares them. ``graph_plan`` still checks everything again."""
    project, refused = _load_project(context, request.project_id, None, "/project_id")
    if refused is not None:
        return refused
    if project.revision_sha256 != request.project_revision:
        return conflict(issue("revision_conflict", PLANNING_INPUTS_STALE, "/project_revision"))
    nodes = project.spec.graph.get("nodes")
    # The draft graph is free JSON: only string IDs can be named (a list or object ID is unhashable; 9.8 review 1).
    planned = {item["id"] for item in nodes if isinstance(item, dict) and isinstance(item.get("id"), str)
               and item.get("kind") != "select"} if isinstance(nodes, list) else set()
    found, seen, keys = [], set(), set()
    for index, item in enumerate(request.node_time_limits):
        path = f"/node_time_limits/{index}/node_id"
        if item.node_id not in planned:
            found.append(issue("node_resource_unknown", NODE_RESOURCE_UNKNOWN, path))
        elif item.node_id in seen:
            found.append(issue("node_resource_duplicate", NODE_RESOURCE_DUPLICATE, path))
        seen.add(item.node_id)
    for index, item in enumerate(request.estimates):
        key = item.profile_id.casefold().rstrip(".")
        if key in keys:
            found.append(issue("estimate_duplicate", ESTIMATE_DUPLICATE, f"/estimates/{index}/profile_id"))
        keys.add(key)
    if found:
        return invalid(*found)
    body = {"project_id": request.project_id, "project_revision": request.project_revision,
            "simulation": request.simulation,
            "node_time_limits": [item.model_dump(mode="json") for item in request.node_time_limits],
            "estimates": [item.model_dump(mode="json") for item in request.estimates],
            "saved_at": _now(context).isoformat()}
    stored = _StoredPlanningInputs.model_validate(body)
    _replace(_planning_inputs_path(context.state_dir, request.project_id),
             canonical({"schema": PLANNING_INPUTS_SCHEMA, **body}))
    return ok(_planning_inputs_view(project, stored))
