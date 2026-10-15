"""Strict public configuration; no backend-specific arbitrary YAML injection."""
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .model_hub import ModelProfile


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


Identifier = str


class Hyperparameters(StrictModel):
    precision: Literal["fp16", "bf16"] = "bf16"
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
    save_total_limit: int = Field(default=2, ge=1, le=20)


class FinetuningOptions(StrictModel):
    lora_alpha: int | None = Field(default=None, ge=1, le=4096)
    lora_dropout: float = Field(default=0, ge=0, lt=1)
    use_rslora: bool = False
    use_dora: bool = False
    loraplus_lr_ratio: float | None = Field(default=None, gt=0, le=1000)
    loraplus_lr_embedding: float = Field(default=1e-6, gt=0, le=1)
    pissa_init: bool = False
    pissa_iter: int = Field(default=16, ge=-1, le=256)
    pissa_convert: bool = False
    freeze_trainable_layers: int = Field(default=2, ge=-128, le=128)
    freeze_trainable_modules: Literal["all", "self_attn", "mlp", "self_attn,mlp"] = "all"
    freeze_extra_modules: Literal["embed_tokens", "lm_head", "norm", "embed_tokens,lm_head"] | None = None
    oft_rank: int = Field(default=0, ge=0, le=256)
    oft_block_size: int = Field(default=32, ge=0, le=4096)
    oft_target: Literal["all", "q_proj,v_proj"] = "all"
    module_dropout: float = Field(default=0, ge=0, lt=1)


class StageOptions(StrictModel):
    pref_loss: Literal["sigmoid", "hinge", "ipo", "kto_pair", "orpo", "simpo"] = "sigmoid"
    pref_beta: float = Field(default=0.1, gt=0, le=10)
    pref_ftx: float = Field(default=0, ge=0, le=100)
    pref_bco_weight: float = Field(default=0, ge=0, le=100)
    dpo_label_smoothing: float = Field(default=0, ge=0, le=0.5)
    kto_chosen_weight: float = Field(default=1, gt=0, le=100)
    kto_rejected_weight: float = Field(default=1, gt=0, le=100)
    simpo_gamma: float = Field(default=0.5, ge=0, le=100)
    ppo_buffer_size: int = Field(default=1, ge=1, le=128)
    ppo_epochs: int = Field(default=4, ge=1, le=100)
    ppo_score_norm: bool = False
    ppo_target: float = Field(default=6, gt=0, le=100)
    ppo_whiten_rewards: bool = False
    reward_model: str | None = Field(default=None, pattern=r"^/[a-zA-Z0-9_./-]+$")
    reward_model_type: Literal["lora", "full"] = "lora"
    reward_model_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class EvaluationProfile(StrictModel):
    kind: Literal["pipeline", "classification", "extraction", "json", "kbf_extraction", "reward"] = "pipeline"
    thresholds: dict[str, float] = Field(default_factory=dict)
    dataset_id: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9_-]+$")
    dataset_version: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9_.-]+$")
    max_samples: int = Field(default=32, ge=1, le=1000)
    max_new_tokens: int = Field(default=256, ge=1, le=2048)

    @model_validator(mode="after")
    def paired_dataset(self):
        if bool(self.dataset_id) != bool(self.dataset_version):
            raise ValueError("evaluation dataset ID and version must be supplied together")
        if self.dataset_id and self.kind == "pipeline":
            raise ValueError("validation data requires a task evaluation kind")
        if self.kind == "reward" and (set(self.thresholds) - {"eval_accuracy"}
                                      or any(not 0 <= value <= 1 for value in self.thresholds.values())):
            raise ValueError("reward thresholds only support minimum eval_accuracy between 0 and 1")
        return self


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
    model_profile: ModelProfile | None = None
    model_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    tokenizer_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    training_backend: Literal["llamafactory"] = "llamafactory"
    training_stage: Literal["pt", "sft", "rm", "ppo", "dpo", "kto"] = "sft"
    training_method: Literal["lora", "qlora", "full", "freeze", "oft"] = "lora"
    quantization_bit: Literal[4, 8] = 4
    template: str = "qwen"
    finetuning_options: FinetuningOptions = Field(default_factory=FinetuningOptions)
    stage_options: StageOptions = Field(default_factory=StageOptions)
    hyperparameters: Hyperparameters = Field(default_factory=Hyperparameters)
    evaluation_profile: EvaluationProfile = Field(default_factory=EvaluationProfile)
    resource_profile: ResourceProfile = Field(default_factory=ResourceProfile)
    artifact_destination: Literal["control-plane"] = "control-plane"

    @model_validator(mode="after")
    def supported(self):
        from .capabilities import compatibility
        result = compatibility(self.model_dump())
        if not result["valid"]:
            raise ValueError("; ".join(result["errors"]))
        if self.model_revision != self.tokenizer_revision:
            raise ValueError("this backend requires tokenizer_revision == model_revision")
        if self.model_profile and (self.model_profile.repo_id != self.base_model or
                                   self.model_profile.revision != self.model_revision):
            raise ValueError("registered model identity/revision differs from the pinned configuration; inspect it again")
        if self.dataset_version in {".", ".."}:
            raise ValueError("invalid dataset version")
        return self


def load_config(path: str | Path) -> AIConfig:
    return AIConfig.model_validate(yaml.safe_load(Path(path).read_text(encoding="utf-8")))
