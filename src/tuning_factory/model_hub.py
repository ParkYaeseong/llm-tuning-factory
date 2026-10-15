"""Hugging Face metadata discovery and immutable, architecture-bound model profiles."""
import os
import re
import tempfile
from pathlib import Path

import httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .registry import canonical, digest, immutable_write

ALL_STAGES = ["pt", "sft", "rm", "ppo", "dpo", "kto"]
ALL_METHODS = ["lora", "qlora", "full", "freeze", "oft"]
ARCHITECTURES = {
    "qwen2": ("qwen", ALL_STAGES, ALL_METHODS),
    "qwen3": ("qwen3_nothink", [x for x in ALL_STAGES if x != "ppo"], ALL_METHODS),
    "qwen3_moe": ("qwen3_nothink", [x for x in ALL_STAGES if x != "ppo"], ALL_METHODS),
    "qwen3_next": ("qwen3_nothink", [x for x in ALL_STAGES if x != "ppo"], ALL_METHODS),
    "qwen3_5": ("qwen3_5_nothink", ["sft"], ["lora", "qlora", "full", "freeze"]),
    "qwen3_5_moe": ("qwen3_5_nothink", ["sft"], ["lora", "qlora", "full", "freeze"]),
    "llama": ("llama3", ALL_STAGES, ALL_METHODS),
    "mistral": ("mistral", ALL_STAGES, ALL_METHODS),
    "gemma2": ("gemma", ALL_STAGES, ALL_METHODS),
    "gemma3_text": ("gemma3", ALL_STAGES, ALL_METHODS),
    "gemma3": ("gemma3", ["sft"], ["lora", "qlora", "full", "freeze"]),
    "gemma4": ("gemma4", ["sft"], ["lora", "qlora", "full", "freeze"]),
    "gemma4_text": ("gemma4", ["sft"], ["lora", "qlora", "full", "freeze"]),
    "deepseek_v3": ("deepseek3", [x for x in ALL_STAGES if x != "ppo"], ALL_METHODS),
    "phi3": ("phi", ALL_STAGES, ALL_METHODS),
}


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

    @model_validator(mode="after")
    def reviewed_architecture(self):
        repository_id(self.repo_id)
        if self.architecture not in ARCHITECTURES:
            raise ValueError("unsupported model architecture; engine profile update required")
        templates = {ARCHITECTURES[self.architecture][0]}
        if self.architecture in {"qwen2", "llama"}:
            templates.add("deepseek3")
        if self.architecture in {"qwen3_5", "qwen3_5_moe"}:
            templates.update({"qwen3_5", "qwen3_6"})
        if self.template not in templates:
            raise ValueError("model architecture/template combination is unsupported")
        return self


def imported_model(profile):
    profile = profile if isinstance(profile, ModelProfile) else ModelProfile.model_validate(profile)
    _, stages, methods = ARCHITECTURES[profile.architecture]
    return {"id": profile.repo_id, "template": profile.template, "architecture": profile.architecture,
            "max_length": profile.max_length, "gated": profile.gated, "gpu_verified": False,
            "verification_scope": "HF metadata checked; this model/revision has not been GPU verified",
            "supported_stages": list(stages), "methods": list(methods), "source": "huggingface",
            "registration": profile.model_dump(), "text_only": True}


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
        text = metadata.get("text_config") or metadata
        result = {"repo_id": model, "revision": pinned, "architecture": architecture,
                  "supported": not reasons, "reasons": reasons, "profile": None,
                  "model_revision": pinned, "tokenizer_revision": pinned, "metadata_verified": True,
                  "max_length": min(32768, text.get("max_position_embeddings") or 32768),
                  "hidden_layers": text.get("num_hidden_layers"),
                  "recommended_precision": text.get("torch_dtype", text.get("dtype"))}
        if reasons:
            return result
        template = ARCHITECTURES[architecture][0]
        if architecture in {"qwen3_5", "qwen3_5_moe"} and "qwen3.6" in model.lower():
            template = "qwen3_6"
        tokenizer = self.get(f"https://huggingface.co/{model}/resolve/{pinned}/tokenizer_config.json", optional=True)
        chat = (tokenizer.json().get("chat_template") or "") if tokenizer else ""
        if architecture in {"qwen2", "llama"} and isinstance(chat, str) and "<｜User｜>" in chat:
            template = "deepseek3"
        profile = ModelProfile(repo_id=model, revision=pinned, config_sha256=digest(response.content),
                               architecture=architecture, template=template, max_length=result["max_length"],
                               context_length=text.get("max_position_embeddings") or 32768,
                               hidden_layers=result["hidden_layers"], recommended_precision=result["recommended_precision"],
                               gated=bool(info.get("gated")))
        return {**result, "template": template, "profile": profile.model_dump(),
                "supported_stages": imported_model(profile)["supported_stages"],
                "note": "Metadata only; model weights download at approved training. GPU verification pending."}
