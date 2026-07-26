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
from studio.editspec.schema import (
    Canvas,
    Clip,
    CreatedFrom,
    Decision,
    EditSpec,
    Framing,
    SourceRange,
    SpecMeta,
    Timebase,
    TimelinePlacement,
)

SEQUENCE_PLANNER_VERSION = "sequence-planner-2.0.0"
_ROLE = {
    "buildup": "build",
    "drop": "impact",
}


def canonical_role(role: str) -> str:
    return _ROLE.get(role, role)


@dataclass(frozen=True)
class _Slot:
    role: str
    start: float
    duration: float
    energy: float


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
    impacts = [value for value in music.impact_points if 0 < value < plan.duration_sec]
    candidates = []
    for index, cut in enumerate(cuts):
        beat = min(music.beats, key=lambda value: abs(value - cut))
        impact = min(impacts, key=lambda value: abs(value - cut)) if impacts else None
        if impact is not None and abs(impact - cut) <= abs(beat - cut):
            anchor, priority = impact, profile.impact_snap_priority
        else:
            anchor, priority = beat, 1.0
        candidates.append(
            (
                abs(anchor - cut) / max(priority, 1e-6),
                index,
                float(anchor),
            )
        )
    target = min(len(cuts), round(len(cuts) * profile.beat_sync_target))
    selected = {
        index: anchor
        for _, index, anchor in sorted(candidates)[:target]
    }
    return [selected.get(index, cut) for index, cut in enumerate(cuts)]


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
    # Narrative section boundaries are semantic anchors and must survive even
    # when the reference has a different duration or music structure.
    cuts.extend(section.end for section in plan.structure[:-1])
    minimum = max(0.08, min(profile.min_shot_length * 0.72, 0.32))
    boundaries = _dedupe_boundaries(
        cuts,
        duration=plan.duration_sec,
        minimum=minimum,
    )
    values = []
    for start, end in zip(boundaries, boundaries[1:]):
        section = _section_at(plan, (start + end) / 2)
        values.append(
            _Slot(
                role=canonical_role(section.role),
                start=start,
                duration=end - start,
                energy=section.energy,
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


def _transition(
    previous: sqlite3.Row | None,
    current: sqlite3.Row,
    style: EditingStyleProfile,
) -> float:
    if previous is None:
        return 0.5
    score = 0.0
    score += 0.35 if (
        previous["character"] and previous["character"] == current["character"]
    ) else 0.15
    previous_scale = previous["shot_scale"] if previous["shot_scale"] is not None else 0.5
    current_scale = current["shot_scale"] if current["shot_scale"] is not None else 0.5
    scale_delta = abs(previous_scale - current_scale)
    score += 0.25 * (
        1.0 - min(1.0, abs(scale_delta - style.scale_contrast_target))
    )
    motion_changed = previous["motion_dir"] != current["motion_dir"]
    desired_change = style.motion_change_ratio >= 0.5
    score += 0.22 if motion_changed == desired_change else 0.08
    previous_energy = previous["visual_energy"] or 0.5
    current_energy = current["visual_energy"] or 0.5
    score += 0.18 * (1.0 - min(1.0, abs(previous_energy - current_energy)))
    return score


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


def _source_window(row: sqlite3.Row, duration: float) -> tuple[float, float]:
    """Center the used range on the exact representative frame that was scored."""
    start = float(row["start_sec"])
    end = float(row["end_sec"])
    keyframe = Path(row["keyframe"] or "").stem
    match = re.search(r"_c([0-4])$", keyframe)
    position = _KEYFRAME_POSITION[int(match.group(1))] if match else 0.5
    representative = start + (end - start) * position
    source_in = max(start, min(representative - duration / 2.0, end - duration))
    return source_in, source_in + duration


def plan_sequence(
    conn: sqlite3.Connection,
    *,
    plan: DirectorPlan,
    music: MusicMap,
    candidates_by_role: dict[str, list[RankedCandidate]],
    canvas: Canvas = Canvas(width=1080, height=1350, aspect="4:5"),
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
    score_by_id = {
        candidate.shot_id: candidate.total
        for candidates in candidates_by_role.values()
        for candidate in candidates
    }
    states = [_State(0.0, [], [], [])]
    slots = _slots(plan, music)
    forced_used: set[str] = set()
    reserved_for_role = {
        shot_id: role
        for role, shot_id in (selected_by_role or {}).items()
    }
    for slot in slots:
        pool = candidates_by_role.get(slot.role, [])
        if not pool:
            pool = [item for values in candidates_by_role.values() for item in values]
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
                if row is None or row["id"] in state.shot_ids:
                    continue
                source_duration = row["end_sec"] - row["start_sec"]
                if source_duration + 1e-6 < slot.duration:
                    continue
                energy_fit = 1.0 - abs((row["visual_energy"] or 0.5) - slot.energy)
                value = (
                    state.score
                    + 0.5 * score_by_id[row["id"]]
                    + 0.22 * _transition(prior, row, plan.editing_style)
                    + 0.18 * energy_fit
                    + 0.1 * _sequence_novelty(row, state.rows)
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
                f"slot {slot.role}@{slot.start:.3f}s 无满足时长且不重复的候选"
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
        source_in, source_out = _source_window(row, slot.duration)
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
