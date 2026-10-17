import pytest
from pydantic import ValidationError

from tuning_factory.config import load_config
from tuning_factory.recipes import RecipeSpec, RecipeStore, recipe_from_config, recipe_training_options


def test_clone_is_independent_and_revision_history_survives(tmp_path):
    store = RecipeStore(tmp_path)
    original = store.save(RecipeSpec(recipe_id="original", name="원본"), expected_revision=None)
    clone = store.clone("original", name="사본")
    assert clone.spec.recipe_id != "original"
    assert clone.spec.name == "사본"
    options = clone.spec.hyperparameters.model_copy(update={"learning_rate": 0.005})
    edited = store.save(clone.spec.model_copy(update={"hyperparameters": options}), clone.revision_sha256)
    assert store.get("original") == original
    assert store.get(clone.spec.recipe_id).spec.hyperparameters.learning_rate == 0.005
    assert store.get(clone.spec.recipe_id, clone.revision_sha256) == clone
    assert len(store.list()) == 2
    with pytest.raises(ValueError, match="revision conflict"):
        store.save(original.spec, "a" * 64)
    assert edited.revision_sha256 != clone.revision_sha256


@pytest.mark.parametrize("stage,kind", [
    ("pt", "text"), ("sft", "instruction"), ("rm", "preference"),
    ("ppo", "prompt"), ("dpo", "preference"), ("kto", "kto"),
])
def test_stage_input_kind_contract(stage, kind):
    recipe = RecipeSpec(recipe_id="recipe", name="Recipe", training_stage=stage, input_kind=kind)
    assert recipe.input_kind == kind
    with pytest.raises(ValidationError, match="input kind"):
        RecipeSpec(recipe_id="recipe", name="Bad", training_stage=stage,
                   input_kind="text" if kind != "text" else "instruction")


@pytest.mark.parametrize("field,value", [
    ("dataset_id", "private"), ("dataset_version", "1"), ("project_id", "private"),
    ("pod_id", "borrowed"), ("credential", "secret"), ("api_key", "secret"),
    ("artifact_destination", "s3://private"), ("export_allowed", True),
    ("command", "arbitrary"), ("reward_model", "/tmp/reward"),
])
def test_recipe_rejects_execution_and_identity_fields(field, value):
    with pytest.raises(ValidationError):
        RecipeSpec(recipe_id="recipe", name="Recipe", **{field: value})


@pytest.mark.parametrize("field,value", [
    ("reward_model", "/tmp/reward"), ("reward_model_sha256", "a" * 64),
    ("reward_model_type", "lora"), ("command", "arbitrary"),
])
def test_recipe_stage_options_exclude_reward_binding(field, value):
    with pytest.raises(ValidationError):
        RecipeSpec(recipe_id="recipe", name="Recipe", stage_options={field: value})


def test_evaluation_defaults_never_contain_dataset_binding():
    with pytest.raises(ValidationError):
        RecipeSpec(recipe_id="recipe", name="Recipe", evaluation_defaults={"dataset_id": "private"})
    recipe = RecipeSpec(recipe_id="recipe", name="Recipe")
    assert recipe.evaluation_defaults.max_samples == 32
    assert "dataset_id" not in recipe.evaluation_defaults.model_dump()


@pytest.mark.parametrize("updates", [
    {"training_stage": "custom"}, {"training_method": "custom"},
    {"capability_version": "unknown"}, {"training_backend": "shell"},
    {"training_method": "full", "finetuning_options": {"use_dora": True}},
    {"training_method": "freeze", "finetuning_options": {"freeze_trainable_layers": 0}},
    {"training_method": "oft", "training_stage": "ppo", "input_kind": "prompt"},
    {"training_method": "qlora", "finetuning_options": {"pissa_init": True}},
    {"training_stage": "dpo", "input_kind": "preference",
     "stage_options": {"pref_loss": "hinge", "dpo_label_smoothing": 0.1}},
])
def test_recipe_preserves_reviewed_option_bounds(updates):
    with pytest.raises(ValidationError):
        RecipeSpec(recipe_id="recipe", name="Recipe", **updates)


def test_config_conversion_copies_options_without_mutating_config():
    config = load_config("configs/ai/paper_extraction.yaml")
    before = config.model_dump()
    recipe = recipe_from_config(config)
    options = recipe_training_options(recipe)
    assert options["training_stage"] == "sft"
    assert options["hyperparameters"]["max_steps"] == 2
    assert "dataset_id" not in options
    assert "resource_profile" not in options
    assert "reward_model" not in options["stage_options"]
    options["hyperparameters"]["max_steps"] = 999
    assert recipe.hyperparameters.max_steps == 2
    assert config.model_dump() == before

