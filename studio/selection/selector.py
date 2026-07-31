"""Bridge from a TimelineSlot to ranked ShotWindow candidates (REFACTOR.md §4, §16).

Owns exactly the "selection" half of the split REFACTOR.md draws: generate/
score ShotWindow candidates for a slot. The Beam Search across the whole
slot sequence (continuity, novelty, dedup) is ``studio.planning``'s job
(``global_sequence_planner.py``) — this module hands it a ranked list per
slot, not a sequence decision.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from studio.editing.retrieval.engine import RetrievalQuery, retrieve
from studio.planning.slots import TimelineSlot
from studio.selection.backends.anime_face import create_anime_face_backend
from studio.selection.candidate_provider import DEFAULT_SHORTLIST_SIZE, get_or_build_shot_windows
from studio.selection.reference_fit import compute_reference_fit
from studio.selection.schemas import ShotWindow
from studio.selection.sequence_scoring import score_window_for_slot

SELECTOR_VERSION = "selector-1.0.0"
DURATION_TOLERANCE_SEC = 0.6


@dataclass(frozen=True)
class ScoredWindow:
    window: ShotWindow
    score: float
    components: dict
    shot_row: sqlite3.Row


def shot_pool(
    conn: sqlite3.Connection,
    *,
    asset_ids: list[str] | None = None,
    character: str | None = None,
    duration_sec: float | None = None,
    limit: int = 100,
) -> list[str]:
    """Coarse, deterministic shot prefilter (reuses the existing hard-gate
    retrieval engine — cleanliness/character/duration — before the much more
    precise per-window technical gate runs)."""
    query = RetrievalQuery(
        asset_ids=asset_ids or [],
        character=character,
        subtitle_allowed=False,
        min_duration_sec=max(0.1, duration_sec - DURATION_TOLERANCE_SEC) if duration_sec else None,
        max_duration_sec=(duration_sec + DURATION_TOLERANCE_SEC) if duration_sec else None,
        limit=max(limit, 100),
    )
    pool = retrieve(conn, query)
    if not pool and duration_sec is not None:
        pool = retrieve(conn, query.model_copy(update={"min_duration_sec": None, "max_duration_sec": None}))
    return pool


def _media_path(row: sqlite3.Row) -> Path | None:
    return next(
        (Path(value) for value in (row["proxy_path"], row["path"]) if value and Path(value).is_file()),
        None,
    )


def candidates_for_slot(
    conn: sqlite3.Connection,
    slot: TimelineSlot,
    shot_ids: list[str],
    *,
    face_backend=None,
    limit: int = DEFAULT_SHORTLIST_SIZE,
) -> list[ScoredWindow]:
    """Generate/cache ShotWindows for each candidate shot and rank them for
    this specific slot. Windows failing the technical gate score 0.0 here
    (REFACTOR.md §8.4: never rescued by a soft score) but are still
    returned so a caller can see why a slot ran dry rather than it just
    silently being empty."""
    if not shot_ids:
        return []
    face_backend = face_backend or create_anime_face_backend()
    conn.row_factory = sqlite3.Row
    placeholders = ",".join("?" for _ in shot_ids)
    rows = conn.execute(
        f"""
        SELECT s.*, a.path AS path, a.proxy_path AS proxy_path
        FROM shots s JOIN assets a ON a.id=s.asset_id
        WHERE s.id IN ({placeholders})
        """,
        shot_ids,
    ).fetchall()

    scored: list[ScoredWindow] = []
    for row in rows:
        media = _media_path(row)
        if media is None:
            continue
        try:
            windows = get_or_build_shot_windows(
                conn,
                shot_id=row["id"], asset_id=row["asset_id"], media=media,
                shot_start_sec=float(row["start_sec"]), shot_end_sec=float(row["end_sec"]),
                face_backend=face_backend,
                entry_motion=slot.entry_direction, exit_motion=slot.exit_direction,
                preferred_entry=slot.entry_direction, preferred_exit=slot.exit_direction,
            )
        except Exception:  # noqa: BLE001
            continue
        for window in windows:
            reference_fit = compute_reference_fit(slot, row, window=window)
            score, components = score_window_for_slot(window, slot, reference_fit=reference_fit)
            scored.append(ScoredWindow(window=window, score=score, components=components, shot_row=row))

    scored.sort(key=lambda item: (-item.score, item.window.id))
    return scored[:limit]


__all__ = ["SELECTOR_VERSION", "ScoredWindow", "candidates_for_slot", "shot_pool"]
