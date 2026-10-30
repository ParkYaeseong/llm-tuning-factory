"""Header-only target evidence of HF roots (Task 9.7): the linear modules of a pinned Hugging Face base model, read
from its safetensors headers without torch and without downloading weights.

``model_root_targets`` (secret scope ``HF_TOKEN``, explicit ``download_approved``) works on a root that
``model_root_snapshot`` already indexed, so its config and tokenizer are known. Through the HF resolve URLs of the
pinned revision it reads ``model.safetensors.index.json`` (or, when that is absent, the single
``model.safetensors``). Then, for each shard, it makes exactly two HTTP range requests: ``bytes=0-7`` (the
little-endian u64 header length, at most 16 MiB) and the header JSON. A range must be answered ``206`` with the
matching ``Content-Range``; a ``200`` (the whole file) is closed unread, so weights are never streamed. Every
response is capped, and so is their total. Redirects are followed here, never by httpx: each redirect body is
discarded unread (declared or not), every hop must be HTTPS, and httpx's ``next_request`` drops the Authorization
header when a redirect leaves the origin (the CDN) and keeps ``Range``.

The evidence follows exactly the full-model artifact rule of ``target_inspection.verify_inspected_targets``: the
2D ``*.weight`` tensors that ``_not_a_target`` keeps, with ``(out, in)`` dimensions. The headers are validated by
``model_artifacts.header_tensors``, the rule set local safetensors files use. Architectures whose linear modules
are not plain 2D weights are refused, never guessed: only reviewed dense text architectures are inspected, every
tensor must sit under the causal LM's own names (``model.`` or ``lm_head.``, the ``base_model_prefix`` of all of
them), and any tensor of three or more dimensions, any expert tensor, any quantized storage, a zero dimension or no
target weight at all refuses the root. Composite models (``qwen3_5``, ``gemma4``) contribute only their language
model (``model.language_model.``, ``lm_head.``): vision and audio towers, projectors and ``mtp.*`` are ignored, and
the hybrid linear attention's 3D ``conv1d`` kernel (never a target) is allowed; the tensors of Gemma 4's KV-shared
layers, which transformers never loads, are skipped by the root's hash-bound config.json. The evidence is
stored immutably beside the root index (``workflow_planning.save_root_targets``); ``graph_plan`` passes it as
``ExecutionChoices.base_targets``, which lifts D15 for that root.

Refusals use this module's fixed texts (or 9.2's English HTTP texts); an unexpected error contributes only its type
name, so neither a server reply nor the token is ever echoed.
"""

import re
import struct
from urllib.parse import quote

import httpx
from pydantic import ValidationError

from .desktop_workflows import (
    _DEVICE_NAMES,
    WorkflowContext,
    WorkflowResult,
    conflict,
    invalid,
    issue,
    ok,
    workflow_action,
)
from .graph_planner import ModelTargetEvidence, TargetModuleEvidence
from .model_artifacts import HFRootRef, TensorMetadata, _json, _open_regular, _relative, header_tensors
from .registry import canonical, digest
from .target_inspection import (
    COMPOSITE_MODEL_TYPES,
    LANGUAGE_MODEL_PREFIX,
    _not_a_target,
    is_composite_inventory,
    unloaded_modules,
)
from .workflow_planning import (
    HF_CONNECTION,
    HF_ORIGIN,
    MODEL_ROOT_INVALID,
    MODEL_ROOT_MISSING,
    REPOSITORY_UNSUPPORTED,
    ROOT_TARGETS_INVALID,
    RootSnapshot,
    _Refused,
    https_only,
    load_root_targets,
    model_roots,
    root_snapshots_dir,
    save_root_targets,
)

INDEX_FILE = "model.safetensors.index.json"
SINGLE_FILE = "model.safetensors"
MAX_HEADER_BYTES = 16 * 1024 * 1024
MAX_INDEX_BYTES = 16 * 1024 * 1024
MAX_CONFIG_BYTES = 1024 * 1024
MAX_TARGET_TOTAL_BYTES = 64 * 1024 * 1024      # every body byte read; redirect bodies are never read
MAX_SHARDS = 256
MAX_REDIRECTS = 5
# Reviewed dense text architectures: every linear layer is an nn.Linear stored as one 2D (out, in) weight under its
# own module name (phi3's fused qkv_proj and gate_up_proj are such layers). Mixture-of-experts, hybrid
# linear-attention, multimodal and MLA architectures are refused: the pinned runtime fuses or renames their
# checkpoint tensors at load, so their headers do not give the module inventory.
# Composite models: only their language model (vision, audio, projectors and mtp.* are frozen or unloaded); hybrid
# linear attention keeps its 3-D conv1d kernel, which is never a target. Their MoE variants stay refused (fused 3-D
# experts).
HEADER_TARGET_ARCHITECTURES = frozenset({"qwen2", "qwen3", "llama", "mistral", "gemma2", "gemma3_text", "phi3",
                                         "qwen3_5", "gemma4"})
FLOAT_DTYPES = frozenset({"F16", "BF16", "F32", "F64"})
QUANTIZED_SUFFIXES = (".qweight", ".weight_packed")       # packed storage beside or instead of ``.weight``
# The names a causal LM's checkpoint uses at runtime: transformers 5.6 adds ``model.`` to a bare ``XModel`` save
# (embedding models) at load, and a classifier's ``score`` or a value head is not a module of AutoModelForCausalLM.
CAUSAL_LM_PREFIXES = ("model.", "lm_head.")
_CONTENT_RANGE = re.compile(r"bytes ([0-9]{1,20})-([0-9]{1,20})/([0-9]{1,20})")
FILE_REQUEST_FAILED = "Hugging Face file request failed (HTTP {status}); check model access"   # 9.2's text

TARGETS_APPROVAL_REQUIRED = "모델 모듈 구조를 확인하려면 다운로드 승인이 필요합니다. 가중치는 받지 않고 safetensors 헤더만 읽습니다."
ARCHITECTURE_UNSUPPORTED = ("이 모델 구조는 선형 모듈이 일반 2차원 가중치가 아니어서(융합·전문가·양자화 등) 헤더만으로 모듈 구조를 "
                            "확정할 수 없습니다. 대상 모듈을 직접 고르세요(예: q_proj,v_proj).")
RANGE_UNSUPPORTED = "모델 파일 서버가 요청한 바이트 범위만 보내지 않아, 가중치를 받지 않도록 읽기를 멈췄습니다."
HEADER_INVALID = "모델 파일의 safetensors 헤더가 올바르지 않거나 읽는 중에 파일이 바뀌었습니다."
INDEX_INVALID = "모델의 safetensors 색인(model.safetensors.index.json)이 올바르지 않거나 안전하지 않습니다."
WEIGHTS_FORMAT_UNSUPPORTED = "이 모델 버전에는 safetensors 가중치(model.safetensors 또는 model.safetensors.index.json)가 없습니다."
TARGETS_TOO_LARGE = "모델 파일 서버의 응답이 크기 제한(헤더·색인 각 16 MiB, 합계 64 MiB)을 넘어 읽기를 멈췄습니다."
TOO_MANY_REDIRECTS = "모델 파일 서버가 연결을 너무 여러 번 넘겨 읽기를 멈췄습니다."
ENCODED_RESPONSE = "모델 파일 서버가 압축된 응답을 보내 읽지 않았습니다."
TARGETS_CHANGED = ("이 고정 모델 버전에 이미 저장된 모듈 구조 정보와 읽은 헤더가 다릅니다. "
                   "고정 버전은 바뀌지 않아야 하므로 저장하지 않았습니다.")


class RootTargets(RootSnapshot):
    """``model_root_targets``: the pinned-root fields and rules of ``model_root_snapshot``."""


# -- HTTP: one client, bounded reads, exact ranges ----------------------------------------------

def _client(token: str | None, *, transport: httpx.BaseTransport | None = None) -> httpx.Client:
    """The Hugging Face client; an Authorization header only when ``HF_TOKEN`` was scoped to this request.

    Redirects are followed by ``_open``, never by httpx (which reads a redirect body in full first). Every hop passes
    the HTTPS hook before it is sent. Responses are never content-decoded (``identity``)."""
    headers = {"Accept-Encoding": "identity"}
    if token:
        headers["Authorization"] = "Bearer " + token
    return httpx.Client(timeout=20, follow_redirects=False, headers=headers, transport=transport,
                        event_hooks={"request": [https_only]})


def _open(client: httpx.Client, url: str, headers: dict[str, str] | None = None, *,
          code: str = "targets_failed") -> httpx.Response:
    """A streamed GET that follows at most ``MAX_REDIRECTS`` redirects. Each redirect is closed with its body unread;
    the next hop is httpx's ``next_request``, which drops Authorization across origins and keeps ``Range``. The
    caller closes the returned response. ``code`` is the issue code of the too-many-redirects refusal
    (``model_root_snapshot`` reads through here too)."""
    request = client.build_request("GET", url, headers=headers)
    for _ in range(MAX_REDIRECTS + 1):
        response = client.send(request, stream=True, follow_redirects=False)
        if response.next_request is None:
            return response
        request = response.next_request
        response.close()
    raise _Refused(code, TOO_MANY_REDIRECTS)


def _failed(status: int) -> _Refused:
    return _Refused("targets_failed", FILE_REQUEST_FAILED.format(status=status))


def _identity(response: httpx.Response, refused: _Refused) -> None:
    if response.headers.get("content-encoding", "identity").strip().casefold() not in {"", "identity"}:
        raise refused


def _read(response: httpx.Response, limit: int, refused: _Refused) -> bytes:
    """At most ``limit`` body bytes: one more refuses before the next chunk is pulled. ``_identity`` ran first, so
    nothing is decoded (``iter_bytes`` also serves a body the transport already holds)."""
    body = bytearray()
    for chunk in response.iter_bytes():
        body += chunk
        if len(body) > limit:
            raise refused
    return bytes(body)


def _index(client: httpx.Client, url: str, budget: int) -> bytes | None:
    """The shard index of the pinned revision, or None when the revision has none (HTTP 404)."""
    too_large = _Refused("size_limit", TARGETS_TOO_LARGE)
    response = _open(client, url)
    try:
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise _failed(response.status_code)
        _identity(response, _Refused("targets_failed", ENCODED_RESPONSE))
        limit = min(MAX_INDEX_BYTES, budget)
        declared = response.headers.get("content-length", "").strip()
        if declared.isdigit() and int(declared) > limit:     # refused before reading; undeclared sizes stream capped
            raise too_large
        return _read(response, limit, too_large)
    finally:
        response.close()


def _range(client: httpx.Client, url: str, start: int, length: int, budget: int,
           missing: _Refused | None = None) -> tuple[bytes, int]:
    """``Range: bytes=start-(start+length-1)``: a 206 with exactly that Content-Range and ``length`` bytes; the
    file's total size. A 200 is the whole file: refused with its body unread."""
    if length > budget:
        raise _Refused("size_limit", TARGETS_TOO_LARGE)
    end = start + length - 1
    refused = _Refused("range_unsupported", RANGE_UNSUPPORTED)
    response = _open(client, url, {"Range": f"bytes={start}-{end}"})
    try:
        if response.status_code == 200:
            raise refused
        if response.status_code == 404 and missing is not None:
            raise missing
        if response.status_code != 206:
            raise _failed(response.status_code)
        _identity(response, refused)
        match = _CONTENT_RANGE.fullmatch(response.headers.get("content-range", "").strip())
        if match is None or (int(match[1]), int(match[2])) != (start, end) or int(match[3]) <= end:
            raise refused
        body = _read(response, length, refused)
        if len(body) != length:
            raise refused
        return body, int(match[3])
    finally:
        response.close()


def _shard_tensors(client: httpx.Client, url: str, shard: str, budget: int,
                   missing: _Refused | None = None) -> tuple[tuple[TensorMetadata, ...], int]:
    """The validated tensors of one shard from exactly two range requests, and the bytes read."""
    prefix, total = _range(client, url, 0, 8, budget, missing)
    length = struct.unpack("<Q", prefix)[0]
    if length > MAX_HEADER_BYTES:
        raise _Refused("size_limit", TARGETS_TOO_LARGE)
    if length < 1 or 8 + length > total:
        raise _Refused("header_invalid", HEADER_INVALID)
    body, again = _range(client, url, 8, length, budget - 8)
    if again != total:               # the file changed between the two requests
        raise _Refused("header_invalid", HEADER_INVALID)
    try:
        return header_tensors(_json(body), total - 8 - length, shard), 8 + length
    except ValueError:               # pydantic's ValidationError included: its text could echo header content
        raise _Refused("header_invalid", HEADER_INVALID) from None


def _weight_map(raw: bytes) -> dict[str, str]:
    refused = _Refused("index_invalid", INDEX_INVALID)
    try:
        index = _json(raw)
    except ValueError:
        raise refused from None
    weight_map = index.get("weight_map") if isinstance(index, dict) else None
    if not isinstance(weight_map, dict) or not weight_map:
        raise refused
    for shard in weight_map.values():
        if not isinstance(shard, str) or not shard.endswith(".safetensors"):
            raise refused
        try:
            _relative(shard)
        except ValueError:
            raise refused from None
    if len(set(weight_map.values())) > MAX_SHARDS:
        raise refused
    return weight_map


def _headers(client: httpx.Client, request: RootTargets) -> tuple[tuple[str, ...], tuple[TensorMetadata, ...]]:
    """Every tensor of the pinned revision's safetensors checkpoint, from its headers only."""
    base = f"{HF_ORIGIN}/{request.repo_id}/resolve/{request.revision}/"
    budget = MAX_TARGET_TOTAL_BYTES
    raw = _index(client, base + INDEX_FILE, budget)
    if raw is None:
        weight_map, shards = None, (SINGLE_FILE,)
        missing = _Refused("weights_format_unsupported", WEIGHTS_FORMAT_UNSUPPORTED)
    else:
        budget -= len(raw)
        weight_map, missing = _weight_map(raw), None
        shards = tuple(sorted(set(weight_map.values())))
    tensors = []
    for shard in shards:
        found, used = _shard_tensors(client, base + quote(shard, safe="/"), shard, budget, missing)
        budget -= used
        if weight_map is not None and {tensor.name for tensor in found} != {
                name for name, file in weight_map.items() if file == shard}:
            raise _Refused("index_invalid", INDEX_INVALID)
        tensors.extend(found)
    return shards, tuple(tensors)


# -- evidence ------------------------------------------------------------------------------------

def target_evidence(root: HFRootRef, tensors, config=None) -> ModelTargetEvidence:
    """The rule ``verify_inspected_targets`` applies to full-model inventories, or a fixed refusal when the linear
    modules are not plain 2D floating-point weights. ``config`` (the root's config.json) names the checkpoint
    tensors transformers never builds and loads (``unloaded_modules``: Gemma 4's KV-shared layers)."""
    unsupported = _Refused("architecture_unsupported", ARCHITECTURE_UNSUPPORTED)
    modules = []
    composite = is_composite_inventory(tensor.name for tensor in tensors)
    unloaded = unloaded_modules(config) if config is not None else frozenset()
    for tensor in tensors:
        if composite and not tensor.name.startswith((LANGUAGE_MODEL_PREFIX, "lm_head.")):
            continue
        if tensor.name.rsplit(".", 1)[0] in unloaded:
            continue
        kernel = composite and len(tensor.shape) == 3 and tensor.name.endswith(".linear_attn.conv1d.weight")
        if (not tensor.name.startswith(CAUSAL_LM_PREFIXES) or (len(tensor.shape) >= 3 and not kernel)
                or "experts" in tensor.name.split(".") or tensor.name.endswith(QUANTIZED_SUFFIXES)):
            raise unsupported
        if tensor.name.endswith(".weight") and len(tensor.shape) == 2 and not _not_a_target(tensor.name):
            if tensor.dtype not in FLOAT_DTYPES or 0 in tensor.shape:
                raise unsupported
            modules.append((tensor.name[:-len(".weight")], tensor.shape))
    if not modules:                  # a valid checkpoint without one target weight: nothing to adapt
        raise unsupported
    try:
        return ModelTargetEvidence(base=root, modules=tuple(
            TargetModuleEvidence(name=name, input_dimension=shape[1], output_dimension=shape[0])
            for name, shape in sorted(modules)))
    except ValidationError:
        raise _Refused("header_invalid", HEADER_INVALID) from None


def _unsafe_repository(repo_id: str) -> bool:
    """Names Windows would alter, refused as ``model_root_snapshot`` refuses them."""
    return any(part != part.rstrip(". ") or part.split(".")[0].casefold() in _DEVICE_NAMES
               for part in repo_id.split("/"))


def _indexed_root(context: WorkflowContext, request: RootTargets):
    found = [entry for entry in model_roots(context.state_dir)
             if (entry.root.repo_id, entry.root.revision) == (request.repo_id, request.revision)]
    if not found:
        raise _Refused("model_root_missing", MODEL_ROOT_MISSING.format(root=f"{request.repo_id}@{request.revision}"))
    if len(found) > 1:
        raise _Refused("model_root_invalid", MODEL_ROOT_INVALID)
    return found[0]


def _root_config(context: WorkflowContext, root: HFRootRef) -> dict:
    """The snapshotted config.json of an indexed root, bound by its hash and checked for ``unloaded_modules``."""
    refused = _Refused("model_root_invalid", MODEL_ROOT_INVALID)
    owner, name = root.repo_id.split("/")
    path = root_snapshots_dir(context.state_dir) / owner / name / root.revision / "config.json"
    try:
        with _open_regular(path) as stream:
            raw = stream.read(MAX_CONFIG_BYTES + 1)
        if len(raw) > MAX_CONFIG_BYTES or digest(raw) != root.config_sha256:
            raise refused
        config = _json(raw)
        unloaded_modules(config)
    except (OSError, ValueError):
        raise refused from None
    if not isinstance(config, dict):
        raise refused
    return config


def _stored(context: WorkflowContext, root: HFRootRef) -> ModelTargetEvidence | None:
    try:
        return load_root_targets(context.state_dir, root)
    except ValueError:
        raise _Refused("root_targets_invalid", ROOT_TARGETS_INVALID) from None


@workflow_action("model_root_targets", RootTargets)
def model_root_targets(context: WorkflowContext, request: RootTargets) -> WorkflowResult:
    """An explicit, user-approved header-only read of one indexed HF root (D14, D15, Q-A): never weights."""
    if request.download_approved is not True:
        return invalid(issue("approval_required", TARGETS_APPROVAL_REQUIRED, "/download_approved"))
    if _unsafe_repository(request.repo_id):
        return invalid(issue("repository_unsupported", REPOSITORY_UNSUPPORTED, "/repo_id"))
    try:   # local checks first: nothing is requested for a root that cannot get evidence
        entry = _indexed_root(context, request)
        if entry.profile.architecture not in HEADER_TARGET_ARCHITECTURES:
            raise _Refused("architecture_unsupported", ARCHITECTURE_UNSUPPORTED)
        config = _root_config(context, entry.root) if entry.profile.architecture in COMPOSITE_MODEL_TYPES else None
        existing = _stored(context, entry.root)
    except _Refused as exc:
        return invalid(issue(exc.code, str(exc), "/repo_id"))
    client = _client(context.secrets.get("HF_TOKEN") or None)
    try:
        shards, tensors = _headers(client, request)
        evidence = target_evidence(entry.root, tensors, config)
    except _Refused as exc:
        return invalid(issue(exc.code, str(exc), "/repo_id"))
    except httpx.HTTPError:
        return invalid(issue("targets_failed", HF_CONNECTION, "/repo_id"))
    except (ValueError, OSError, TypeError, AttributeError, KeyError) as exc:   # unexpected: the type name only
        return invalid(issue("targets_failed", type(exc).__name__, "/repo_id"))
    finally:
        client.close()
    if existing is None:
        try:
            save_root_targets(context.state_dir, evidence)
        except ValueError:             # evidence of this root was stored meanwhile: compare with it
            try:
                existing = _stored(context, entry.root)
            except _Refused as exc:
                return invalid(issue(exc.code, str(exc), "/repo_id"))
            if existing is None:
                return invalid(issue("root_targets_invalid", ROOT_TARGETS_INVALID, "/repo_id"))
        except OSError as exc:
            return invalid(issue("targets_failed", type(exc).__name__, "/repo_id"))
    if existing is not None and existing != evidence:
        return conflict(issue("version_exists", TARGETS_CHANGED, "/repo_id"))
    return ok({"root": entry.root.model_dump(mode="json"), "shards": list(shards),
               "module_count": len(evidence.modules),
               "targets": sorted({module.name.rsplit(".", 1)[-1] for module in evidence.modules}),
               "evidence_sha256": digest(canonical(evidence.model_dump(mode="json")))})
