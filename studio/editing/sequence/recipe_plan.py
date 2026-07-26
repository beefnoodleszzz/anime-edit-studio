"""Deterministic, capability-gated Recipe intent planning.

Creative structure comes from DirectorPlan. This module only maps that
structure to already accepted Recipes while enforcing impact-density budgets.
It never invents Recipe ids or bypasses resolve_capabilities.yaml.
"""
from __future__ import annotations

from collections.abc import Callable

from studio.core.capabilities import is_verified
from studio.creative.director import DirectorPlan
from studio.editspec.schema import (
    EditSpec,
    MotionBeat,
    MotionPhrase,
    RecipeRef,
    Retime,
    SfxCue,
    TransitionEnd,
)
from studio.execution.recipes import RecipeRegistry

RECIPE_PLANNER_VERSION = "recipe-planner-1.3.0"
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
        if clip.transition.in_.recipe == "motion_blur_transition_v1":
            clip.transition.in_ = TransitionEnd()
        if clip.transition.out.recipe == "motion_blur_transition_v1":
            clip.transition.out = TransitionEnd()
        if clip.retime.type == "speed_ramp":
            clip.retime = Retime()
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
    motion_phrases: list[MotionPhrase] = []

    if (
        capability_check("motion_phrase_compositor")
        and plan.editing_style.source in {"reference", "curated"}
        and len(clips) >= 3
    ):
        # A phrase must carry velocity through the cut.  V1.2 used a fixed
        # "two moving + four hold" cadence; that leaked a visible four-second
        # template into reference-led edits and stopped motion at the cut.
        # Three-beat phrases keep the peak on the boundary and vary the
        # breathing gaps.  The final emotional tail is left un-whipped so its
        # deceleration comes from the picture rhythm rather than a late effect.
        phrase_index = 0
        cursor = 0
        fallback_direction = "right"
        gap_pattern = (1, 2, 1, 3, 1)
        ending_start = plan.duration_sec * (1 - plan.editing_style.ending_duration_ratio)
        while cursor + 2 < len(clips):
            group = clips[cursor:cursor + 3]
            if group[0].timeline.in_sec >= ending_start:
                break
            direction_hint = (
                plan.editing_style.motion_direction_pattern[cursor]
                if cursor < len(plan.editing_style.motion_direction_pattern)
                else fallback_direction
            )
            if "left" in direction_hint:
                direction = "left"
            elif "right" in direction_hint:
                direction = "right"
            else:
                direction = fallback_direction
            intensity_hint = (
                plan.editing_style.motion_intensity_pattern[cursor]
                if cursor < len(plan.editing_style.motion_intensity_pattern)
                else 0.7
            )
            # Preserve the reference's kinetic contrast.  Clamping every
            # phrase to a medium peak makes the whole edit move uniformly;
            # low-energy hints should remain micro-motion while high-energy
            # hints are allowed to produce the visible whip.
            peak = max(0.12, min(1.0, float(intensity_hint)))
            motion_phrases.append(
                MotionPhrase(
                    id=f"motion-phrase-{phrase_index:03d}",
                    beats=[
                        MotionBeat(
                            clip_id=group[0].id,
                            stage="accelerate",
                            intensity=max(0.12, peak * 0.72),
                        ),
                        MotionBeat(
                            clip_id=group[1].id,
                            stage="carry",
                            intensity=max(0.14, peak * 0.78),
                        ),
                        MotionBeat(
                            clip_id=group[2].id,
                            stage="settle",
                            intensity=max(0.1, peak * 0.58),
                        ),
                    ],
                    direction=direction,
                    translation=0.004 + 0.016 * peak,
                    scale_delta=0.002 + 0.014 * peak,
                    blur_strength=0.06 + 0.3 * peak,
                    cut_window_sec=min(
                        0.24,
                        min(clip.timeline.duration_sec for clip in group) / 3,
                    ),
                    decision={
                        "source": "rule",
                        "confidence": 0.82,
                        "reasoning": (
                            "reference velocity wave: accelerate → carry across "
                            "cuts → settle; irregular breathing gap"
                        ),
                    },
                )
            )
            occupied_effects.update(clip.id for clip in group)
            fallback_direction = "left" if direction == "right" else "right"
            gap = gap_pattern[phrase_index % len(gap_pattern)]
            phrase_index += 1
            cursor += len(group) + gap

    # Reference-led non-hard cuts become sparse, non-overlapping two-sided
    # bridges. Both TimelineItems must remain free because Resolve Fusion comps
    # are versions, not a serial stack.
    desired_transitions = min(
        max(0, round((len(clips) - 1) * (1 - plan.editing_style.hard_cut_ratio))),
        max(0, (len(clips) - 1) // 2),
    )
    if desired_transitions and _admitted(
        registry, "motion_blur_transition_v1", kind="transition",
        capability="transition", capability_check=capability_check,
    ):
        candidates = list(range(len(clips) - 1))
        for cut_index in _spread(candidates, desired_transitions):
            left, right = clips[cut_index], clips[cut_index + 1]
            if left.id in occupied_effects or right.id in occupied_effects:
                continue
            duration = min(
                0.24,
                left.timeline.duration_sec / 3,
                right.timeline.duration_sec / 3,
            )
            params = {"length": duration, "angle": 0.0}
            left.transition.out = TransitionEnd(
                recipe="motion_blur_transition_v1",
                duration_sec=duration,
                params=params,
            )
            right.transition.in_ = TransitionEnd(
                recipe="motion_blur_transition_v1",
                duration_sec=duration,
                params=params,
            )
            occupied_effects.update((left.id, right.id))
            use("motion_blur_transition_v1")

    desired_ramps = min(
        len(impact),
        max(0, round(plan.duration_sec * plan.editing_style.speed_ramp_density)),
    )
    if desired_ramps and _admitted(
        registry, "speed_ramp_v1", kind="effect",
        capability="timespeed_recipe", capability_check=capability_check,
    ):
        available = [clip for clip in impact if clip.id not in occupied_effects]
        for clip in _spread(available, desired_ramps):
            clip.retime = Retime(
                type="speed_ramp",
                entry_speed=0.55,
                impact_speed=1.65,
                exit_speed=0.75,
                impact_at_sec=clip.timeline.duration_sec / 2,
            )
            occupied_effects.add(clip.id)
            use("speed_ramp_v1")

    # Reference-led tug grammar: the accepted CameraPunch Fusion comp performs
    # a deterministic push-in then pull-out inside every shot. This deliberately
    # replaces sparse impact effects because Resolve exposes Fusion comps as
    # versions, not a guaranteed serial stack.
    if push_pull and _admitted(
        registry, "camera_punch_v1", kind="effect",
        capability="add_fusion_comp", capability_check=capability_check,
    ):
        for clip in clips:
            if clip.id in occupied_effects:
                continue
            clip.effects = [use("camera_punch_v1")]
            occupied_effects.add(clip.id)

    if not push_pull and _admitted(
        registry, "white_flash_v1", kind="effect",
        capability="add_fusion_comp", capability_check=capability_check,
    ):
        available = [clip for clip in impact if clip.id not in occupied_effects]
        for clip in _spread(available, min(plan.impact_budget.flash_max, 2)):
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
    if (
        opening
        and opening.id not in occupied_effects
        and not push_pull
        and _admitted(
        registry, "eye_focus_v1", kind="effect",
        capability="add_fusion_comp", capability_check=capability_check,
        )
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
            "motion_phrases": motion_phrases,
            "meta": meta,
        }
    )


__all__ = ["RECIPE_PLANNER_VERSION", "apply_recipe_plan"]
