"""Cache/generate ShotWindow candidates and coarsely shortlist them (REFACTOR.md §8.4, §21).

Execution budget (REFACTOR.md §21): the whole library gets the cheap
temporal-window generation once (cached by ``shot_windows`` keyed on
shot_id + analysis_version); only the coarse shortlist per slot (20-40
windows) is worth spending a heavier per-window pass on later, and only the
top 5-8 of that ever sees a genuinely heavy backend (CoTracker/SAM2/DOVER —
wired in by callers, not this module).
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Callable

from studio.selection.backends.protocols import AnimeFaceBackend
from studio.selection.schemas import MotionDirection, ShotWindow
from studio.selection.temporal_windows import TEMPORAL_WINDOWS_VERSION, build_shot_window, generate_spans

CANDIDATE_PROVIDER_VERSION = "candidate-provider-1.0.0"
DEFAULT_SHORTLIST_SIZE = 30


def _row_to_window(row: sqlite3.Row) -> ShotWindow:
    return ShotWindow.model_validate_json(row["features_json"])


def _insert_window(conn: sqlite3.Connection, window: ShotWindow, analysis_version: str) -> None:
    conn.execute(
        """
        INSERT INTO shot_windows(
          id,shot_id,asset_id,start_sec,end_sec,anchor_sec,kind,
          technical_pass,features_json,analysis_version
        ) VALUES (?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(shot_id,start_sec,end_sec,kind,analysis_version) DO UPDATE SET
          technical_pass=excluded.technical_pass,
          features_json=excluded.features_json
        """,
        (
            window.id, window.shot_id, window.asset_id,
            window.start_sec, window.end_sec, window.anchor_sec, window.kind,
            int(window.technical.passed), window.model_dump_json(), analysis_version,
        ),
    )


def get_or_build_shot_windows(
    conn: sqlite3.Connection,
    *,
    shot_id: str,
    asset_id: str,
    media: Path,
    shot_start_sec: float,
    shot_end_sec: float,
    face_backend: AnimeFaceBackend,
    target_durations: list[float] | None = None,
    entry_motion: MotionDirection = "none",
    exit_motion: MotionDirection = "none",
    preferred_entry: MotionDirection = "none",
    preferred_exit: MotionDirection = "none",
    analysis_version: str = TEMPORAL_WINDOWS_VERSION,
) -> list[ShotWindow]:
    conn.row_factory = sqlite3.Row
    cached = conn.execute(
        "SELECT * FROM shot_windows WHERE shot_id=? AND analysis_version=?",
        (shot_id, analysis_version),
    ).fetchall()
    if cached:
        return [_row_to_window(row) for row in cached]

    spans = generate_spans(
        media, start_sec=shot_start_sec, end_sec=shot_end_sec, face_backend=face_backend,
        target_durations=target_durations,
    )
    windows = [
        build_shot_window(
            shot_id, asset_id, media, span,
            face_backend=face_backend,
            shot_start_sec=shot_start_sec, shot_end_sec=shot_end_sec,
            entry_motion=entry_motion, exit_motion=exit_motion,
            preferred_entry=preferred_entry, preferred_exit=preferred_exit,
        )
        for span in spans
    ]
    with conn:
        for window in windows:
            _insert_window(conn, window, analysis_version)
    return windows


def shortlist_candidates(
    windows: list[ShotWindow],
    *,
    limit: int = DEFAULT_SHORTLIST_SIZE,
    score: Callable[[ShotWindow], float] | None = None,
) -> list[ShotWindow]:
    """Hard-gate then coarsely rank; REFACTOR.md §8.4: a technical failure
    can never be rescued by a high soft score, so it is filtered first."""
    survivors = [window for window in windows if window.technical.passed]
    if score is None:
        score = lambda window: max(window.portrait.portrait_score, window.action.action_score)  # noqa: E731
    survivors.sort(key=lambda window: (-score(window), window.id))
    return survivors[:limit]


__all__ = [
    "CANDIDATE_PROVIDER_VERSION",
    "DEFAULT_SHORTLIST_SIZE",
    "get_or_build_shot_windows",
    "shortlist_candidates",
]
