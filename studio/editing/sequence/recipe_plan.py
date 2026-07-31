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
    Marker,
    MotionBeat,
    MotionPhrase,
    RecipeRef,
    Retime,
    SfxCue,
    TransitionEnd,
)
from studio.execution.recipes import RecipeRegistry
from studio.editing.music import MusicMotionMap

RECIPE_PLANNER_VERSION = "recipe-planner-1.10.0"
_MANAGED_EFFECTS = {
    "white_flash_v1", "impact_shake_v1", "eye_focus_v1", "camera_punch_v1",
    "anime_glow_v1", "rgb_split_impact_v1", "speed_flash_v1",
}
_MANAGED_SOUNDS = {
    "impact_low_v1", "sub_impact_v1", "sword_whoosh_v1", "riser_v1",
}
_MANAGED_TRANSITIONS = {
    "motion_blur_transition_v1", "motion_blur_transition_v2",
}
# Reference edits mostly play source footage slowed down; only the editor's
# own camera work is at "real" speed. Opening/ending shots hold longest,
# impact-adjacent shots stay closest to native speed so beat-synced action
# still reads, everything else sits in the calm middle.
_CALM_RETIME_SPEED: dict[str | None, float] = {
    "opening": 0.80,
    "ending": 0.80,
    "impact": 0.85,
    None: 0.80,
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


def _motion_evidence(relation) -> str | None:
    if relation is None or relation.kind != "match_action" or relation.confidence < 0.7:
        return None
    return next(
        (
            feature for feature in relation.matched_features
            if feature.startswith(("motion_direction:", "motion_reversal:"))
        ),
        None,
    )


def _reference_motion_groups(clips: list) -> list[list]:
    """Return non-overlapping 3–4 clip runs joined by measured motion evidence."""
    groups: list[list] = []
    run_start: int | None = None
    for index in range(1, len(clips) + 1):
        evidence = (
            _motion_evidence(clips[index].incoming_cut)
            if index < len(clips)
            else None
        )
        if evidence:
            if run_start is None:
                run_start = index - 1
            continue
        if run_start is None:
            continue
        run = clips[run_start:index]
        cursor = 0
        while len(run) - cursor >= 3:
            remaining = len(run) - cursor
            group_size = 3 if remaining in {3, 6} else 4
            groups.append(run[cursor:cursor + group_size])
            cursor += group_size
        run_start = None
    return groups


def _evidence_direction(feature: str | None) -> str:
    if not feature:
        return "right"
    value = feature.split(":", 1)[1]
    if "->" in value:
        value = value.split("->", 1)[1]
    return "left" if "left" in value else "right"


def _reference_curve_direction(style, timeline_sec: float, duration_sec: float) -> str:
    """Map the measured finished-edit velocity to an executable cardinal axis."""
    if not style.motion_curve:
        return "right"
    reference_time = (
        timeline_sec / max(duration_sec, 1e-6) * style.motion_curve[-1].time
    )
    point = min(
        style.motion_curve,
        key=lambda value: abs(value.time - reference_time),
    )
    if abs(point.vy) > abs(point.vx):
        return "up" if point.vy < 0 else "down"
    return "left" if point.vx < 0 else "right"


def _opposite_direction(first: str, second: str) -> bool:
    vectors = {
        "left": (-1, 0), "right": (1, 0),
        "up": (0, -1), "down": (0, 1),
        "up-left": (-1, -1), "up-right": (1, -1),
        "down-left": (-1, 1), "down-right": (1, 1),
    }
    ax, ay = vectors[first]
    bx, by = vectors[second]
    return ax * bx + ay * by < 0


def _reverse_direction(direction: str) -> str:
    return {
        "left": "right", "right": "left",
        "up": "down", "down": "up",
        "up-left": "down-right", "down-right": "up-left",
        "up-right": "down-left", "down-left": "up-right",
    }[direction]


def apply_recipe_plan(
    spec: EditSpec,
    *,
    plan: DirectorPlan,
    registry: RecipeRegistry | None = None,
    capability_check: Callable[[str], bool] = is_verified,
    music_motion: MusicMotionMap | None = None,
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
        if clip.transition.in_.recipe in _MANAGED_TRANSITIONS:
            clip.transition.in_ = TransitionEnd()
        if clip.transition.out.recipe in _MANAGED_TRANSITIONS:
            clip.transition.out = TransitionEnd()
        if clip.retime.type == "speed_ramp" or abs(clip.retime.speed - 1.0) > 1e-9:
            clip.source = clip.source.model_copy(
                update={"out_sec": clip.source.in_sec + clip.timeline.duration_sec}
            )
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
        and plan.editing_style.source == "reference"
        and len(clips) >= 3
    ):
        style = plan.editing_style
        # A high-change reference is editor-driven motion choreography, not a
        # request to find naturally fast footage. Tile the actual timeline with
        # adjacent phrases so velocity is shaped through cuts. For calmer
        # references retain the conservative measured-match-action admission.
        editor_driven = (
            style.motion_change_ratio >= 0.6
            or style.motion_p75_target >= 3.0
        )
        groups = (
            [clips[index:index + 4] for index in range(0, len(clips), 4)]
            if editor_driven
            else _reference_motion_groups(clips)
        )
        groups = [group for group in groups if len(group) >= 2]
        absolute_scale = min(2.2, max(0.65, style.motion_p75_target / 2.4))
        global_directions = (
            [
                _reference_curve_direction(
                    style, clip.timeline.in_sec, plan.duration_sec
                )
                for clip in clips
            ]
            if editor_driven else []
        )
        cursor = 0
        for phrase_index, group in enumerate(groups):
            features = [
                _motion_evidence(clip.incoming_cut)
                for clip in group[1:]
            ]
            beat_directions = []
            if editor_driven:
                measured_direction = global_directions[cursor]
                phrase_direction = (
                    "left" if "left" in measured_direction
                    else "right" if "right" in measured_direction
                    else "left" if phrase_index % 2 else "right"
                )
                beat_directions = [phrase_direction] * len(group)
            direction = (
                beat_directions[0]
                if beat_directions
                else _evidence_direction(features[0])
            )
            accents = [
                music_motion.nearest(
                    clip.timeline.out_sec,
                    tolerance_sec=min(0.16, max(0.08, clip.timeline.duration_sec / 4)),
                )
                if music_motion is not None else None
                for clip in group
            ]
            incoming_accents = [
                music_motion.nearest(
                    clip.timeline.in_sec,
                    tolerance_sec=min(
                        0.16, max(0.08, clip.timeline.duration_sec / 4)
                    ),
                )
                if music_motion is not None else None
                for clip in group
            ]
            intensities = [
                (
                    max(0.2, accent.strength)
                    if accent is not None
                    else max(
                        0.2 if editor_driven else 0.08,
                        min(
                            1.0,
                            float(style.motion_intensity_pattern[index])
                            if index < len(style.motion_intensity_pattern)
                            else 0.55,
                        ),
                    )
                )
                for index, accent in zip(
                    range(cursor, cursor + len(group)), accents, strict=True
                )
            ]
            if editor_driven and music_motion is not None:
                # Direction changes are editorial punctuation. Carry weak
                # pulses; reverse or change zoom on structural accents so the
                # BGM controls choreography rather than merely scaling a
                # fixed per-clip template.
                current_direction = beat_directions[0]
                for index in range(1, len(group)):
                    prior = accents[index - 1]
                    if (
                        prior is not None
                        and (
                            prior.kind in {"impact", "downbeat"}
                            or prior.strength >= 0.82
                        )
                    ):
                        current_direction = _reverse_direction(current_direction)
                    beat_directions[index] = current_direction
            beat_zoom_directions = []
            # The reference's cut grammar is a radial relay: the outgoing shot
            # pushes into the cut and the incoming shot keeps pushing for its
            # first 3–6 frames. Alternating in/out produces a visible pull-back
            # exactly where the demo accelerates forward.
            current_zoom = "in"
            for index in range(len(group)):
                beat_zoom_directions.append(current_zoom)
            peak = max(intensities)
            scale_delta = min(
                0.85, (0.020 + 0.054 * peak) * absolute_scale * 6.4
            )
            translation = min(
                0.16,
                scale_delta / (2.0 * (1.0 + scale_delta)) * 0.92,
                (0.014 + 0.038 * peak) * absolute_scale * 3.2,
            )
            if editor_driven:
                motion_intensities = [max(0.55, value) for value in intensities]
                velocities = [
                    (
                        accent.target_velocity
                        if accent is not None
                        else 0.12 + 0.10 * value
                    )
                    for value, accent in zip(
                        motion_intensities, accents, strict=True
                    )
                ]
                entry_velocities = [
                    (
                        min(velocities[index - 1], velocities[index])
                        if index > 0
                        and beat_directions[index - 1] == beat_directions[index]
                        else 0.0
                    )
                    for index in range(len(group))
                ]
                exit_velocities = [
                    (
                        min(velocities[index], velocities[index + 1])
                        if index + 1 < len(group)
                        and beat_directions[index] == beat_directions[index + 1]
                        else 0.0
                    )
                    for index in range(len(group))
                ]
                stages = [
                    (
                        "carry" if entry > 0 and exit > 0
                        else "settle" if entry > 0
                        else "accelerate" if exit > 0
                        else "reverse" if index > 0
                        and beat_directions[index - 1] != beat_directions[index]
                        else "accelerate"
                    )
                    for index, (entry, exit) in enumerate(
                        zip(entry_velocities, exit_velocities, strict=True)
                    )
                ]
            else:
                motion_intensities = intensities
                entry_velocities = [0.0] * len(group)
                exit_velocities = [0.0] * len(group)
                stages = [
                    "accelerate",
                    *[
                        "reverse"
                        if feature and feature.startswith("motion_reversal:")
                        else "carry"
                        for feature in features
                    ],
                ]
            beats = [
                MotionBeat(
                    clip_id=clip.id,
                    stage=stage,
                    intensity=intensity,
                    direction=(
                        beat_direction if editor_driven else None
                    ),
                    zoom_direction=(
                        beat_zoom_direction if editor_driven else None
                    ),
                    accent_at_sec=(
                        # A settle clip inherits the impact at its *incoming*
                        # boundary.  Mapping the outgoing accent here made the
                        # new shot wait until its own tail before accelerating,
                        # so every hard cut visibly restarted the move.
                        0.0
                        if (
                            incoming_accent is not None
                            and (stage != "accelerate" or accent is None)
                        )
                        else (
                            max(
                                0.0,
                                min(
                                    clip.timeline.duration_sec,
                                    accent.sec - clip.timeline.in_sec,
                                ),
                            )
                            if accent is not None else None
                        )
                    ),
                    anticipation_sec=(
                        incoming_accent.anticipation_sec
                        if (
                            incoming_accent is not None
                            and (stage != "accelerate" or accent is None)
                        )
                        else accent.anticipation_sec if accent is not None else None
                    ),
                    release_sec=(
                        incoming_accent.release_sec
                        if (
                            incoming_accent is not None
                            and (stage != "accelerate" or accent is None)
                        )
                        else accent.release_sec if accent is not None else None
                    ),
                    entry_intensity=(
                        incoming_accent.strength
                        if incoming_accent is not None else intensity
                    ),
                    translation=(
                        min(
                            0.08 / intensity,
                            velocity * clip.timeline.duration_sec / intensity,
                        )
                        if editor_driven else None
                    ),
                    scale_delta=(
                        min(0.32, (
                            (0.12 + 0.16 * intensity)
                            * min(1.0, clip.timeline.duration_sec / 0.7)
                        )) / intensity
                        if editor_driven else None
                    ),
                    entry_velocity=entry_velocity,
                    exit_velocity=exit_velocity,
                )
                for (
                    clip,
                    stage,
                    intensity,
                    beat_direction,
                    beat_zoom_direction,
                    accent,
                    incoming_accent,
                    velocity,
                    entry_velocity,
                    exit_velocity,
                ) in zip(
                    group,
                    stages,
                    motion_intensities,
                    beat_directions or [direction] * len(group),
                    beat_zoom_directions,
                    accents,
                    incoming_accents,
                    (
                        velocities
                        if editor_driven else [0.0] * len(group)
                    ),
                    entry_velocities,
                    exit_velocities,
                    strict=True,
                )
            ]
            motion_phrases.append(
                MotionPhrase(
                    id=f"motion-phrase-{phrase_index:03d}",
                    beats=beats,
                    direction=direction,
                    zoom_direction=(
                        "in" if editor_driven
                        else "in" if phrase_index % 2 == 0 else "out"
                    ),
                    translation=translation,
                    scale_delta=scale_delta,
                    rotation_deg=(
                        (1 if phrase_index % 2 == 0 else -1)
                        * min(4.0, 0.8 + 2.6 * peak)
                        if editor_driven else 0.0
                    ),
                    blur_strength=min(0.18, 0.025 + 0.10 * peak),
                    cut_window_sec=min(
                        0.24,
                        min(clip.timeline.duration_sec for clip in group) / 3,
                    ),
                    decision={
                        "source": "rule",
                        "confidence": 0.84,
                        "reasoning": (
                            "reference frame-motion envelope mapped to musical "
                            "timeline: accelerate → carry → settle → reverse"
                            if editor_driven
                            else "adjacent match-action boundaries with measured "
                            "carry/reversal direction evidence"
                        ),
                    },
                )
            )
            occupied_effects.update(clip.id for clip in group)
            cursor += len(group)

    if (
        capability_check("motion_phrase_compositor")
        and plan.editing_style.source == "curated"
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
    # Prefer v2 (adds a short landing-settle zoom on the incoming side) once a
    # human has reviewed it; until then this quietly falls back to the
    # long-accepted v1 blur-only bridge so behavior never regresses silently.
    transition_recipe = "motion_blur_transition_v2"
    if not _admitted(
        registry, transition_recipe, kind="transition",
        capability="transition", capability_check=capability_check,
    ):
        transition_recipe = "motion_blur_transition_v1"
    if desired_transitions and _admitted(
        registry, transition_recipe, kind="transition",
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
            if transition_recipe == "motion_blur_transition_v2":
                params["settle_scale"] = 0.05
            left.transition.out = TransitionEnd(
                recipe=transition_recipe,
                duration_sec=duration,
                params=params,
            )
            right.transition.in_ = TransitionEnd(
                recipe=transition_recipe,
                duration_sec=duration,
                params=params,
            )
            occupied_effects.update((left.id, right.id))
            use(transition_recipe)

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

    # A short, visually verified demon-eye close-up may request a localized
    # eye glow inside the already-owned MotionPhrase comp. The cue remains
    # explicit in EditSpec instead of being inferred by the Resolve compiler.
    eye_closeups = [
        clip for clip in clips
        if clip.role == "ending"
        and {"cracked_skin", "horns"} <= _clip_tokens(clip)
    ]
    if eye_closeups:
        eye_clip = eye_closeups[-1]
        spec.markers = [
            marker for marker in spec.markers
            if marker.kind != "eye_glow_cue"
        ]
        spec.markers.append(
            Marker(
                sec=eye_clip.timeline.in_sec,
                duration_sec=eye_clip.timeline.duration_sec,
                kind="eye_glow_cue",
                note="Localized pink-purple eye glow",
                clip_id=eye_clip.id,
            )
        )
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
        color_targets = (
            clips if "tiktok_impact" in plan.tone else impact
        )
        for clip in color_targets:
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

    # Reference edits (a.mp4-class) read as calm/held or slowed footage — the
    # *editor's* camera work supplies the motion, not the raw source playing
    # at native speed. Our shots were, until now, always played back 1:1,
    # which reads as "just moving footage with effects on it" no matter how
    # good the motion phrases/transitions above are. Give every clip that
    # didn't already claim a special retime (speed_ramp) a calm constant
    # slow-down baseline; this only narrows the already-assigned source
    # window (never reads past it), so it is always safe to apply.
    if capability_check("timespeed_recipe"):
        for clip in clips:
            if clip.retime.type != "constant" or abs(clip.retime.speed - 1.0) > 1e-9:
                continue
            speed = _CALM_RETIME_SPEED.get(clip.role, _CALM_RETIME_SPEED[None])
            clip.retime = Retime(type="constant", speed=speed)
            clip.source = clip.source.model_copy(
                update={
                    "out_sec": clip.source.in_sec
                    + clip.timeline.duration_sec * speed
                }
            )
            if (
                clip.source_selection is not None
                and clip.source_selection.anchor_sec > clip.source.out_sec
            ):
                clip.source_selection = clip.source_selection.model_copy(
                    update={
                        "anchor_sec": clip.source.out_sec,
                        "evidence": [
                            *clip.source_selection.evidence,
                            "retime_anchor_clamped",
                        ],
                    }
                )

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
