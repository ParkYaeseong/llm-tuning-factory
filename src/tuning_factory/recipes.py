"""Reusable text training options, separate from data and execution bindings."""

import uuid
from copy import deepcopy
from typing import Literal

from pydantic import Field, create_model, model_validator

from .capabilities import STAGES, compatibility
from .config import (
    AIConfig,
    EvaluationProfile,
    FinetuningOptions,
    Hyperparameters,
    StageOptions,
    StrictModel,
)
from .projects import LocalId, Revision, _RevisionStore

_REWARD_FIELDS = {"reward_model", "reward_model_type", "reward_model_sha256"}

# Keep the reviewed option bounds/defaults without exposing reward bindings in
# the public recipe schema (excluded serialization alone would still accept them).
RecipeStageOptions = create_model(
    "RecipeStageOptions", __base__=StrictModel,
    **{name: (field.annotation, deepcopy(field)) for name, field in StageOptions.model_fields.items()
       if name not in _REWARD_FIELDS},
)
EvaluationDefaults = create_model(
    "EvaluationDefaults", __base__=StrictModel,
    **{name: (field.annotation, deepcopy(field)) for name, field in EvaluationProfile.model_fields.items()
       if name not in {"dataset_id", "dataset_version"}},
)


class RecipeSpec(StrictModel):
    schema_version: Literal[1] = 1
    recipe_id: LocalId
    version: LocalId = "1"
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    training_backend: Literal["llamafactory"] = "llamafactory"
    capability_version: Literal["lf-text-v1"] = "lf-text-v1"
    training_stage: Literal["pt", "sft", "rm", "ppo", "dpo", "kto"] = "sft"
    training_method: Literal["lora", "qlora", "full", "freeze", "oft"] = "lora"
    input_kind: Literal["text", "instruction", "preference", "prompt", "kto"] = "instruction"
    quantization_bit: Literal[4, 8] = 4
    hyperparameters: Hyperparameters = Field(default_factory=Hyperparameters)
    finetuning_options: FinetuningOptions = Field(default_factory=FinetuningOptions)
    stage_options: RecipeStageOptions = Field(default_factory=RecipeStageOptions)
    evaluation_defaults: EvaluationDefaults = Field(default_factory=EvaluationDefaults)

    @model_validator(mode="after")
    def supported(self):
        if self.input_kind != STAGES[self.training_stage][1]:
            raise ValueError("input kind does not match training stage")
        options = recipe_training_options(self)
        # Apply existing reviewed method/stage checks with a neutral model. Real
        # model compatibility and the PPO reward binding are graph-time checks.
        # These sentinels exist only for validation and never enter the recipe.
        options.update(base_model="Qwen/Qwen2.5-0.5B-Instruct")
        if self.training_stage == "ppo":
            options["stage_options"].update(reward_model="/graph-binding", reward_model_sha256="0" * 64,
                                            reward_model_type="full")
        result = compatibility(options)
        if not result["valid"]:
            raise ValueError("; ".join(result["errors"]))
        # Preserve reward threshold bounds without needing dataset identities.
        EvaluationProfile.model_validate(self.evaluation_defaults.model_dump())
        return self


def recipe_training_options(recipe: RecipeSpec) -> dict:
    """Return a detached option mapping; execution must add model/data bindings."""
    return {name: deepcopy(getattr(recipe, name)) if name not in {
        "hyperparameters", "finetuning_options", "stage_options"
    } else getattr(recipe, name).model_dump() for name in (
        "training_backend", "training_stage", "training_method", "quantization_bit",
        "hyperparameters", "finetuning_options", "stage_options",
    )}


def recipe_from_config(config: AIConfig) -> RecipeSpec:
    """Copy legacy training options into a fresh, identity-free recipe."""
    data = config.model_dump()
    return RecipeSpec(
        recipe_id=uuid.uuid4().hex, name=config.ai_id, input_kind=STAGES[config.training_stage][1],
        **{name: data[name] for name in (
            "training_backend", "training_stage", "training_method", "quantization_bit",
            "hyperparameters", "finetuning_options",
        )},
        stage_options={name: value for name, value in data["stage_options"].items()
                       if name not in _REWARD_FIELDS},
        evaluation_defaults={name: value for name, value in data["evaluation_profile"].items()
                             if name not in {"dataset_id", "dataset_version"}},
    )


class RecipeStore(_RevisionStore[RecipeSpec]):
    def __init__(self, root):
        super().__init__(root, "recipes", RecipeSpec, "recipe_id")

    def clone(self, id: str, *, name: str | None = None, new_recipe_id: str | None = None,
              version: str = "1", revision: str | None = None) -> Revision[RecipeSpec]:
        """Copy a pinned/current recipe into a new independently editable ID."""
        original = self.get(id, revision).spec
        data = original.model_dump()
        data.update(recipe_id=new_recipe_id or uuid.uuid4().hex,
                    name=name if name is not None else original.name, version=version)
        return self.save(RecipeSpec.model_validate(data), expected_revision=None)
