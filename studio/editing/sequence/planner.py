"""Beam-search Sequence Planner producing an EditSpec draft.

This is deliberately not beat-cut or per-slot greedy selection. The beam keeps
multiple complete prefixes and evaluates continuity, variation, role fit and
repeat constraints across the visual phrase.
"""
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from studio.core.hashing import stable_hash
from studio.creative.director import DirectorPlan
from studio.creative.reference import EditingStyleProfile
from studio.editing.music import MusicMap
from studio.editing.ranking import RankedCandidate
from studio.editing.sequence.visual_phrase import plan_visual_phrases
from studio.editspec.schema import (
    Canvas,
    Clip,
    CutRelation,
    CreatedFrom,
    Decision,
    EditSpec,
    Framing,
    SourceRange,
    SourceSelection,
    SpecMeta,
    Timebase,
    TimelinePlacement,
)

SEQUENCE_PLANNER_VERSION = "sequence-planner-3.1.0"
_ROLE = {
    "buildup": "build",
    "drop": "impact",
}

# When the candidate pool is thinner than the beat-locked cut count, keeping the
# rhythm matters more than never repeating a shot: a燃-cut edit reuses its
# strongest shots on later beats rather than hanging one shot across many beats.
# A shot may reappear only after this many other clips, and each reuse is
# penalized so fresh shots always win when they exist.
MIN_REPEAT_GAP = 4
REPEAT_PENALTY = 0.6


def canonical_role(role: str) -> str:
    return _ROLE.get(role, role)


@dataclass(frozen=True)
class _Slot:
    role: str
    start: float
    duration: float
    energy: float
    intent: str = "carry"


@dataclass
class _State:
    score: float
    shot_ids: list[str]
    rows: list[sqlite3.Row]
    alternatives: list[list[str]]


def _section_at(plan: DirectorPlan, sec: float):
    return next(
        (
            section
            for section in plan.structure
            if section.start <= sec < section.end
        ),
        plan.structure[-1],
    )


def _pattern_cuts(
    start: float,
    end: float,
    average: float,
    profile: EditingStyleProfile,
) -> list[float]:
    """Generate varied fallback cuts from a portable duration pattern."""
    duration = end - start
    # The style owns global cadence; the Director's section value modulates it
    # so energy structure survives when one style is reused across projects.
    style_average = 1.0 / profile.target_cut_density
    effective_average = (style_average * max(average, 1e-6)) ** 0.5
    count = max(1, round(duration / effective_average))
    pattern = [
        profile.duration_pattern[index % len(profile.duration_pattern)]
        for index in range(count)
    ]
    scale = duration / sum(pattern)
    cursor = start
    cuts = []
    for value in pattern[:-1]:
        cursor += value * scale
        cuts.append(cursor)
    return cuts


def _snap_cuts_to_music(
    cuts: list[float],
    *,
    plan: DirectorPlan,
    music: MusicMap,
    profile: EditingStyleProfile,
) -> list[float]:
    """Snap only the measured share of cuts; preserve the remaining cadence.

    This avoids mechanical every-beat cutting while guaranteeing that the
    requested reference affinity is measurable.
    """
    if not cuts or not music.beats or profile.beat_sync_target <= 0:
        return cuts
    # Reference cadence controls spacing, never the phase of a different
    # soundtrack. Use the detected beat grid as the single phase authority:
    # mixing arbitrary onsets and impacts into the cut grid produces individually
    # "synced" cuts whose intervals do not form a coherent 1/2/4-beat grammar.
    anchors = sorted(
        set(beat for beat in music.beats if 0 < beat < plan.duration_sec)
    )
    nearest = [
        min(
            anchors,
            key=lambda anchor: (abs(anchor - cut), anchor),
        )
        for cut in cuts
    ]
    snap_count = min(
        len(cuts),
        max(0, round(len(cuts) * profile.beat_sync_target)),
    )
    snap_indices = {
        index
        for index, _ in sorted(
            enumerate(cuts),
            key=lambda item: (
                abs(nearest[item[0]] - item[1]),
                item[1],
            ),
        )[:snap_count]
    }
    return [
        nearest[index] if index in snap_indices else cut
        for index, cut in enumerate(cuts)
    ]


def _dedupe_boundaries(
    boundaries: list[float],
    *,
    duration: float,
    minimum: float,
) -> list[float]:
    output = [0.0]
    for value in sorted(boundaries):
        value = max(0.0, min(duration, round(value, 6)))
        if value - output[-1] < minimum:
            continue
        if duration - value < minimum:
            continue
        output.append(value)
    if duration - output[-1] < minimum and len(output) > 1:
        output.pop()
    output.append(duration)
    return output


def _slots(plan: DirectorPlan, music: MusicMap) -> list[_Slot]:
    profile = plan.editing_style
    preserve_phrase_accents = False
    beat_grid_mode = bool(
        music.bpm
        and music.beats
        and profile.beat_grid_subdivision == "section_1_2_4"
    )
    if profile.normalized_cut_positions:
        cuts = [
            value * plan.duration_sec
            for value in profile.normalized_cut_positions
        ]
    else:
        cuts = [
            value
            for section in plan.structure
            for value in _pattern_cuts(
                section.start,
                section.end,
                section.average_shot_length,
                profile,
            )
        ]
    cuts = _snap_cuts_to_music(
        cuts,
        plan=plan,
        music=music,
        profile=profile,
    )
    intent_by_start: dict[float, str] = {0.0: "establish"}
    if (
        beat_grid_mode
        and "vibe" in {value.lower() for value in plan.tone}
        and not profile.normalized_cut_positions
    ):
        phrase_plan = plan_visual_phrases(
            music, duration_sec=plan.duration_sec
        )
        cuts = phrase_plan.cut_times
        preserve_phrase_accents = (
            len({
                round(value, 6)
                for value in music.impact_points
                if 0 < value < plan.duration_sec
            }) / plan.duration_sec >= 0.65
        )
        for phrase in phrase_plan.phrases:
            for sec, intent in zip(
                phrase.cut_times, phrase.shot_intents, strict=True
            ):
                intent_by_start[round(sec, 6)] = intent
    elif beat_grid_mode and not profile.normalized_cut_positions:
        # Build a section-aware 1/2/4-beat grammar. The hook and impact may cut
        # every beat, melodic build/release phrases use two beats, and the
        # ending gets four-beat breathing room.
        grid_cuts: list[float] = []
        hook_end = min(plan.duration_sec * profile.hook_duration_ratio, 2.2)
        for section in plan.structure:
            role = canonical_role(section.role)
            section_beats = [
                beat
                for beat in music.beats
                if section.start < beat < min(section.end, plan.duration_sec)
            ]
            for index, beat in enumerate(section_beats):
                stride = (
                    1
                    if role == "impact" or beat <= hook_end
                    else 4
                    if role == "ending"
                    else 2
                )
                if index % stride == 0:
                    grid_cuts.append(beat)
        cuts = grid_cuts
    # Long unsignalled gaps both weaken rhythm and require unnecessarily long
    # source shots. Subdivide them on the current soundtrack's measured
    # onsets/beats while preserving the reference's maximum shot duration.
    anchors = sorted(set([*music.beats, *music.onsets, *music.impact_points]))
    maximum = min(1.2, profile.max_shot_length)
    changed = True
    gap_fill_cuts: list[float] = []
    while changed and anchors and profile.source == "reference":
        changed = False
        boundaries = [0.0, *sorted(set(cuts)), plan.duration_sec]
        for left, right in zip(boundaries, boundaries[1:]):
            if right - left <= maximum:
                continue
            options = [
                value for value in anchors
                if left + 0.2 < value < right - 0.2
            ]
            if options:
                choice = min(options, key=lambda value: abs(value - (left + right) / 2))
                cuts.append(choice)
                gap_fill_cuts.append(choice)
                changed = True
                break
    # These cuts exist to enforce the hard max-shot-length ceiling, not merely
    # to chase density — the later density-budget prune (below) sorts by
    # proximity to the nearest beat/impact anchor and can otherwise drop a
    # gap-fill cut in favour of a cut elsewhere in the timeline that happens
    # to sit closer to its own anchor, silently reopening the long gap this
    # loop just closed (observed: a 2.6s hold surviving straight through the
    # impact section because its gap-fill cut lost the global proximity sort).
    protected: list[float] = list(gap_fill_cuts)
    if profile.source == "reference" and not profile.normalized_cut_positions:
        hook_end = min(
            plan.duration_sec * profile.hook_duration_ratio,
            2.2,
        )
        if profile.hook_event_count > 1 and hook_end > 0:
            hook_step = hook_end / profile.hook_event_count
            cuts = [
                cut for cut in cuts
                if not (0 < cut < hook_end)
            ]
            hook_cuts = [
                hook_step * index
                for index in range(1, profile.hook_event_count + 1)
            ]
            cuts.extend(hook_cuts)
            protected.extend(hook_cuts)

        ending_start = plan.duration_sec * (1 - profile.ending_duration_ratio)
        ending_duration = plan.duration_sec - ending_start
        ending_pattern = profile.ending_deceleration_pattern
        ending_scale = ending_duration / sum(ending_pattern)
        cuts = [
            cut for cut in cuts
            if not (ending_start < cut < plan.duration_sec)
        ]
        cursor = ending_start
        cuts.append(cursor)
        protected.append(cursor)
        for weight in ending_pattern[:-1]:
            cursor += weight * ending_scale
            cuts.append(cursor)
            protected.append(cursor)
    # Narrative section boundaries are semantic anchors and must survive even
    # when the reference has a different duration or music structure.
    section_cuts = [section.end for section in plan.structure[:-1]]
    cuts.extend(section_cuts)
    protected.extend(section_cuts)
    # Hook, ending, and semantic boundaries are generated after the first snap;
    # align them too. Their narrative meaning survives a sub-frame musical
    # adjustment, while an off-beat "protected" cut does not.
    if not preserve_phrase_accents:
        cuts = _snap_cuts_to_music(
            cuts, plan=plan, music=music, profile=profile
        )
        protected = _snap_cuts_to_music(
            protected, plan=plan, music=music, profile=profile
        )

    target_cut_count = max(
        len({round(value, 6) for value in protected}),
        round(profile.target_cut_density * plan.duration_sec),
    )
    protected_keys = {round(value, 6) for value in protected}
    unique_cuts = {
        round(value, 6): float(value)
        for value in cuts
        if 0 < value < plan.duration_sec
    }
    if beat_grid_mode:
        # The grid is already density-controlled by musical phrase; pruning it
        # by reference density would cluster cuts early and create long gaps.
        target_cut_count = len(unique_cuts)
    keep = {
        key: value
        for key, value in unique_cuts.items()
        if key in protected_keys
    }
    remaining = [
        (key, value)
        for key, value in unique_cuts.items()
        if key not in protected_keys
    ]
    rank_anchors = [*music.beats, *music.impact_points]
    remaining.sort(
        key=lambda item: (
            min(abs(item[1] - anchor) for anchor in rank_anchors)
            if rank_anchors else 0.0,
            item[1],
        )
    )
    for key, value in remaining[: max(0, target_cut_count - len(keep))]:
        keep[key] = value
    # The prune above ranks every candidate globally by proximity to a beat or
    # impact point. A section that happens to have several less-on-anchor
    # candidates (e.g. onset-driven gap fills, which score against the wider
    # beats+onsets+impacts anchor set above but not against this narrower one)
    # can lose all of them to unrelated cuts elsewhere in the timeline, quietly
    # reopening a gap the earlier max-shot-length subdivision had already
    # closed. Repair any such gap by pulling the best still-available
    # candidate for it back in, even past the density budget: a shot held far
    # longer than the reference's own maximum is a worse rhythm failure than
    # slightly exceeding the target cut count.
    dropped = {key: value for key, value in remaining if key not in keep}
    changed = True
    while changed and dropped:
        changed = False
        kept_values = sorted(keep.values())
        span_boundaries = [0.0, *kept_values, plan.duration_sec]
        for left, right in zip(span_boundaries, span_boundaries[1:]):
            if right - left <= maximum:
                continue
            pool = [
                (key, value) for key, value in dropped.items()
                if left < value < right
            ]
            if not pool:
                continue
            best_key, best_value = min(
                pool,
                key=lambda item: (
                    min(abs(item[1] - anchor) for anchor in anchors)
                    if anchors else 0.0,
                    item[1],
                ),
            )
            keep[best_key] = best_value
            del dropped[best_key]
            changed = True
            break
    cuts = list(keep.values())
    minimum = max(0.08, min(profile.min_shot_length * 0.72, 0.32))
    boundaries = _dedupe_boundaries(
        cuts,
        duration=plan.duration_sec,
        minimum=minimum,
    )
    values = []
    for slot_index, (start, end) in enumerate(zip(boundaries, boundaries[1:])):
        section = _section_at(plan, (start + end) / 2)
        role = canonical_role(section.role)
        intent = intent_by_start.get(round(start, 6))
        if intent is None:
            near_impact = any(
                abs(start - point) <= profile.beat_tolerance_sec
                for point in music.impact_points
            )
            intent = (
                "impact" if role == "impact" or near_impact
                else "anticipation" if role == "pre_drop"
                else "settle" if role == "ending"
                else "reaction" if role == "release"
                else "establish" if slot_index == 0
                else "carry"
            )
        values.append(
            _Slot(
                role=role,
                start=start,
                duration=end - start,
                energy=section.energy,
                intent=intent,
            )
        )
    return values


def rhythm_metrics(
    slots: list[_Slot],
    music: MusicMap,
    *,
    tolerance_sec: float = 0.08,
) -> dict[str, float | int]:
    cuts = [slot.start for slot in slots[1:]]
    synced = sum(
        min(abs(cut - beat) for beat in music.beats) <= tolerance_sec
        for cut in cuts
    ) if music.beats else 0
    durations = sorted(slot.duration for slot in slots)
    middle = len(durations) // 2
    median = (
        durations[middle]
        if len(durations) % 2
        else (durations[middle - 1] + durations[middle]) / 2
    )
    return {
        "shot_count": len(slots),
        "cut_density": len(cuts) / max(sum(slot.duration for slot in slots), 1e-6),
        "median_shot_length": median,
        "beat_sync_ratio": synced / len(cuts) if cuts else 0.0,
    }


def planned_rhythm_metrics(
    plan: DirectorPlan,
    music: MusicMap,
) -> dict[str, float | int]:
    return rhythm_metrics(
        _slots(plan, music),
        music,
        tolerance_sec=plan.editing_style.beat_tolerance_sec,
    )


def role_source_duration_requirements(
    plan: DirectorPlan, music: MusicMap
) -> dict[str, float]:
    """Return the minimum usable source duration for each review role."""
    requirements: dict[str, float] = {}
    for slot in _slots(plan, music):
        requirements[slot.role] = max(
            requirements.get(slot.role, 0.0), slot.duration
        )
    return requirements


def _fit_slots_to_unique_coverage(
    slots: list[_Slot],
    *,
    music: MusicMap,
    candidates_by_role: dict[str, list[RankedCandidate]],
    row_by_id: dict[str, sqlite3.Row],
    max_shot_length: float,
) -> list[_Slot]:
    """Keep the beat-locked cut density; only intervene if reuse can't cover it.

    Beam search now reuses shots on non-adjacent beats (see ``MIN_REPEAT_GAP``),
    so a thin pool no longer forces cuts to be merged into long shots — that
    merge is exactly what turned a 33-cut plan into an 11-shot slideshow.  With
    reuse available, any cut count is fillable as long as the pool can sustain
    the repeat gap, so the common case returns the slots untouched.

    Only a genuinely tiny pool (fewer distinct shots than the repeat gap needs)
    still requires dropping cuts, and even then a merged shot may never exceed
    ``max_shot_length`` — rhythm is preserved over shot variety.
    """
    unique_ids = {
        item.shot_id
        for values in candidates_by_role.values()
        for item in values
        if item.shot_id in row_by_id
    }
    fitted = list(slots)
    if len(unique_ids) >= MIN_REPEAT_GAP + 1:
        return fitted
    anchors = [*music.beats, *music.impact_points]
    target_count = max(1, len(unique_ids))
    while len(fitted) > target_count:
        choices: list[tuple[float, float, int, _Slot]] = []
        for index in range(len(fitted) - 1):
            left, right = fitted[index], fitted[index + 1]
            if left.role != right.role:
                continue
            duration = left.duration + right.duration
            # Never let a merge produce a long, rhythm-breaking shot.
            if duration > max_shot_length + 1e-6:
                continue
            pool = candidates_by_role.get(left.role, [])
            if not any(
                (
                    row := row_by_id.get(candidate.shot_id)
                ) is not None
                and float(row["end_sec"] - row["start_sec"]) + 1e-6 >= duration
                for candidate in pool
            ):
                continue
            boundary = right.start
            distance = (
                min(abs(boundary - anchor) for anchor in anchors)
                if anchors else 1.0
            )
            merged = _Slot(
                role=left.role,
                start=left.start,
                duration=duration,
                energy=(left.energy * left.duration + right.energy * right.duration)
                / duration,
                intent=left.intent,
            )
            # Prefer removing a cut far from musical anchors; for a tie, avoid
            # creating an unnecessarily long source requirement.
            choices.append((-distance, duration, index, merged))
        if not choices:
            break
        _, _, index, merged = min(choices)
        fitted[index : index + 2] = [merged]
    return fitted


def _transition(
    previous: sqlite3.Row | None,
    current: sqlite3.Row,
    style: EditingStyleProfile,
    *,
    previous_reference_direction: str | None = None,
    current_reference_direction: str | None = None,
    cut_kind: str = "carry",
) -> float:
    if previous is None:
        return 0.5
    score = 0.0
    score += 0.22 if (
        previous["character"] and previous["character"] == current["character"]
    ) else 0.1
    previous_scale = previous["shot_scale"] if previous["shot_scale"] is not None else 0.5
    current_scale = current["shot_scale"] if current["shot_scale"] is not None else 0.5
    scale_delta = abs(previous_scale - current_scale)
    desired_scale = (
        max(0.35, style.scale_contrast_target)
        if cut_kind == "impact" else style.scale_contrast_target
    )
    score += 0.18 * (1.0 - min(1.0, abs(scale_delta - desired_scale)))
    motion_changed = previous["motion_dir"] != current["motion_dir"]
    if previous_reference_direction and current_reference_direction:
        desired_change = previous_reference_direction != current_reference_direction
    else:
        desired_change = style.motion_change_ratio >= 0.5
    score += 0.16 if motion_changed == desired_change else 0.04
    previous_energy = previous["visual_energy"] or 0.5
    current_energy = current["visual_energy"] or 0.5
    score += 0.11 * (1.0 - min(1.0, abs(previous_energy - current_energy)))
    previous_brightness = previous["brightness"] or 0.5
    current_brightness = current["brightness"] or 0.5
    brightness_delta = abs(previous_brightness - current_brightness)
    score += 0.11 * (
        min(1.0, brightness_delta * 2.0)
        if cut_kind == "impact"
        else 1.0 - min(1.0, brightness_delta)
    )
    try:
        left = json.loads(previous["graphic_features"] or "{}")
        right = json.loads(current["graphic_features"] or "{}")
    except (TypeError, json.JSONDecodeError):
        left, right = {}, {}
    if left and right:
        subject_distance = np.hypot(
            float(left["subject_x"]) - float(right["subject_x"]),
            float(left["subject_y"]) - float(right["subject_y"]),
        )
        luminance_distance = np.hypot(
            float(left["luminance_x"]) - float(right["luminance_x"]),
            float(left["luminance_y"]) - float(right["luminance_y"]),
        )
        edge_left = np.asarray(left["edge_orientation"], dtype=np.float64)
        edge_right = np.asarray(right["edge_orientation"], dtype=np.float64)
        edge_match = float(
            np.dot(edge_left, edge_right)
            / max(np.linalg.norm(edge_left) * np.linalg.norm(edge_right), 1e-6)
        )
        score += 0.1 * (1.0 - min(1.0, subject_distance / 0.7))
        score += 0.06 * (1.0 - min(1.0, luminance_distance / 0.7))
        score += 0.06 * max(0.0, min(1.0, edge_match))
    else:
        score += 0.11
    return score


def _pattern_value(values: list, index: int, total: int):
    if not values:
        return None
    if total <= 1:
        return values[0]
    position = index / (total - 1)
    target = round(position * (len(values) - 1))
    return values[max(0, min(len(values) - 1, target))]


def _reference_fit(
    row: sqlite3.Row,
    *,
    index: int,
    total: int,
    style: EditingStyleProfile,
    scale_range: tuple[float, float],
    motion_range: tuple[float, float],
) -> float:
    """Score the shot against the reference's visual grammar at this slot."""
    scores: list[tuple[float, float]] = []
    target_scale = _pattern_value(style.shot_scale_pattern, index, total)
    if target_scale is not None:
        target_scale = scale_range[0] + target_scale * (
            scale_range[1] - scale_range[0]
        )
        actual_scale = row["shot_scale"] if row["shot_scale"] is not None else 0.5
        scores.append((1.0 - min(1.0, abs(actual_scale - target_scale)), 0.38))
    target_direction = _pattern_value(style.motion_direction_pattern, index, total)
    if target_direction is not None and row["motion_dir"]:
        exact = row["motion_dir"] == target_direction
        both_static = row["motion_dir"] == "static" and target_direction == "static"
        scores.append((1.0 if exact or both_static else 0.25, 0.34))
    target_motion = _pattern_value(style.motion_intensity_pattern, index, total)
    if target_motion is not None:
        rank = float(target_motion)
        if rank <= 0.5:
            target_motion = style.motion_median_target * (0.2 + 1.6 * rank)
        elif rank <= 0.75:
            target_motion = style.motion_median_target + (
                style.motion_p75_target - style.motion_median_target
            ) * ((rank - 0.5) / 0.25)
        else:
            target_motion = style.motion_p75_target * (
                1.0 + 0.8 * ((rank - 0.75) / 0.25)
            )
        actual_motion = max(0.0, float(row["motion_mag"] or 0.0))
        scale = max(
            float(target_motion),
            style.motion_p75_target,
            0.35,
        )
        scores.append((1.0 - min(1.0, abs(actual_motion - target_motion) / scale), 0.28))
    if not scores:
        return 0.5
    weight = sum(value for _, value in scores)
    return sum(score * value for score, value in scores) / weight


def _sequence_novelty(current: sqlite3.Row, selected: list[sqlite3.Row]) -> float:
    if current["embedding"] is None or not selected:
        return 0.5
    vector = np.frombuffer(current["embedding"], dtype=np.float32)
    if not vector.size or not np.isfinite(vector).all():
        return 0.5
    similarities: list[float] = []
    for row in selected:
        if row["embedding"] is None:
            continue
        other = np.frombuffer(row["embedding"], dtype=np.float32)
        if other.size != vector.size or not np.isfinite(other).all():
            continue
        similarities.append(
            float(
                np.dot(vector, other)
                / (np.linalg.norm(vector) * np.linalg.norm(other) + 1e-8)
            )
        )
    if not similarities:
        return 0.5
    return max(0.0, min(1.0, 1.0 - max(similarities)))


_KEYFRAME_POSITION = (0.15, 0.325, 0.5, 0.675, 0.85)


def _semantic_tokens(row: sqlite3.Row) -> set[str]:
    return {
        token
        for value in (row["action"], row["emotion"], row["tags"])
        for token in re.split(r"[^a-zA-Z0-9_]+", str(value or "").lower())
        if token
    }


def _source_window(
    row: sqlite3.Row,
    duration: float,
    *,
    intent: str,
) -> tuple[float, float, SourceSelection]:
    """Choose an intent-aware range around the scored representative frame.

    Shot-level analysis does not yet expose frame-exact action landmarks, so
    this remains a bounded semantic phase estimate.  The estimate and its
    confidence travel in EditSpec and can be replaced by measured landmarks
    without changing compiler behavior.
    """
    start = float(row["start_sec"])
    end = float(row["end_sec"])
    keyframe = Path(row["keyframe"] or "").stem
    match = re.search(r"_c([0-4])$", keyframe)
    position = _KEYFRAME_POSITION[int(match.group(1))] if match else 0.5
    tokens = _semantic_tokens(row)
    phase = "representative"
    phase_position = position
    evidence = [
        f"scored_keyframe:{position:.3f}",
        *[f"semantic:{token}" for token in sorted(tokens)[:12]],
    ]
    confidence = 0.62 if match else 0.48
    if intent == "impact" or tokens & {
        "impact", "hit", "punch", "kick", "explosion", "slash",
    }:
        phase, phase_position = "impact", max(position, 0.58)
        evidence.append("impact_semantics")
        confidence = max(confidence, 0.7)
    elif intent == "anticipation":
        phase, phase_position = "anticipation", min(position, 0.38)
        evidence.append("anticipation_intent")
        confidence = max(confidence, 0.62)
    elif intent in {"carry", "reverse"} or tokens & {
        "sword", "swing", "running", "run", "attack", "action",
    }:
        phase, phase_position = "action", max(0.42, min(position, 0.62))
        evidence.append("action_semantics")
        confidence = max(confidence, 0.66)
    elif intent == "hold" or tokens & {
        "reaction", "crying", "smile", "surprised", "looking",
    }:
        phase, phase_position = "reaction", max(0.42, min(position, 0.56))
        evidence.append("reaction_semantics")
        confidence = max(confidence, 0.64)
    elif intent == "settle":
        phase, phase_position = "settle", max(position, 0.68)
        evidence.append("settle_intent")
        confidence = max(confidence, 0.6)
    representative = start + (end - start) * phase_position
    source_in = max(start, min(representative - duration / 2.0, end - duration))
    anchor = max(source_in, min(representative, source_in + duration))
    return (
        source_in,
        source_in + duration,
        SourceSelection(
            phase=phase,
            anchor_sec=anchor,
            confidence=confidence,
            evidence=evidence,
        ),
    )


def _cut_relation(
    previous: sqlite3.Row | None,
    current: sqlite3.Row,
    *,
    intent: str,
) -> CutRelation:
    if previous is None:
        return CutRelation(
            kind="establish",
            motivation="建立本段主体与视觉空间",
            confidence=0.9,
            matched_features=["sequence_start"],
        )
    previous_tokens = _semantic_tokens(previous)
    current_tokens = _semantic_tokens(current)
    shared = sorted(previous_tokens & current_tokens)
    same_action = bool(
        previous["action"]
        and current["action"]
        and previous["action"] == current["action"]
    )
    same_direction = bool(
        previous["motion_dir"]
        and previous["motion_dir"] == current["motion_dir"]
        and previous["motion_dir"] != "static"
    )
    scale_delta = abs(
        float(previous["shot_scale"] or 0.5)
        - float(current["shot_scale"] or 0.5)
    )
    brightness_delta = abs(
        float(previous["brightness"] or 0.5)
        - float(current["brightness"] or 0.5)
    )
    if intent in {"carry", "reverse"} and (same_action or same_direction):
        features = [
            name for name, enabled in (
                ("action", same_action), ("motion_direction", same_direction)
            ) if enabled
        ]
        return CutRelation(
            kind="match_action",
            motivation="沿动作或运动方向跨切，保持动势连续",
            confidence=0.82 if len(features) == 2 else 0.7,
            matched_features=features,
        )
    if scale_delta <= 0.1 and brightness_delta <= 0.12:
        return CutRelation(
            kind="graphic_match",
            motivation="利用相近景别与亮度结构建立图形匹配",
            confidence=0.68,
            matched_features=["shot_scale", "brightness"],
        )
    if intent == "impact" or brightness_delta >= 0.38 or scale_delta >= 0.42:
        return CutRelation(
            kind="contrast",
            motivation="通过景别、明暗或能量反差强化冲击",
            confidence=0.76,
            matched_features=[
                name for name, enabled in (
                    ("shot_scale", scale_delta >= 0.42),
                    ("brightness", brightness_delta >= 0.38),
                    ("impact_intent", intent == "impact"),
                ) if enabled
            ],
        )
    if current["emotion"] and current["emotion"] != previous["emotion"]:
        return CutRelation(
            kind="reaction",
            motivation="以情绪变化回应上一镜的信息或动作",
            confidence=0.64,
            matched_features=["emotion_change"],
        )
    if previous["character"] != current["character"] and shared:
        return CutRelation(
            kind="parallel",
            motivation="不同主体以共享语义形成平行蒙太奇",
            confidence=0.62,
            matched_features=[f"semantic:{value}" for value in shared[:3]],
        )
    return CutRelation(
        kind="continuation",
        motivation="延续当前人物、场景或能量乐句",
        confidence=0.58,
        matched_features=[f"semantic:{value}" for value in shared[:3]],
    )


def plan_sequence(
    conn: sqlite3.Connection,
    *,
    plan: DirectorPlan,
    music: MusicMap,
    candidates_by_role: dict[str, list[RankedCandidate]],
    canvas: Canvas = Canvas(width=1080, height=1080, aspect="1:1"),
    timebase: Timebase = Timebase(num=24000, den=1001),
    music_asset_id: str | None = None,
    selected_by_role: dict[str, str] | None = None,
    beam_width: int = 32,
) -> EditSpec:
    conn.row_factory = sqlite3.Row
    all_ids = sorted(
        {
            candidate.shot_id
            for candidates in candidates_by_role.values()
            for candidate in candidates
        }
    )
    if not all_ids:
        raise ValueError("Sequence Planner 没有候选")
    rows = conn.execute(
        f"SELECT * FROM shots WHERE id IN ({','.join('?' for _ in all_ids)})",
        all_ids,
    ).fetchall()
    row_by_id = {row["id"]: row for row in rows}
    scales = sorted(float(row["shot_scale"]) for row in rows if row["shot_scale"] is not None)
    motions = sorted(max(0.0, float(row["motion_mag"])) for row in rows if row["motion_mag"] is not None)

    def robust_range(values: list[float], fallback: tuple[float, float]) -> tuple[float, float]:
        if len(values) < 2:
            return fallback
        return (
            float(np.percentile(values, 10)),
            float(np.percentile(values, 90)),
        )

    scale_range = robust_range(scales, (0.2, 0.8))
    motion_range = robust_range(motions, (0.0, 5.0))
    score_by_id = {
        candidate.shot_id: candidate.total
        for candidates in candidates_by_role.values()
        for candidate in candidates
    }
    states = [_State(0.0, [], [], [])]
    slots = _fit_slots_to_unique_coverage(
        _slots(plan, music),
        music=music,
        candidates_by_role=candidates_by_role,
        row_by_id=row_by_id,
        max_shot_length=max(1.2, plan.editing_style.max_shot_length),
    )
    forced_used: set[str] = set()
    reserved_for_role = {
        shot_id: role
        for role, shot_id in (selected_by_role or {}).items()
    }
    global_pool = [
        item for values in candidates_by_role.values() for item in values
    ]
    for slot_index, slot in enumerate(slots):
        pool = candidates_by_role.get(slot.role, [])
        # Guarantee enough distinct candidates to sustain the reuse gap even for
        # a run of same-role slots on a thin character; role-appropriate shots
        # still score higher, the global tail is only feasibility headroom.
        if len({item.shot_id for item in pool}) < MIN_REPEAT_GAP + 1:
            seen = {item.shot_id for item in pool}
            pool = [*pool, *(item for item in global_pool if item.shot_id not in seen)]
        expansions = []
        for state in states:
            prior = state.rows[-1] if state.rows else None
            viable = []
            for candidate in pool:
                forced = (selected_by_role or {}).get(slot.role)
                if forced and slot.role not in forced_used and candidate.shot_id != forced:
                    continue
                reserved_role = reserved_for_role.get(candidate.shot_id)
                if reserved_role is not None and reserved_role != slot.role:
                    continue
                row = row_by_id.get(candidate.shot_id)
                if row is None:
                    continue
                # Reuse is allowed, but never within MIN_REPEAT_GAP clips — a
                # shot must not reappear adjacently or in the same phrase.
                if row["id"] in state.shot_ids[-MIN_REPEAT_GAP:]:
                    continue
                reuse_count = state.shot_ids.count(row["id"])
                source_duration = row["end_sec"] - row["start_sec"]
                # Extremely long "shots" in anime sources are almost always
                # undetected credit/title sequences or static production cards.
                if source_duration > 30.0:
                    continue
                if source_duration + 1e-6 < slot.duration:
                    continue
                # Preserve scarce long sources for later long slots. Without
                # this term, an otherwise attractive 5-second source can be
                # consumed by an early 0.7-second slot and make a valid
                # no-repeat sequence impossible near the ending.
                duration_conservation = min(
                    1.0, slot.duration / max(source_duration, 1e-6)
                )
                energy_fit = 1.0 - abs((row["visual_energy"] or 0.5) - slot.energy)
                reference_fit = _reference_fit(
                    row,
                    index=slot_index,
                    total=len(slots),
                    style=plan.editing_style,
                    scale_range=scale_range,
                    motion_range=motion_range,
                )
                previous_reference_direction = _pattern_value(
                    plan.editing_style.motion_direction_pattern,
                    max(0, slot_index - 1),
                    len(slots),
                )
                current_reference_direction = _pattern_value(
                    plan.editing_style.motion_direction_pattern,
                    slot_index,
                    len(slots),
                )
                cut_kind = (
                    slot.intent
                    if slot.intent in {"impact", "reverse", "carry"}
                    else "impact" if slot.role == "impact"
                    else "carry"
                    if previous_reference_direction == current_reference_direction
                    else "reverse"
                )
                motion = max(0.0, float(row["motion_mag"] or 0.0))
                target_motion = {
                    "hold": plan.editing_style.motion_median_target * 0.2,
                    "establish": plan.editing_style.motion_median_target * 0.5,
                    "carry": plan.editing_style.motion_median_target,
                    "reverse": plan.editing_style.motion_p75_target * 0.8,
                    "impact": plan.editing_style.motion_p75_target * 1.1,
                    "settle": plan.editing_style.motion_median_target * 0.3,
                    "reaction": plan.editing_style.motion_median_target * 0.35,
                    "anticipation": plan.editing_style.motion_median_target * 0.45,
                }.get(slot.intent, plan.editing_style.motion_median_target)
                intent_motion_fit = 1.0 - min(
                    1.0,
                    abs(motion - target_motion)
                    / max(
                        target_motion,
                        plan.editing_style.motion_p75_target * 0.5,
                        0.35,
                    ),
                )
                value = (
                    state.score
                    + 0.25 * score_by_id[row["id"]]
                    + 0.28 * _transition(
                        prior,
                        row,
                        plan.editing_style,
                        previous_reference_direction=previous_reference_direction,
                        current_reference_direction=current_reference_direction,
                        cut_kind=cut_kind,
                    )
                    + 0.12 * energy_fit
                    + 0.27 * reference_fit
                    + 0.08 * _sequence_novelty(row, state.rows)
                    + 0.10 * duration_conservation
                    + 0.12 * intent_motion_fit
                    - REPEAT_PENALTY * reuse_count
                )
                viable.append((value, row))
            viable.sort(key=lambda item: (-item[0], item[1]["id"]))
            alternatives = [row["id"] for _, row in viable[:3]]
            for value, row in viable[: min(12, len(viable))]:
                expansions.append(
                    _State(
                        score=value,
                        shot_ids=[*state.shot_ids, row["id"]],
                        rows=[*state.rows, row],
                        alternatives=[*state.alternatives, alternatives],
                    )
                )
        if not expansions:
            raise ValueError(
                f"slot {slot.role}@{slot.start:.3f}s 无满足时长的候选"
                "（含重复兜底后仍无解）"
            )
        expansions.sort(key=lambda state: (-state.score, state.shot_ids))
        states = expansions[:beam_width]
        if (selected_by_role or {}).get(slot.role):
            forced_used.add(slot.role)
    best = states[0]
    clips = []
    for index, (slot, row, alternatives) in enumerate(
        zip(slots, best.rows, best.alternatives, strict=True)
    ):
        clip_id = "clip-" + stable_hash(
            {
                "project": plan.project_id,
                "plan_revision": plan.revision,
                "slot": index,
                "shot": row["id"],
            }
        )[:16]
        source_in, source_out, source_selection = _source_window(
            row, slot.duration, intent=slot.intent
        )
        previous_row = best.rows[index - 1] if index else None
        clips.append(
            Clip(
                id=clip_id,
                asset_id=row["asset_id"],
                shot_id=row["id"],
                source=SourceRange(in_sec=source_in, out_sec=source_out),
                timeline=TimelinePlacement(
                    in_sec=slot.start, duration_sec=slot.duration, track="V1"
                ),
                role=slot.role,
                framing=Framing(mode="crop"),
                incoming_cut=_cut_relation(
                    previous_row, row, intent=slot.intent
                ),
                source_selection=source_selection,
                decision=Decision(
                    source="rule",
                    confidence=min(1.0, score_by_id[row["id"]]),
                    reasoning=(
                        "beam search: role + energy + continuity + diversity; "
                        f"planner={SEQUENCE_PLANNER_VERSION}"
                    ),
                    alternatives=[
                        value for value in alternatives if value != row["id"]
                    ],
                ),
            )
        )
    revision = (
        conn.execute(
            "SELECT coalesce(max(version),0)+1 FROM edit_specs WHERE project_id=?",
            (plan.project_id,),
        ).fetchone()[0]
    )
    spec = EditSpec(
        id=plan.project_id,
        revision=revision,
        created_from=CreatedFrom(
            director_plan=f"{plan.project_id}:{plan.revision}",
            music_map=music.version,
        ),
        timebase=timebase,
        canvas=canvas,
        clips=clips,
        audio=(
            [
                {
                    "id": "music-main",
                    "asset_id": music_asset_id,
                    "track": "A1",
                    "duration_sec": plan.duration_sec,
                }
            ]
            if music_asset_id else []
        ),
        meta=SpecMeta(
            pipeline_version=SEQUENCE_PLANNER_VERSION,
            model_versions={
                "editing_style_profile": plan.editing_style.version,
            },
        ),
    )
    with conn:
        conn.execute(
            """
            INSERT INTO edit_specs(project_id,version,spec_json,parent_version,created_by)
            VALUES (?,?,?,?,?)
            """,
            (
                plan.project_id,
                revision,
                spec.model_dump_json(by_alias=True),
                revision - 1 if revision > 1 else None,
                "rule",
            ),
        )
    return spec


__all__ = [
    "SEQUENCE_PLANNER_VERSION",
    "canonical_role",
    "plan_sequence",
    "planned_rhythm_metrics",
    "rhythm_metrics",
    "role_source_duration_requirements",
]
