"""Hugging Face metadata discovery and immutable, architecture-bound model profiles."""
import os
import re
import tempfile
from pathlib import Path

import httpx
from pydantic import BaseModel, ConfigDict, Field, model_serializer, model_validator

from .registry import canonical, digest, immutable_write

ALL_STAGES = ["pt", "sft", "rm", "ppo", "dpo", "kto"]
ALL_METHODS = ["lora", "qlora", "full", "freeze", "oft"]
# Vision(/audio) + text models trained on text only: LLaMA-Factory loads them with AutoModelForImageTextToText and the
# app keeps their vision/audio towers and projectors frozen (training.py, target_inspection.py).
COMPOSITE_ARCHITECTURES = frozenset({"qwen3_5", "qwen3_5_moe", "gemma4"})
# Reward modeling waits for GPU proof of the value head; the PPO runtime (transformers 4.52.4) lacks these models.
COMPOSITE_STAGES = [x for x in ALL_STAGES if x not in {"rm", "ppo"}]
COMPOSITE_METHODS = ["lora", "qlora", "full", "freeze"]
ARCHITECTURES = {
    "qwen2": ("qwen", ALL_STAGES, ALL_METHODS),
    "qwen3": ("qwen3_nothink", [x for x in ALL_STAGES if x != "ppo"], ALL_METHODS),
    "qwen3_moe": ("qwen3_nothink", [x for x in ALL_STAGES if x != "ppo"], ALL_METHODS),
    "qwen3_next": ("qwen3_nothink", [x for x in ALL_STAGES if x != "ppo"], ALL_METHODS),
    "qwen3_5": ("qwen3_5", COMPOSITE_STAGES, COMPOSITE_METHODS),
    "qwen3_5_moe": ("qwen3_5", COMPOSITE_STAGES, COMPOSITE_METHODS),
    "llama": ("llama3", ALL_STAGES, ALL_METHODS),
    "mistral": ("mistral", ALL_STAGES, ALL_METHODS),
    "gemma2": ("gemma", ALL_STAGES, ALL_METHODS),
    "gemma3_text": ("gemma3", ALL_STAGES, ALL_METHODS),
    "gemma3": ("gemma3", ["sft"], ["lora", "qlora", "full", "freeze"]),
    "gemma4": ("gemma4", COMPOSITE_STAGES, COMPOSITE_METHODS),
    "gemma4_text": ("gemma4", ["sft"], ["lora", "qlora", "full", "freeze"]),
    "deepseek_v3": ("deepseek3", [x for x in ALL_STAGES if x != "ppo"], ALL_METHODS),
    "phi3": ("phi", ALL_STAGES, ALL_METHODS),
}


def _count(value, high, low=1):
    """A config or API count within [low, high]; anything else (bool, text, float, out of range) is unknown (None),
    so one malformed optional fact never blocks an import."""
    return value if isinstance(value, int) and not isinstance(value, bool) and low <= value <= high else None


def _tied(metadata, text, architecture):
    """tie_word_embeddings: the top level, then text_config (booleans only), then the architecture's transformers
    default when it is known to be tied (Gemma configs default to True); otherwise unknown (None), so the tied
    lm_head refusal (capabilities.FREEZE_TIED_LM_HEAD) never refuses a model that may be untied."""
    for level in (metadata, text):
        if isinstance(level.get("tie_word_embeddings"), bool):
            return level["tie_word_embeddings"]
    return True if str(architecture).startswith("gemma") else None


def recommended_precision(metadata):
    """The checkpoint dtype of a config.json: text_config first (composite models nest the text model), then the top
    level; torch_dtype before its newer name dtype; text values only."""
    text = metadata.get("text_config")
    for level in (text if isinstance(text, dict) else {}, metadata):
        for key in ("torch_dtype", "dtype"):
            if isinstance(level.get(key), str):
                return level[key]
    return None


def repository_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}/[A-Za-z0-9][A-Za-z0-9_.-]{0,95}", value):
        raise ValueError("Hugging Face repository must be an exact publisher/model ID")
    return value


class ModelProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    repo_id: str
    revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    config_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    architecture: str
    template: str
    max_length: int = Field(default=32768, ge=64, le=32768)
    context_length: int = Field(default=32768, ge=64, le=10000000)
    hidden_layers: int | None = Field(default=None, ge=1, le=10000)
    recommended_precision: str | None = None
    gated: bool = False
    parameters_total: int | None = Field(default=None, ge=1)   # safetensors total: vision/audio included
    hidden_size: int | None = Field(default=None, ge=1, le=1_000_000)
    moe: bool | None = None
    tie_word_embeddings: bool | None = None

    @model_validator(mode="after")
    def reviewed_architecture(self):
        repository_id(self.repo_id)
        if self.architecture not in ARCHITECTURES:
            raise ValueError("unsupported model architecture; engine profile update required")
        templates = {ARCHITECTURES[self.architecture][0]}
        if self.architecture in {"qwen2", "llama"}:
            templates.add("deepseek3")
        if self.architecture in {"qwen3_5", "qwen3_5_moe"}:
            templates.update({"qwen3_5", "qwen3_5_nothink", "qwen3_6"})
        if self.architecture == "gemma4":
            templates.add("gemma4n")
        if self.template not in templates:
            raise ValueError("model architecture/template combination is unsupported")
        return self

    @model_serializer(mode="wrap")
    def _omit_absent_size_facts(self, handler):
        # Profiles registered before these facts keep their exact bytes and digests (model-profiles/<digest>.json,
        # graph plans): absent facts are omitted instead of dumped as null.
        data = handler(self)
        for key in ("parameters_total", "hidden_size", "moe", "tie_word_embeddings"):
            if data.get(key) is None:
                data.pop(key, None)
        return data


def imported_model(profile):
    profile = profile if isinstance(profile, ModelProfile) else ModelProfile.model_validate(profile)
    _, stages, methods = ARCHITECTURES[profile.architecture]
    sizes = {}
    if profile.parameters_total and profile.hidden_size and profile.hidden_layers:
        sizes = {"parameters_total": profile.parameters_total, "parameters_b": round(profile.parameters_total / 1e9, 2),
                 "hidden_size": profile.hidden_size, "num_layers": profile.hidden_layers}
    sizes.update({key: value for key in ("moe", "tie_word_embeddings")
                  if (value := getattr(profile, key)) is not None})
    return {"id": profile.repo_id, "template": profile.template, "architecture": profile.architecture,
            "max_length": profile.max_length, "gated": profile.gated, "gpu_verified": False,
            "verification_scope": "HF metadata checked; this model/revision has not been GPU verified",
            "supported_stages": list(stages), "methods": list(methods), "source": "huggingface",
            "registration": profile.model_dump(), "text_only": True, **sizes}


def register_model(root, profile):
    profile = ModelProfile.model_validate(profile)
    body = canonical(profile.model_dump())
    root = Path(root)
    immutable_write(root / "model-profiles" / (digest(body) + ".json"), body)
    selected = root / "model-selections"
    selected.mkdir(exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=selected, delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(body)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        temporary.replace(selected / (digest(profile.repo_id.encode()) + ".json"))
    finally:
        temporary.unlink(missing_ok=True)
    return {"model": imported_model(profile), "profile": profile.model_dump()}


def registered_models(root):
    return [imported_model(ModelProfile.model_validate_json(path.read_bytes()))
            for path in sorted((Path(root) / "model-selections").glob("*.json"))]


class ModelHub:
    def __init__(self, token=None, client=None):
        self.owned = client is None
        self.client = client or httpx.Client(timeout=20, follow_redirects=True,
                                             headers={"Authorization": "Bearer " + token} if token else {})

    def __enter__(self):
        return self

    def __exit__(self, *args):
        if self.owned:
            self.client.close()

    def get(self, url, optional=False, **kwargs):
        try:
            response = self.client.get(url, **kwargs)
        except httpx.HTTPError:
            raise ValueError("Hugging Face metadata connection failed; check connectivity") from None
        if optional and response.status_code == 404:
            return None
        if response.status_code != 200:
            raise ValueError(f"Hugging Face metadata request failed (HTTP {response.status_code}); check model access")
        if len(response.content) > 4 * 1024 * 1024:
            raise ValueError("Hugging Face metadata exceeds the inspection size limit")
        return response

    def search(self, query, sort="downloads"):
        if not isinstance(query, str) or not 2 <= len(query.strip()) <= 120 or sort not in {"downloads", "lastModified"}:
            raise ValueError("Enter a model search of 2–120 characters and a supported sort")
        response = self.get("https://huggingface.co/api/models", params={
            "search": query.strip(), "limit": 20, "sort": sort, "direction": -1})
        rows = response.json()
        if not isinstance(rows, list):
            raise ValueError("Hugging Face model search returned an unexpected response")
        return {"models": [{"id": row["id"], "downloads": row.get("downloads", 0), "likes": row.get("likes", 0),
                            "pipeline_tag": row.get("pipeline_tag", "unknown"), "gated": bool(row.get("gated")),
                            "last_modified": row.get("lastModified")} for row in rows[:20]
                           if isinstance(row, dict) and isinstance(row.get("id"), str)]}

    def inspect(self, model, revision=None):
        model = repository_id(model)
        if revision is not None and not re.fullmatch("[0-9a-f]{40}", revision):
            raise ValueError("model revision must be an immutable 40-character commit")
        info = self.get(f"https://huggingface.co/api/models/{model}/revision/{revision or 'main'}").json()
        pinned = info.get("sha", "")
        if not re.fullmatch("[0-9a-f]{40}", pinned) or (revision and pinned != revision):
            raise ValueError("Hugging Face revision lookup did not return the requested immutable commit")
        response = self.get(f"https://huggingface.co/{model}/resolve/{pinned}/config.json", optional=True)
        if response is None:
            return {"repo_id": model, "revision": pinned, "supported": False, "profile": None,
                    "reasons": ["Training config.json is unavailable; GGUF/adapters alone are not base training models"]}
        metadata = response.json()
        architecture = metadata.get("model_type", "unknown")
        reasons = []
        if architecture not in ARCHITECTURES:
            reasons.append(f"Unsupported architecture {architecture}; an engine/profile update is required")
        if metadata.get("auto_map"):
            reasons.append("Models requiring custom remote code are not automatically enabled")
        if metadata.get("quantization_config"):
            reasons.append("Pre-quantized inference checkpoints require a separate training adapter; choose original weights")
        # Machine-readable lower-case reasons are useful to clients without exposing HTTP request details.
        reasons = [reason.replace("Unsupported", "unsupported").replace("Pre-quantized", "pre-quantized") for reason in reasons]
        text = metadata.get("text_config") if isinstance(metadata.get("text_config"), dict) else metadata
        summary = info.get("safetensors")
        facts = {"parameters_total": _count(summary.get("total"), 10 ** 15) if isinstance(summary, dict) else None,
                 "hidden_size": _count(text.get("hidden_size"), 1_000_000),
                 "moe": _count(text.get("num_experts"), 1_000_000) is not None,
                 "tie_word_embeddings": _tied(metadata, text, architecture)}
        positions = _count(text.get("max_position_embeddings"), 10_000_000, low=64) or 32768
        result = {"repo_id": model, "revision": pinned, "architecture": architecture,
                  "supported": not reasons, "reasons": reasons, "profile": None,
                  "model_revision": pinned, "tokenizer_revision": pinned, "metadata_verified": True,
                  "max_length": min(32768, positions), "hidden_layers": _count(text.get("num_hidden_layers"), 10000),
                  "recommended_precision": recommended_precision(metadata), **facts}
        if reasons:
            return result
        template = ARCHITECTURES[architecture][0]
        if architecture in {"qwen3_5", "qwen3_5_moe"} and "qwen3.6" in model.lower():
            template = "qwen3_6"
        if architecture == "gemma4" and _count(text.get("hidden_size_per_layer_input"), 1_000_000):
            template = "gemma4n"   # Gemma 4 E2B/E4B (per-layer inputs 256); 26B-A4B and 31B have 0
        tokenizer = self.get(f"https://huggingface.co/{model}/resolve/{pinned}/tokenizer_config.json", optional=True)
        chat = (tokenizer.json().get("chat_template") or "") if tokenizer else ""
        if architecture in {"qwen2", "llama"} and isinstance(chat, str) and "<｜User｜>" in chat:
            template = "deepseek3"
        profile = ModelProfile(repo_id=model, revision=pinned, config_sha256=digest(response.content),
                               architecture=architecture, template=template, max_length=result["max_length"],
                               context_length=positions,
                               hidden_layers=result["hidden_layers"], recommended_precision=result["recommended_precision"],
                               gated=bool(info.get("gated")), **facts)
        return {**result, "template": template, "profile": profile.model_dump(),
                "supported_stages": imported_model(profile)["supported_stages"],
                "note": "Metadata only; model weights download at approved training. GPU verification pending."}
