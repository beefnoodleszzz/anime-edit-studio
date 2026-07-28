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
    "anime_glow_v1", "rgb_split_impact_v1", "speed_flash_v1",
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


def _clip_tokens(clip) -> set[str]:
    if clip.source_selection is None:
        return set()
    return {
        value.removeprefix("semantic:")
        for value in clip.source_selection.evidence
        if value.startswith("semantic:")
    }


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
        # Place phrases from the measured reference velocity peaks.  No slot
        # modulo or procedural gap pattern is allowed to invent choreography.
        fallback_direction = "right"
        ending_start = plan.duration_sec * (1 - plan.editing_style.ending_duration_ratio)
        style = plan.editing_style
        peak_times = [
            value / max(style.motion_curve[-1].time, 1e-6) * plan.duration_sec
            for value in style.motion_peaks
        ] if style.motion_curve and style.motion_peaks else []
        if not peak_times:
            peak_times = [
                clips[index].timeline.in_sec
                for index in range(1, len(clips) - 3, 5)
            ]
        starts: list[int] = []
        grammar_available = any(clip.incoming_cut is not None for clip in clips)
        # A whip-driven reference (a.mp4: p75≈5.9, change-ratio≈0.9) carries the
        # motion *through* the cut — the join is hidden by movement, so it is the
        # motion/visual continuity that motivates the phrase, not a literal
        # match-action.  Requiring match_action here left whip references with
        # zero phrases (a plain hard-cut slideshow).  For such references admit a
        # phrase where the span is dominated by motion-continuous joins.
        whip_reference = (
            style.motion_p75_target >= 3.0 or style.motion_change_ratio >= 0.6
        )
        motion_link_kinds = {
            "match_action", "graphic_match", "continuation", "carry",
        }
        if whip_reference:
            # a.mp4 rides continuous whip phrases end to end — the motion never
            # dies between accents.  Tile adjacent 4-clip phrases across the body
            # so nearly every shot is carried by movement, not just isolated
            # peaks.  Skip a span only if it lacks any motion-continuous join.
            cursor = 0
            while cursor + 4 <= len(clips):
                if clips[cursor].timeline.in_sec >= plan.duration_sec - 1.8:
                    break
                span = clips[cursor + 1:cursor + 4]
                motion_joins = sum(
                    clip.incoming_cut is not None
                    and clip.incoming_cut.kind in motion_link_kinds
                    for clip in span
                )
                contrast_joins = sum(
                    clip.incoming_cut is not None
                    and clip.incoming_cut.kind == "contrast"
                    for clip in span
                )
                # A span that is all reversal/contrast is a deliberate hard-cut
                # accent; leave it un-whipped for kinetic relief.
                if not grammar_available or motion_joins >= 1 or contrast_joins < 3:
                    starts.append(cursor)
                cursor += 4
        else:
            for peak_time in peak_times:
                start = min(
                    range(max(1, len(clips) - 3)),
                    key=lambda index: abs(
                        clips[index + 1].timeline.in_sec - peak_time
                    ),
                )
                span = clips[start + 1:start + 4]
                motivated = sum(
                    clip.incoming_cut is not None
                    and clip.incoming_cut.kind == "match_action"
                    for clip in span
                ) >= 2
                if (
                    clips[start].timeline.in_sec < plan.duration_sec - 1.8
                    and all(abs(start - existing) >= 4 for existing in starts)
                    and (not grammar_available or motivated)
                ):
                    starts.append(start)
        starts.sort()
        absolute_scale = min(2.2, max(0.65, style.motion_p75_target / 2.4))
        for phrase_index, cursor in enumerate(starts):
            group = clips[cursor:cursor + 4]
            if len(group) < 4:
                continue
            if style.motion_curve:
                reference_time = (
                    group[1].timeline.in_sec / max(plan.duration_sec, 1e-6)
                    * style.motion_curve[-1].time
                )
                point = min(
                    style.motion_curve,
                    key=lambda value: abs(value.time - reference_time),
                )
                direction_hint = "left" if point.vx < 0 else "right"
            else:
                direction_hint = (
                    style.motion_direction_pattern[cursor]
                    if cursor < len(style.motion_direction_pattern)
                    else fallback_direction
                )
            if "left" in direction_hint:
                direction = "left"
            elif "right" in direction_hint:
                direction = "right"
            else:
                direction = fallback_direction
            intensity_hint = (
                style.motion_intensity_pattern[cursor]
                if cursor < len(style.motion_intensity_pattern)
                else 0.7
            )
            # Preserve the reference's kinetic contrast.  Clamping every
            # phrase to a medium peak makes the whole edit move uniformly;
            # low-energy hints should remain micro-motion while high-energy
            # hints are allowed to produce the visible whip.
            peak = max(0.12, min(1.0, float(intensity_hint)))
            tail_scale = (
                0.52 if group[0].timeline.in_sec >= ending_start else 1.0
            )
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
                        MotionBeat(
                            clip_id=group[3].id,
                            stage="reverse",
                            intensity=max(0.28, peak) * tail_scale,
                        ),
                    ],
                    direction=direction,
                    # A whip reference wants a *visible* drag, not micro-motion:
                    # widen the displacement/scale/blur envelope so the cut is
                    # carried by movement (a.mp4 rides ×2.7 motion through cuts).
                    translation=min(
                        0.32 if whip_reference else 0.16,
                        (0.012 + 0.032 * peak) * absolute_scale * tail_scale
                        * (1.9 if whip_reference else 1.0),
                    ),
                    scale_delta=min(
                        0.26 if whip_reference else 0.12,
                        (0.008 + 0.024 * peak) * absolute_scale * tail_scale
                        * (1.9 if whip_reference else 1.0),
                    ),
                    blur_strength=min(
                        0.5,
                        (0.06 + 0.3 * peak) * (1.5 if whip_reference else 1.0),
                    ),
                    cut_window_sec=min(
                        0.24,
                        min(clip.timeline.duration_sec for clip in group) / 3,
                    ),
                    decision={
                        "source": "rule",
                        "confidence": 0.82,
                        "reasoning": (
                            "reference-derived velocity peak: accelerate → "
                            "carry → settle → zero-crossing reverse"
                        ),
                    },
                )
            )
            occupied_effects.update(clip.id for clip in group)
            fallback_direction = "left" if direction == "right" else "right"

    # Reference-led non-hard cuts become sparse, non-overlapping two-sided
    # bridges. Both TimelineItems must remain free because Resolve Fusion comps
    # are versions, not a serial stack.
    # A measured "soft_or_dissolve" boundary does not prove that the source
    # used a directional whip.  Only curated profiles may name our accepted
    # motion-blur bridge; reference profiles keep those boundaries as hard
    # cuts until transition classification can identify a compatible recipe.
    desired_transitions = (
        min(
            max(
                0,
                round(
                    (len(clips) - 1)
                    * (1 - plan.editing_style.hard_cut_ratio)
                ),
            ),
            max(0, (len(clips) - 1) // 2),
        )
        if plan.editing_style.source == "curated"
        else 0
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

    if not push_pull:
        effect_rules = (
            (
                "anime_glow_v1",
                {"glowing_eyes", "glow", "aura", "energy", "fire"},
                1,
            ),
            (
                "speed_flash_v1",
                {"running", "run", "dash", "speed", "sword_swing"},
                min(2, plan.impact_budget.flash_max),
            ),
            (
                "rgb_split_impact_v1",
                {"explosion", "hit", "punch", "kick", "impact"},
                1,
            ),
            (
                "white_flash_v1",
                {"flash", "light", "explosion", "lightning"},
                min(2, plan.impact_budget.flash_max),
            ),
        )
        for recipe_id, required, limit in effect_rules:
            if not _admitted(
                registry, recipe_id, kind="effect",
                capability="add_fusion_comp", capability_check=capability_check,
            ):
                continue
            matching = [
                clip for clip in impact
                if clip.id not in occupied_effects
                and _clip_tokens(clip) & required
            ]
            for clip in _spread(matching, min(limit, len(matching))):
                clip.effects = [use(recipe_id)]
                occupied_effects.add(clip.id)

        if _admitted(
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
    color_rules = (
        ("anime_fire_v1", {"fire", "flame", "orange", "warm"}),
        ("anime_night_blue_v1", {"night", "dark", "moon", "blue"}),
        ("anime_cold_v1", {"sad", "cold", "snow", "crying"}),
    )
    for recipe_id, required in color_rules:
        if not _admitted(
            registry, recipe_id, kind="color",
            capability="color_recipe", capability_check=capability_check,
        ):
            continue
        for clip in clips:
            if _clip_tokens(clip) & required:
                clip.color = use(recipe_id)
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
        tokens = _clip_tokens(clip)
        preferred = (
            "sword_whoosh_v1"
            if tokens & {"sword", "sword_swing", "slash", "blade"}
            else "sub_impact_v1"
            if tokens & {"drop", "explosion", "power", "aura"}
            else "impact_low_v1"
        )
        recipe_id = (
            preferred if preferred in sound_ids
            else sound_ids[index % len(sound_ids)] if sound_ids else None
        )
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
