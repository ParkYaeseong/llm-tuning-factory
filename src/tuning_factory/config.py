"""Strict public configuration; no backend-specific arbitrary YAML injection."""
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


Identifier = str


class Hyperparameters(StrictModel):
    learning_rate: float = Field(default=0.0001, gt=0, le=1)
    num_train_epochs: float = Field(default=1, gt=0, le=100)
    per_device_train_batch_size: int = Field(default=1, ge=1, le=64)
    gradient_accumulation_steps: int = Field(default=4, ge=1)
    cutoff_len: int = Field(default=1024, ge=64, le=32768)
    lora_rank: int = Field(default=8, ge=1, le=256)
    lora_target: Literal["all", "q_proj,v_proj"] = "all"
    seed: int = 42
    max_steps: int = Field(default=-1, ge=-1)
    save_steps: int = Field(default=50, ge=1)


class EvaluationProfile(StrictModel):
    kind: Literal["pipeline", "classification", "extraction", "json", "kbf_extraction"] = "pipeline"
    thresholds: dict[str, float] = Field(default_factory=dict)


class ResourceProfile(StrictModel):
    gpu_count: int = Field(default=1, ge=1, le=8)
    gpu_type: str = "NVIDIA RTX A4000"
    timeout_seconds: int = Field(default=3600, ge=1, le=604800)
    hourly_cost_usd: float = Field(default=0, ge=0)
    max_cost_usd: float = Field(default=1, gt=0)


class AIConfig(StrictModel):
    ai_id: str = Field(pattern=r"^[a-zA-Z0-9_-]+$")
    workflow_id: str = Field(min_length=1)
    dataset_id: str = Field(pattern=r"^[a-zA-Z0-9_-]+$")
    dataset_version: str = Field(pattern=r"^[a-zA-Z0-9_.-]+$")
    base_model: str
    model_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    tokenizer_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    training_backend: Literal["llamafactory"] = "llamafactory"
    training_method: Literal["lora", "qlora"] = "lora"
    template: str = "qwen"
    hyperparameters: Hyperparameters = Field(default_factory=Hyperparameters)
    evaluation_profile: EvaluationProfile = Field(default_factory=EvaluationProfile)
    resource_profile: ResourceProfile = Field(default_factory=ResourceProfile)
    artifact_destination: Literal["control-plane"] = "control-plane"

    @model_validator(mode="after")
    def supported(self):
        supported_models = {
            "Qwen/Qwen2.5-0.5B-Instruct": "qwen",
            "Qwen/Qwen2.5-1.5B-Instruct": "qwen",
            "Qwen/Qwen2.5-7B-Instruct": "qwen",
            "meta-llama/Llama-3.1-8B-Instruct": "llama3",
            "meta-llama/Llama-3.2-3B-Instruct": "llama3",
        }
        if supported_models.get(self.base_model) != self.template:
            raise ValueError("unsupported model/template combination; extend the tested model catalog first")
        if self.model_revision != self.tokenizer_revision:
            raise ValueError("this backend requires tokenizer_revision == model_revision")
        if self.dataset_version in {".", ".."}:
            raise ValueError("invalid dataset version")
        return self


def load_config(path: str | Path) -> AIConfig:
    return AIConfig.model_validate(yaml.safe_load(Path(path).read_text(encoding="utf-8")))
