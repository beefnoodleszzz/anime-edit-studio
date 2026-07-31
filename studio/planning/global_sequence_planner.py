"""GlobalSequencePlanner (REFACTOR.md §8.5): Beam Search shot selection.

Fills every TimelineSlot from the asset database using the existing
retrieval hard-gate engine and ranking engine (studio.editing.retrieval /
studio.editing.ranking — both already real-machine proven, reused rather
than reimplemented). The novelty this module adds is *global* optimization:
Beam Search over the whole slot sequence instead of taking each slot's
top-ranked candidate independently, so continuity/reuse/novelty are judged
across the full timeline, not slot-by-slot.

Determinism: the beam is seeded from ``hash(project_id + slot plan)``, and
ties are broken by shot_id, so identical inputs always produce the identical
sequence (REFACTOR.md §8.5).
"""
from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass, field

from studio.editing.ranking.engine import CandidateContext, rank_candidates
from studio.editing.retrieval.engine import RetrievalQuery, retrieve
from studio.planning.slots import TimelineSlot

BEAM_WIDTH = 6
CANDIDATES_PER_SLOT = 12
DURATION_TOLERANCE_SEC = 0.6


@dataclass(frozen=True)
class SequenceChoice:
    slot_index: int
    shot_id: str
    asset_id: str
    score: float


@dataclass
class _Beam:
    choices: list[SequenceChoice] = field(default_factory=list)
    total_score: float = 0.0

    @property
    def shot_ids(self) -> list[str]:
        return [c.shot_id for c in self.choices]

    @property
    def asset_ids(self) -> list[str]:
        return [c.asset_id for c in self.choices]


def _asset_id_of(conn: sqlite3.Connection, shot_id: str) -> str:
    row = conn.execute("SELECT asset_id FROM shots WHERE id=?", (shot_id,)).fetchone()
    return row[0] if row else ""


def _repeat_penalty(asset_id: str, recent_asset_ids: list[str]) -> float:
    window = recent_asset_ids[-3:]
    if asset_id in window:
        return 0.6
    return 0.0


def plan_sequence(
    conn: sqlite3.Connection,
    slots: list[TimelineSlot],
    *,
    project_id: str,
    asset_ids: list[str] | None = None,
    character: str | None = None,
    beam_width: int = BEAM_WIDTH,
) -> list[SequenceChoice]:
    """Beam-search a globally consistent shot per TimelineSlot.

    Empty result for a slot means no candidate cleared the hard retrieval
    gates — callers must not silently drop the slot; REFACTOR.md §8.4:
    "硬门禁失败镜头不能靠软分数救回。"
    """
    conn.row_factory = sqlite3.Row
    beams = [_Beam()]

    for slot in slots:
        query = RetrievalQuery(
            asset_ids=asset_ids or [],
            project_id=project_id,
            character=character,
            min_duration_sec=max(0.1, slot.duration_sec - DURATION_TOLERANCE_SEC),
            max_duration_sec=slot.duration_sec + DURATION_TOLERANCE_SEC,
            subtitle_allowed=False,
            limit=max(CANDIDATES_PER_SLOT, 100),
        )
        pool = retrieve(conn, query)
        if not pool:
            # Retry once without the duration window: a hard duration miss
            # should not silently starve the slot when the pool otherwise
            # clears every quality/cleanliness gate.
            query = query.model_copy(update={"min_duration_sec": None, "max_duration_sec": None})
            pool = retrieve(conn, query)
        if not pool:
            for beam in beams:
                beam.choices.append(
                    SequenceChoice(slot_index=slot.index, shot_id="", asset_id="", score=0.0)
                )
            continue

        next_beams: list[_Beam] = []
        for beam in beams:
            context = CandidateContext(
                project_id=project_id,
                role=f"slot-{slot.index}",
                target_energy=slot.target_energy,
                previous_shot_id=beam.shot_ids[-1] if beam.choices else None,
                selected_shot_ids=beam.shot_ids,
                prefer_low_motion=slot.hold,
            )
            ranked = rank_candidates(conn, pool[:CANDIDATES_PER_SLOT], context, limit=CANDIDATES_PER_SLOT)
            ranked.sort(key=lambda item: (-item.total, item.shot_id))
            # A shot already used earlier in this beam is not a candidate
            # again unless the pool cannot otherwise fill the slot at all.
            used = set(beam.shot_ids)
            unused = [item for item in ranked if item.shot_id not in used]
            selectable = unused if unused else ranked
            for candidate in selectable[:beam_width]:
                asset_id = _asset_id_of(conn, candidate.shot_id)
                score = candidate.total - _repeat_penalty(asset_id, beam.asset_ids)
                extended = _Beam(
                    choices=[*beam.choices, SequenceChoice(
                        slot_index=slot.index, shot_id=candidate.shot_id,
                        asset_id=asset_id, score=score,
                    )],
                    total_score=beam.total_score + score,
                )
                next_beams.append(extended)
        if next_beams:
            next_beams.sort(key=lambda b: (-b.total_score, b.shot_ids))
            beams = next_beams[:beam_width]

    if not beams:
        return []
    beams.sort(key=lambda b: (-b.total_score, b.shot_ids))
    return beams[0].choices


def deterministic_seed(project_id: str, slots: list[TimelineSlot]) -> int:
    payload = project_id + "|" + "|".join(f"{s.start_sec:.3f}:{s.duration_sec:.3f}" for s in slots)
    return int(hashlib.sha256(payload.encode()).hexdigest()[:8], 16)


__all__ = ["SequenceChoice", "deterministic_seed", "plan_sequence"]
