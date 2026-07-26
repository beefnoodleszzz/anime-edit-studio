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

SEQUENCE_PLANNER_VERSION = "sequence-planner-1.2.0"
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


def _slots(plan: DirectorPlan, music: MusicMap) -> list[_Slot]:
    values = []
    for section in plan.structure:
        duration = section.end - section.start
        count = max(1, round(duration / section.average_shot_length))
        weights = [
            0.86 if index % 3 == 0 else 1.12 if index % 3 == 1 else 1.02
            for index in range(count)
        ]
        scale = duration / sum(weights)
        cursor = section.start
        for index, weight in enumerate(weights):
            end = section.end if index == count - 1 else cursor + weight * scale
            values.append(
                _Slot(
                    role=canonical_role(section.role),
                    start=cursor,
                    duration=end - cursor,
                    energy=section.energy,
                )
            )
            cursor = end
    return values


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


def _transition(previous: sqlite3.Row | None, current: sqlite3.Row) -> float:
    if previous is None:
        return 0.5
    score = 0.0
    score += 0.35 if (
        previous["character"] and previous["character"] == current["character"]
    ) else 0.15
    previous_scale = previous["shot_scale"] if previous["shot_scale"] is not None else 0.5
    current_scale = current["shot_scale"] if current["shot_scale"] is not None else 0.5
    scale_delta = abs(previous_scale - current_scale)
    score += 0.25 * min(1.0, scale_delta * 2.2)
    if previous["motion_dir"] == current["motion_dir"]:
        score += 0.22
    elif {previous["motion_dir"], current["motion_dir"]} <= {"left", "right"}:
        score += 0.08
    else:
        score += 0.14
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
                    + 0.22 * _transition(prior, row)
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
            model_versions={},
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
    "role_source_duration_requirements",
]
