from unittest.mock import Mock

from studio.execution.recipes import Recipe, RecipeRegistry
from studio.execution.resolve import color


def test_color_recipe_registers_companion_lut_and_uses_verified_group_path(
    tmp_path, monkeypatch
):
    root = tmp_path / "repo"
    folder = root / "recipes" / "color" / "clean"
    folder.mkdir(parents=True)
    (folder / "clean.drx").write_text("<xml/>")
    (folder / "clean.cube").write_text("LUT_3D_SIZE 2")
    registry = RecipeRegistry(
        [
            Recipe(
                id="clean",
                version="1.0.0",
                kind="color",
                engine="resolve_colorgroup",
                capability="color_recipe",
                verified=True,
                artifact="recipes/color/clean/clean.drx",
            )
        ],
        root=root,
    )
    monkeypatch.setattr(color, "RESOLVE_LUT_ROOT", tmp_path / "resolve-lut")
    adapter = Mock()
    color.apply_color_recipe(
        adapter, registry, recipe_id="clean", items=["clip"]
    )
    registered = tmp_path / "resolve-lut" / "AES" / "clean.cube"
    assert registered.read_text() == "LUT_3D_SIZE 2"
    adapter.refresh_lut_list.assert_called_once_with()
    adapter.apply_group_lut.assert_called_once_with(
        ["clip"],
        group_name="aes:clean@1.0.0",
        lut_path=folder / "clean.cube",
        registered_path="AES/clean.cube",
    )
