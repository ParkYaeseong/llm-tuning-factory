"""The recipe editor's defaults stay equal to RecipeSpec's.

`desktop/tests/recipe-defaults.json` pins both sides: this test compares it with the Python model, and
`desktop/tests/recipes.spec.ts` compares it with the desktop's `recipeFromTemplate({})` (Task 10.2 review, M6).
Regenerate it from `RecipeSpec(recipe_id="defaults", name="defaults").model_dump(mode="json")` when a default changes.
"""

import json
from pathlib import Path

from tuning_factory.recipes import RecipeSpec

FIXTURE = Path(__file__).resolve().parents[1] / "desktop" / "tests" / "recipe-defaults.json"


def test_desktop_recipe_defaults_fixture_equals_the_python_model():
    expected = RecipeSpec(recipe_id="defaults", name="defaults").model_dump(mode="json")
    assert json.loads(FIXTURE.read_text(encoding="utf-8")) == expected


def test_desktop_recipe_defaults_fixture_has_no_data_resource_or_reward_binding():
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert not {"reward_model", "reward_model_type", "reward_model_sha256"} & set(fixture["stage_options"])
    assert not {"dataset_id", "dataset_version"} & set(fixture["evaluation_defaults"])
    assert not {"ai_id", "workflow_id", "base_model", "resource_profile"} & set(fixture)
