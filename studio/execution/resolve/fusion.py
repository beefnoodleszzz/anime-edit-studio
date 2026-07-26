"""Fusion Recipe execution through ResolveAdapter only."""
from __future__ import annotations

from studio.editspec.schema import RecipeRef
from studio.execution.recipes import RecipeRegistry

from .adapter import ResolveAdapter, ResolveOperationError


def apply_fusion_recipe(
    adapter: ResolveAdapter,
    registry: RecipeRegistry,
    *,
    item,
    ref: RecipeRef,
) -> None:
    recipe = registry.get(ref.recipe)
    if recipe is None:
        raise ResolveOperationError(f"Fusion Recipe 未注册: {ref.recipe}")
    resolved = registry.resolved_params(ref.recipe, ref.params)
    engine_params = {
        recipe.bindings[name]: value
        for name, value in resolved.items()
        if name in recipe.bindings
    }
    adapter.replace_fusion_comp(
        item,
        registry.artifact_path(ref.recipe),
        comp_name=f"aes:{recipe.key}",
        parameters=engine_params,
    )


__all__ = ["apply_fusion_recipe"]
