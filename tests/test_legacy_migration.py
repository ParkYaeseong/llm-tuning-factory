from pathlib import Path

from tuning_factory.config import AIConfig, load_config
from tuning_factory.projects import ProjectStore
from tuning_factory.recipes import RecipeStore, recipe_from_config
from tuning_factory.registry import Registry


def test_legacy_profile_blob_and_metadata_bytes_are_unchanged(tmp_path):
    config_path = tmp_path / "legacy.yaml"
    config_path.write_bytes(Path("configs/ai/paper_extraction.yaml").read_bytes())
    source = tmp_path / "source.json"
    source.write_bytes(b'[{"instruction":"x", "output":"y"}]\n')
    registry = Registry(tmp_path / "registry")
    registry.register_dataset("dummy", "1", source)
    config = load_config(config_path)
    registry.register_ai(config)
    paths = [config_path, source, *[p for p in registry.root.rglob("*") if p.is_file()]]
    before = {p: p.read_bytes() for p in paths}

    project = ProjectStore(tmp_path).create("new", "General", "Any purpose", "text")
    recipes = RecipeStore(tmp_path)
    recipes.save(recipe_from_config(config), expected_revision=None)
    assert project.spec.legacy_origin is None
    assert {p: p.read_bytes() for p in paths} == before


def test_ppo_conversion_omits_reward_path_hash_and_type():
    data = load_config("configs/ai/paper_extraction.yaml").model_dump()
    data.update(training_stage="ppo", stage_options={
        "reward_model": "/opt/private/reward", "reward_model_sha256": "a" * 64,
        "reward_model_type": "full", "ppo_epochs": 7,
    })
    config = AIConfig.model_validate(data)
    before = config.model_dump_json()
    recipe = recipe_from_config(config)
    assert recipe.input_kind == "prompt"
    assert recipe.stage_options.ppo_epochs == 7
    assert not any(key.startswith("reward_model") for key in recipe.stage_options.model_dump())
    assert config.model_dump_json() == before

