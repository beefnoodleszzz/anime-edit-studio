from pathlib import Path

from studio.execution.recipes import ParameterRule, Recipe, RecipeRegistry


def recipe(**kw):
    values = {
        "id": "impact",
        "version": "1",
        "kind": "effect",
        "engine": "fusion",
        "capability": "add_fusion_comp",
        "parameters": {
            "strength": ParameterRule(type="float", min=0, max=1, required=True)
        },
    }
    values.update(kw)
    return Recipe(**values)


def test_unregistered_recipe_is_rejected():
    issues = RecipeRegistry([]).validate("missing", {}, expected_kind="effect")
    assert [item.code for item in issues] == ["RECIPE_NOT_REGISTERED"]


def test_parameter_schema_reports_all_problems():
    registry = RecipeRegistry([recipe()])
    issues = registry.validate(
        "impact", {"strength": 2.0, "bogus": 1}, expected_kind="effect"
    )
    codes = {item.code for item in issues}
    assert {"RECIPE_NOT_VERIFIED", "RECIPE_UNKNOWN_PARAM", "RECIPE_PARAM_RANGE"} <= codes


def test_verified_requires_all_acceptance_artifacts(tmp_path: Path):
    item = recipe(
        verified=True,
        artifact="recipe/impact.comp",
        preview="recipe/preview.mp4",
        acceptance="recipe/ACCEPTANCE.md",
    )
    registry = RecipeRegistry([item], root=tmp_path)
    codes = {
        issue.code
        for issue in registry.validate("impact", {"strength": 0.5}, expected_kind="effect")
    }
    assert "RECIPE_ACCEPTANCE_INCOMPLETE" in codes
    folder = tmp_path / "recipe"
    folder.mkdir()
    for name in ("impact.comp", "preview.mp4", "ACCEPTANCE.md"):
        (folder / name).write_bytes(b"evidence")
    assert registry.validate("impact", {"strength": 0.5}, expected_kind="effect") == []


def test_bool_does_not_pass_numeric_parameter():
    registry = RecipeRegistry([recipe()])
    codes = {
        issue.code
        for issue in registry.validate("impact", {"strength": True}, expected_kind="effect")
    }
    assert "RECIPE_PARAM_TYPE" in codes
