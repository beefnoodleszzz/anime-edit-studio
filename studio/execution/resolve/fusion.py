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
) -> object:
    recipe = registry.get(ref.recipe)
    if recipe is None:
        raise ResolveOperationError(f"Fusion Recipe 未注册: {ref.recipe}")
    resolved = registry.resolved_params(ref.recipe, ref.params)
    engine_params = {
        recipe.bindings[name]: value
        for name, value in resolved.items()
        if name in recipe.bindings
    }
    return adapter.replace_fusion_comp(
        item,
        registry.artifact_path(ref.recipe),
        comp_name=f"aes:{recipe.key}",
        parameters=engine_params,
    )


def apply_speed_ramp_recipe(
    adapter: ResolveAdapter,
    registry: RecipeRegistry,
    *,
    item,
    duration_frames: int,
    entry_speed: float,
    impact_speed: float,
    exit_speed: float,
    impact_frame: int,
) -> None:
    ref = RecipeRef(
        recipe="speed_ramp_v1",
        params={"start_speed": entry_speed, "end_speed": exit_speed},
    )
    comp = apply_fusion_recipe(adapter, registry, item=item, ref=ref)
    adapter.configure_speed_ramp(
        comp,
        duration_frames=duration_frames,
        entry_speed=entry_speed,
        impact_speed=impact_speed,
        exit_speed=exit_speed,
        impact_frame=impact_frame,
    )


def apply_whip_blur_side(
    adapter: ResolveAdapter,
    registry: RecipeRegistry,
    *,
    item,
    recipe_id: str,
    side: str,
    duration_frames: int,
    transition_frames: int,
    params: dict,
) -> None:
    ref = RecipeRef(recipe=recipe_id, params=params)
    comp = apply_fusion_recipe(adapter, registry, item=item, ref=ref)
    resolved = registry.resolved_params(ref.recipe, ref.params)
    adapter.configure_whip_blur_side(
        comp,
        side=side,
        duration_frames=duration_frames,
        transition_frames=transition_frames,
        length=float(resolved["length"]),
        angle=float(resolved["angle"]),
        settle_scale=float(resolved.get("settle_scale", 0.0)),
    )


__all__ = [
    "apply_fusion_recipe",
    "apply_speed_ramp_recipe",
    "apply_whip_blur_side",
]
