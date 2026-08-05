"""GlobalSequencePlanner: Beam Search over pre-curated shot candidates.

Every shot fed in here already passed selection in anime-shot-library
(external curation by ID); ``studio.planning.candidates`` just trims/scores
each one for a single slot. This module is the only place that judges the
*sequence*: identity/series/motion-direction continuity across the beam,
and rejecting a window that reuses another already-chosen window's source
range.

Determinism: the beam is seeded from ``hash(project_id + slot plan)``, and
ties are broken by window_id, so identical inputs always produce the
identical sequence.
"""
from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass, field

from studio.planning.candidates import ScoredWindow, candidates_for_slot
from studio.planning.schemas import MotionDirection, direction_cosine
from studio.planning.slots import EntryMotion, TimelineSlot

BEAM_WIDTH = 6
CANDIDATES_PER_SLOT = 30
SOURCE_OVERLAP_THRESHOLD = 0.5


@dataclass(frozen=True)
class SequenceChoice:
    slot_index: int
    window_id: str = ""
    shot_id: str = ""
    asset_id: str = ""
    source_in_sec: float = 0.0
    source_out_sec: float = 0.0
    anchor_sec: float = 0.0
    window_kind: str = "generic"
    score: float = 0.0
    score_components: dict = field(default_factory=dict)
    subject_scale: float | None = None
    subject_center: tuple[float, float] | None = None
    entry_motion: MotionDirection = "none"
    exit_motion: MotionDirection = "none"


@dataclass
class _Beam:
    choices: list[SequenceChoice] = field(default_factory=list)
    total_score: float = 0.0
    selected_window_ids: list[str] = field(default_factory=list)
    selected_source_ranges: list[tuple[str, float, float]] = field(default_factory=list)
    selected_shot_ids: list[str] = field(default_factory=list)
    selected_asset_ids: list[str] = field(default_factory=list)
    identity_cluster: str | None = None
    series_scope: str | None = None
    previous_subject_scale: float | None = None
    previous_subject_center: tuple[float, float] | None = None
    previous_color: str | None = None
    previous_exit_motion: MotionDirection = "none"
    energy_history: list[float] = field(default_factory=list)

    @property
    def window_ids(self) -> list[str]:
        return self.selected_window_ids


def _overlap_ratio(a: tuple[str, float, float], b: tuple[str, float, float]) -> float:
    asset_a, in_a, out_a = a
    asset_b, in_b, out_b = b
    if asset_a != asset_b:
        return 0.0
    intersection = max(0.0, min(out_a, out_b) - max(in_a, in_b))
    shortest = min(out_a - in_a, out_b - in_b)
    return intersection / shortest if shortest > 0 else 0.0


def _is_duplicate(candidate: ScoredWindow, beam: _Beam) -> bool:
    if candidate.window.id in beam.selected_window_ids:
        return True
    candidate_range = (candidate.window.asset_id, candidate.window.start_sec, candidate.window.end_sec)
    return any(
        _overlap_ratio(candidate_range, existing) > SOURCE_OVERLAP_THRESHOLD
        for existing in beam.selected_source_ranges
    )


_RELATION_MOTION_WEIGHT = 0.25


def _relation_motion_bonus(entry_motion: MotionDirection, beam: _Beam, *, relation: EntryMotion) -> float:
    """Score how well a candidate's entry motion satisfies the Demo's
    carry/reverse/reset relation for this cut — this is the actual
    mechanism REFACTOR.md's "跨镜头运动连续性" promises, not a flat ±0.05
    same-direction nudge that both under-rewards a good carry match and
    under-penalizes a reverse landing on the same side as the outgoing exit.

    - carry: incoming should continue in the *same* direction as the
      previous clip's exit — reward proportional to cosine similarity.
    - reverse: incoming should push against the previous exit — reward
      proportional to the *negative* cosine (i.e. opposite direction).
    - reset: incoming should settle, not carry momentum — reward a measured
      "none"/low motion entry regardless of what the previous clip did.
    - none: the Demo showed no directional relation here; do not fabricate
      a preference either way.
    """
    if relation == "reset":
        return 0.0 if entry_motion == "none" else -_RELATION_MOTION_WEIGHT * 0.4
    if relation == "none":
        return 0.0
    if beam.previous_exit_motion == "none" or entry_motion == "none":
        return 0.0
    cosine = direction_cosine(beam.previous_exit_motion, entry_motion)
    return _RELATION_MOTION_WEIGHT * (cosine if relation == "carry" else -cosine)


def _continuity_bonus(candidate: ScoredWindow, beam: _Beam, *, relation: EntryMotion) -> float:
    """Reward/penalize how well this candidate follows the beam's most
    recent choice: identity/series/scale continuity plus a relation-aware
    motion-direction match (see ``_relation_motion_bonus``)."""
    if not beam.choices:
        return 0.0
    window = candidate.window
    bonus = 0.0

    identity = window.subject.identity_cluster
    if beam.identity_cluster is not None and identity is not None:
        bonus += 0.05 if identity == beam.identity_cluster else -0.10

    series_scope = window.subject.series_scope
    if beam.series_scope is not None and series_scope is not None and series_scope != beam.series_scope:
        bonus -= 0.20  # cross-work contamination is a stronger signal than identity drift

    if beam.previous_subject_scale is not None and window.subject.subject_scale is not None:
        bonus -= 0.05 * abs(window.subject.subject_scale - beam.previous_subject_scale)

    # Color continuity is tracked on the beam (previous_color) for future use
    # once ShotWindow carries a per-window dominant color; it isn't wired
    # through yet, so it contributes nothing here rather than a fake bonus.

    bonus += _relation_motion_bonus(window.editability.entry_motion, beam, relation=relation)

    recent_asset_ids = beam.selected_asset_ids[-3:]
    if window.asset_id in recent_asset_ids:
        bonus -= 0.15  # same-asset repetition is still a real novelty problem

    return bonus


def plan_sequence(
    conn: sqlite3.Connection,
    slots: list[TimelineSlot],
    *,
    project_id: str,
    available_shot_ids: list[str],
    beam_width: int = BEAM_WIDTH,
) -> list[SequenceChoice]:
    """Beam-search a globally consistent window per TimelineSlot, choosing
    only among ``available_shot_ids`` — the exact shots the caller already
    imported from anime-shot-library for this project. No further pool
    retrieval happens here; curation already happened upstream.

    Empty result for a slot means no candidate was available/usable for
    it — callers must not silently drop the slot.
    """
    conn.row_factory = sqlite3.Row
    beams = [_Beam()]

    for slot in slots:
        candidates = candidates_for_slot(conn, slot, available_shot_ids, limit=CANDIDATES_PER_SLOT)
        candidates = [c for c in candidates if c.window.technical.passed]
        if not candidates:
            for beam in beams:
                beam.choices.append(SequenceChoice(slot_index=slot.index))
                beam.energy_history.append(slot.target_energy)
            continue

        next_beams: list[_Beam] = []
        for beam in beams:
            usable = [c for c in candidates if not _is_duplicate(c, beam)]
            selectable = usable if usable else candidates
            for candidate in selectable[:beam_width]:
                window = candidate.window
                total_score = candidate.score + _continuity_bonus(candidate, beam, relation=slot.entry_motion)
                choice = SequenceChoice(
                    slot_index=slot.index,
                    window_id=window.id, shot_id=window.shot_id, asset_id=window.asset_id,
                    source_in_sec=window.start_sec, source_out_sec=window.end_sec,
                    anchor_sec=window.anchor_sec, window_kind=window.kind,
                    score=total_score, score_components=candidate.components,
                    subject_scale=window.subject.subject_scale, subject_center=window.subject.subject_center,
                    entry_motion=window.editability.entry_motion, exit_motion=window.editability.exit_motion,
                )
                extended = _Beam(
                    choices=[*beam.choices, choice],
                    total_score=beam.total_score + total_score,
                    selected_window_ids=[*beam.selected_window_ids, window.id],
                    selected_source_ranges=[
                        *beam.selected_source_ranges,
                        (window.asset_id, window.start_sec, window.end_sec),
                    ],
                    selected_shot_ids=[*beam.selected_shot_ids, window.shot_id],
                    selected_asset_ids=[*beam.selected_asset_ids, window.asset_id],
                    identity_cluster=window.subject.identity_cluster or beam.identity_cluster,
                    series_scope=window.subject.series_scope or beam.series_scope,
                    previous_subject_scale=window.subject.subject_scale,
                    previous_subject_center=window.subject.subject_center,
                    previous_color=beam.previous_color,
                    previous_exit_motion=window.editability.exit_motion,
                    energy_history=[*beam.energy_history, slot.target_energy],
                )
                next_beams.append(extended)
        if next_beams:
            next_beams.sort(key=lambda b: (-b.total_score, b.selected_window_ids))
            beams = next_beams[:beam_width]

    if not beams:
        return []
    beams.sort(key=lambda b: (-b.total_score, b.selected_window_ids))
    return beams[0].choices


def deterministic_seed(project_id: str, slots: list[TimelineSlot]) -> int:
    payload = project_id + "|" + "|".join(f"{s.start_sec:.3f}:{s.duration_sec:.3f}" for s in slots)
    return int(hashlib.sha256(payload.encode()).hexdigest()[:8], 16)


__all__ = ["SequenceChoice", "deterministic_seed", "plan_sequence"]
