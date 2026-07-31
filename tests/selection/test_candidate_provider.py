"""ShotWindow build + cache round-trip (REFACTOR.md §8.4, §21)."""
from __future__ import annotations

import cv2
import numpy as np

from studio.core.database import connect
from studio.selection.backends.protocols import BackendStatus
from studio.selection.candidate_provider import get_or_build_shot_windows, shortlist_candidates
from studio.selection.schemas import ShotWindow, TechnicalProfile

_SIZE = (160, 120)


class _NoFaceBackend:
    status = BackendStatus(backend="fake", available=False)

    def detect(self, frame):
        return []


def _write_video(path, frames, fps=10.0):
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, _SIZE)
    for frame in frames:
        writer.write(frame)
    writer.release()


def test_get_or_build_generates_and_caches(tmp_path):
    conn = connect(tmp_path / "engine.v2.sqlite")
    with conn:
        conn.execute(
            "INSERT INTO assets(id,path,sha256) VALUES (?,?,?)", ("a1", "/tmp/a.mp4", "hash1")
        )
        conn.execute(
            "INSERT INTO shots(id,asset_id,idx,start_sec,end_sec) VALUES (?,?,?,?,?)",
            ("s1", "a1", 0, 0.0, 1.0),
        )

    frames = [np.full((_SIZE[1], _SIZE[0], 3), 130, dtype=np.uint8) for _ in range(15)]
    video = tmp_path / "shot.mp4"
    _write_video(video, frames)

    first = get_or_build_shot_windows(
        conn, shot_id="s1", asset_id="a1", media=video,
        shot_start_sec=0.0, shot_end_sec=1.0, face_backend=_NoFaceBackend(),
    )
    assert first
    count = conn.execute("SELECT COUNT(*) FROM shot_windows WHERE shot_id='s1'").fetchone()[0]
    assert count == len(first)

    second = get_or_build_shot_windows(
        conn, shot_id="s1", asset_id="a1", media=video,
        shot_start_sec=0.0, shot_end_sec=1.0, face_backend=_NoFaceBackend(),
    )
    assert [w.id for w in second] == [w.id for w in first]
    # Cache hit must not duplicate rows.
    count_again = conn.execute("SELECT COUNT(*) FROM shot_windows WHERE shot_id='s1'").fetchone()[0]
    assert count_again == count
    conn.close()


def _window(window_id, *, passed, score):
    return ShotWindow(
        id=window_id, shot_id="s1", asset_id="a1",
        start_sec=0.0, end_sec=1.0, anchor_sec=0.5,
        technical=TechnicalProfile(passed=passed),
        portrait={"portrait_score": score},
    )


def test_shortlist_drops_technical_failures_and_ranks_by_score():
    windows = [
        _window("w-fail", passed=False, score=0.99),
        _window("w-low", passed=True, score=0.2),
        _window("w-high", passed=True, score=0.8),
    ]
    shortlisted = shortlist_candidates(windows, limit=5)
    assert [w.id for w in shortlisted] == ["w-high", "w-low"]
