"""Deterministic, capability-gated Recipe intent planning.

Creative structure comes from DirectorPlan. This module only maps that
structure to already accepted Recipes while enforcing impact-density budgets.
It never invents Recipe ids or bypasses resolve_capabilities.yaml.
"""
from __future__ import annotations

from collections.abc import Callable

from studio.core.capabilities import is_verified
from studio.creative.director import DirectorPlan
from studio.editspec.schema import EditSpec, RecipeRef, SfxCue
from studio.execution.recipes import RecipeRegistry

RECIPE_PLANNER_VERSION = "recipe-planner-1.1.0"
_MANAGED_EFFECTS = {
    "white_flash_v1", "impact_shake_v1", "eye_focus_v1", "camera_punch_v1",
}
_MANAGED_SOUNDS = {
    "impact_low_v1", "sub_impact_v1", "sword_whoosh_v1", "riser_v1",
}


def _admitted(
    registry: RecipeRegistry,
    recipe_id: str,
    *,
    kind: str,
    capability: str,
    capability_check: Callable[[str], bool],
) -> bool:
    recipe = registry.get(recipe_id)
    return bool(
        recipe
        and recipe.verified
        and recipe.kind == kind
        and capability_check(capability)
        and not registry.validate(recipe_id, {}, expected_kind=kind)
    )


def _spread(values: list, count: int) -> list:
    """Choose stable, evenly distributed values without duplicate indices."""
    if count <= 0 or not values:
        return []
    if count >= len(values):
        return list(values)
    if count == 1:
        return [values[len(values) // 2]]
    indices = [
        round(index * (len(values) - 1) / (count - 1))
        for index in range(count)
    ]
    return [values[index] for index in dict.fromkeys(indices)]


def apply_recipe_plan(
    spec: EditSpec,
    *,
    plan: DirectorPlan,
    registry: RecipeRegistry | None = None,
    capability_check: Callable[[str], bool] = is_verified,
) -> EditSpec:
    """Return a new EditSpec using only admitted Recipes.

    One Fusion Recipe is assigned per TimelineItem because Resolve Fusion comps
    on a clip are versions rather than a guaranteed serial effect stack.
    """
    registry = registry or RecipeRegistry.load()
    clips = [clip.model_copy(deep=True) for clip in spec.clips]
    for clip in clips:
        clip.effects = [
            ref for ref in clip.effects if ref.recipe not in _MANAGED_EFFECTS
        ]
        clip.audio = clip.audio.model_copy(
            update={
                "sfx": [
                    cue for cue in clip.audio.sfx
                    if cue.recipe not in _MANAGED_SOUNDS
                ]
            }
        )
    used_versions: dict[str, str] = {}

    def use(recipe_id: str) -> RecipeRef:
        recipe = registry.get(recipe_id)
        if recipe is None:
            raise ValueError(f"Recipe 不存在: {recipe_id}")
        used_versions[recipe_id] = recipe.version
        return RecipeRef(recipe=recipe_id)

    impact = sorted(
        (clip for clip in clips if clip.role == "impact"),
        key=lambda clip: clip.timeline.in_sec,
    )
    occupied_effects: set[str] = set()
    push_pull = "push_pull" in plan.tone

    # Reference-led tug grammar: the accepted CameraPunch Fusion comp performs
    # a deterministic push-in then pull-out inside every shot. This deliberately
    # replaces sparse impact effects because Resolve exposes Fusion comps as
    # versions, not a guaranteed serial stack.
    if push_pull and _admitted(
        registry, "camera_punch_v1", kind="effect",
        capability="add_fusion_comp", capability_check=capability_check,
    ):
        for clip in clips:
            clip.effects = [use("camera_punch_v1")]
            occupied_effects.add(clip.id)

    if not push_pull and _admitted(
        registry, "white_flash_v1", kind="effect",
        capability="add_fusion_comp", capability_check=capability_check,
    ):
        for clip in _spread(impact, min(plan.impact_budget.flash_max, 2)):
            clip.effects = [use("white_flash_v1")]
            occupied_effects.add(clip.id)

    if not push_pull and _admitted(
        registry, "impact_shake_v1", kind="effect",
        capability="add_fusion_comp", capability_check=capability_check,
    ):
        remaining = [clip for clip in impact if clip.id not in occupied_effects]
        for clip in _spread(remaining, min(plan.impact_budget.shake_max, 3)):
            clip.effects = [use("impact_shake_v1")]
            occupied_effects.add(clip.id)

    # A restrained focal push at the opening is outside the impact budget but
    # still limited to one clip and never stacked with another Fusion comp.
    opening = next((clip for clip in clips if clip.role == "opening"), None)
    if opening and not push_pull and _admitted(
        registry, "eye_focus_v1", kind="effect",
        capability="add_fusion_comp", capability_check=capability_check,
    ):
        opening.effects = [use("eye_focus_v1")]

    if _admitted(
        registry, "anime_clean_v1", kind="color",
        capability="color_recipe", capability_check=capability_check,
    ):
        for clip in clips:
            clip.color = use("anime_clean_v1")
    if _admitted(
        registry, "anime_high_contrast_v1", kind="color",
        capability="color_recipe", capability_check=capability_check,
    ):
        for clip in impact:
            clip.color = use("anime_high_contrast_v1")
    if impact and _admitted(
        registry, "red_impact_v1", kind="color",
        capability="color_recipe", capability_check=capability_check,
    ):
        peak = impact[len(impact) // 2]
        peak.color = use("red_impact_v1")

    sound_ids = [
        recipe_id
        for recipe_id in ("impact_low_v1", "sub_impact_v1", "sword_whoosh_v1")
        if _admitted(
            registry, recipe_id, kind="sound",
            capability="sound_recipe_prebake", capability_check=capability_check,
        )
    ]
    sfx_slots = _spread(
        impact,
        min(plan.impact_budget.sfx_max, max(2, len(impact) // 3)),
    )
    for index, clip in enumerate(sfx_slots):
        recipe_id = sound_ids[index % len(sound_ids)] if sound_ids else None
        if recipe_id:
            clip.audio = clip.audio.model_copy(
                update={"sfx": [*clip.audio.sfx, SfxCue(recipe=recipe_id)]}
            )
            use(recipe_id)

    pre_drop = next(
        (clip for clip in reversed(clips) if clip.role == "pre_drop"),
        None,
    )
    total_sfx = sum(len(clip.audio.sfx) for clip in clips)
    if (
        pre_drop
        and total_sfx < plan.impact_budget.sfx_max
        and _admitted(
            registry, "riser_v1", kind="sound",
            capability="sound_recipe_prebake", capability_check=capability_check,
        )
    ):
        pre_drop.audio = pre_drop.audio.model_copy(
            update={"sfx": [*pre_drop.audio.sfx, SfxCue(recipe="riser_v1")]}
        )
        use("riser_v1")

    meta = spec.meta.model_copy(deep=True)
    meta.recipe_versions = {**meta.recipe_versions, **used_versions}
    meta.model_versions = {
        **meta.model_versions,
        "recipe_planner": RECIPE_PLANNER_VERSION,
    }
    return spec.model_copy(
        update={
            "clips": clips,
            "meta": meta,
        }
    )


__all__ = ["RECIPE_PLANNER_VERSION", "apply_recipe_plan"]
