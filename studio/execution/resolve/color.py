"""ColorGroup Recipe execution through ResolveAdapter only."""
from __future__ import annotations

import shutil
from pathlib import Path

from studio.execution.recipes import RecipeRegistry

from .adapter import ResolveAdapter, ResolveOperationError

RESOLVE_LUT_ROOT = Path(
    "/Library/Application Support/Blackmagic Design/DaVinci Resolve/LUT"
)


def apply_color_recipe(
    adapter: ResolveAdapter,
    registry: RecipeRegistry,
    *,
    recipe_id: str,
    items: list,
) -> None:
    recipe = registry.get(recipe_id)
    if recipe is None:
        raise ResolveOperationError(f"Color Recipe 未注册: {recipe_id}")
    # Resolve 21 exposes ApplyGradeFromDRX on the node graph but rejects DRX
    # files exported from the Gallery in this context. The verified production
    # path is the companion 3D LUT through ColorGroup.PostClip.SetLUT.
    drx = registry.artifact_path(recipe_id)
    lut = drx.with_suffix(".cube")
    if not lut.is_file():
        raise ResolveOperationError(
            f"Color Recipe 缺少已验证的 companion LUT: {lut}"
        )
    registered = RESOLVE_LUT_ROOT / "AES" / lut.name
    registered.parent.mkdir(parents=True, exist_ok=True)
    if not registered.is_file() or registered.read_bytes() != lut.read_bytes():
        shutil.copy2(lut, registered)
    adapter.refresh_lut_list()
    adapter.apply_group_lut(
        items,
        group_name=f"aes:{recipe.key}",
        lut_path=lut,
        registered_path=f"AES/{lut.name}",
    )


__all__ = ["RESOLVE_LUT_ROOT", "apply_color_recipe"]
