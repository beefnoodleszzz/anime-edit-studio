"""Per-slot candidate construction and scoring.

Replaces ``studio.selection`` now that footage arrives pre-curated by ID
from anime-shot-library: there is no sub-window search inside a Shot to run
(the shot IS the usable window already), and no technical/portrait/action
CV gate to compute (the library already filtered for that). What is still
this module's job:

- trim each candidate shot to the slot's target duration,
- estimate entry/exit camera-motion direction on demand (cheap,
  deterministic — :mod:`studio.analysis.global_motion`, no learned model),
- score duration fit + identity/series continuity + motion compatibility
  against the slot.

The sequence-level continuity bonus (identity/series/motion drift across
consecutive picks, dedup) stays in
:mod:`studio.planning.global_sequence_planner` — this module only ranks
candidates for one slot in isolation.
"""
from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import cv2

from studio.analysis.global_motion import estimate_global_motion
from studio.planning.schemas import (
    DIRECTION_VECTORS,
    EditabilityProfile,
    MotionDirection,
    ShotWindow,
    SubjectProfile,
    TechnicalProfile,
)
from studio.planning.slots import TimelineSlot

CANDIDATES_VERSION = "candidates-2.0.0"

_INTRINSIC_WEIGHT = 0.35
_DURATION_WEIGHT = 0.35
_MOTION_WEIGHT = 0.15
_IDENTITY_WEIGHT = 0.075
_SERIES_WEIGHT = 0.075

_DIRECTION_ANGLES: list[tuple[float, MotionDirection]] = [
    (0.0, "right"), (45.0, "down-right"), (90.0, "down"), (135.0, "down-left"),
    (180.0, "left"), (225.0, "up-left"), (270.0, "up"), (315.0, "up-right"),
]
_MOTION_MIN_MAGNITUDE = 2.0


@dataclass(frozen=True)
class ScoredWindow:
    window: ShotWindow
    score: float
    components: dict


def _direction_bucket(tx: float, ty: float) -> MotionDirection:
    magnitude = (tx * tx + ty * ty) ** 0.5
    if magnitude < _MOTION_MIN_MAGNITUDE:
        return "none"
    angle = math.degrees(math.atan2(ty, tx)) % 360.0
    return min(
        _DIRECTION_ANGLES,
        key=lambda item: min(abs(angle - item[0]), 360 - abs(angle - item[0])),
    )[1]


def _direction_similarity(a: MotionDirection, b: MotionDirection) -> float:
    if a == "none" or b == "none":
        return 0.75
    va, vb = DIRECTION_VECTORS[a], DIRECTION_VECTORS[b]
    cosine = va[0] * vb[0] + va[1] * vb[1]
    return max(0.0, min(1.0, (cosine + 1.0) / 2.0))


_MOTION_ESTIMATE_MAX_WIDTH = 480


def _downscaled_gray(frame) -> "cv2.typing.MatLike":
    """Motion estimation only needs to resolve a coarse 9-way direction, not
    exact pixel displacement — downscaling before the optical-flow/ECC pass
    is a large, safe speedup (ECC in particular scales with pixel count and
    can take seconds per call at full HD)."""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    height, width = gray.shape
    if width <= _MOTION_ESTIMATE_MAX_WIDTH:
        return gray
    scale = _MOTION_ESTIMATE_MAX_WIDTH / width
    return cv2.resize(gray, (_MOTION_ESTIMATE_MAX_WIDTH, max(1, round(height * scale))), interpolation=cv2.INTER_AREA)


@lru_cache(maxsize=2048)
def _estimate_edge_motion(media: str, shot_start_sec: float, shot_end_sec: float) -> tuple[MotionDirection, MotionDirection]:
    """Sample a frame pair near each edge of the *shot itself* (its own
    catalog start/end, not whatever window a particular slot happens to
    trim it to) and bucket the measured camera-like motion into a
    direction, so the beam can reward matching entry/exit motion across a
    cut without any learned backend.

    Keying the cache on the shot's own fixed boundaries — rather than the
    per-slot trimmed window — is deliberate: a shot's overall pan direction
    is a property of the shot, not of which slot is asking, and caching on
    the trim window meant this recomputed via real video decode for every
    (shot, slot) pair instead of once per shot."""
    capture = cv2.VideoCapture(media)
    try:
        fps = capture.get(cv2.CAP_PROP_FPS) or 24.0
        gap = max(1, round(0.2 * fps))

        def _direction_at(center_sec: float) -> MotionDirection:
            frame_index = max(0, round(center_sec * fps))
            capture.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
            ok1, frame1 = capture.read()
            capture.set(cv2.CAP_PROP_POS_FRAMES, frame_index + gap)
            ok2, frame2 = capture.read()
            if not (ok1 and ok2):
                return "none"
            gray1 = _downscaled_gray(frame1)
            gray2 = _downscaled_gray(frame2)
            estimate = estimate_global_motion(gray1, gray2)
            if estimate.confidence < 0.3:
                return "none"
            return _direction_bucket(estimate.tx, estimate.ty)

        gap_sec = gap / fps
        entry = _direction_at(shot_start_sec)
        exit_ = _direction_at(max(shot_start_sec, shot_end_sec - gap_sec))
        return entry, exit_
    except cv2.error:
        return "none", "none"
    finally:
        capture.release()


def _trim_to_duration(shot_start: float, shot_end: float, target_duration: float) -> tuple[float, float] | None:
    """Center-crop the curated shot to the slot's target duration.

    A shot shorter than the target cannot back it: the timeline placement
    always reserves the full ``target_duration`` regardless of the source's
    own ``[in,out]`` (Resolve's adapter treats the timeline duration as
    authoritative and imports that many source frames), so a shorter
    source would silently read past the selected shot into whatever
    footage follows it on disk. Such a shot is not a usable candidate for
    this slot at all — returns ``None`` rather than a too-short window."""
    shot_duration = shot_end - shot_start
    if shot_duration < target_duration:
        return None
    if shot_duration == target_duration:
        return shot_start, shot_end
    slack = shot_duration - target_duration
    trimmed_start = shot_start + slack / 2.0
    return trimmed_start, trimmed_start + target_duration


def _score(window: ShotWindow, slot: TimelineSlot, *, intrinsic: float) -> tuple[float, dict[str, float]]:
    duration_fit = 1.0 - min(
        1.0, abs(window.duration_sec - slot.duration_sec) / max(slot.duration_sec, 1e-6)
    )
    motion_compat = 0.5 * _direction_similarity(
        window.editability.entry_motion, slot.entry_direction
    ) + 0.5 * _direction_similarity(window.editability.exit_motion, slot.exit_direction)
    identity_fit = (
        1.0
        if slot.required_identity is None
        or window.subject.identity_cluster is None
        or slot.required_identity == window.subject.identity_cluster
        else 0.0
    )
    series_fit = (
        1.0
        if slot.required_series_scope is None
        or window.subject.series_scope is None
        or slot.required_series_scope == window.subject.series_scope
        else 0.0
    )
    components = {
        "intrinsic": intrinsic,
        "duration_fit": duration_fit,
        "motion_compatibility": motion_compat,
        "identity_fit": identity_fit,
        "series_scope_fit": series_fit,
    }
    total = (
        _INTRINSIC_WEIGHT * intrinsic
        + _DURATION_WEIGHT * duration_fit
        + _MOTION_WEIGHT * motion_compat
        + _IDENTITY_WEIGHT * identity_fit
        + _SERIES_WEIGHT * series_fit
    )
    return max(0.0, min(1.0, total)), components


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
    limit: int = 30,
) -> list[ScoredWindow]:
    """Build and score one candidate per already-curated shot for this slot.

    Every shot in ``shot_ids`` is assumed to already have passed quality/
    character curation in anime-shot-library, so ``technical.passed`` is
    always true here — there is no hard gate left to run locally."""
    if not shot_ids:
        return []
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
        shot_start, shot_end = float(row["start_sec"]), float(row["end_sec"])
        trimmed = _trim_to_duration(shot_start, shot_end, slot.duration_sec)
        if trimmed is None:
            continue
        start_sec, end_sec = trimmed
        entry_motion, exit_motion = _estimate_edge_motion(str(media), shot_start, shot_end)
        window = ShotWindow(
            id=f"{row['id']}:trim:{start_sec:.3f}-{end_sec:.3f}",
            shot_id=row["id"], asset_id=row["asset_id"],
            start_sec=start_sec, end_sec=end_sec, anchor_sec=(start_sec + end_sec) / 2.0,
            technical=TechnicalProfile(passed=True),
            subject=SubjectProfile(
                identity_cluster=row["character"], series_scope=row["series"],
            ),
            editability=EditabilityProfile(entry_motion=entry_motion, exit_motion=exit_motion),
        )
        score, components = _score(window, slot, intrinsic=0.7)
        scored.append(ScoredWindow(window=window, score=score, components=components))

    scored.sort(key=lambda item: (-item.score, item.window.id))
    return scored[:limit]


__all__ = ["CANDIDATES_VERSION", "ScoredWindow", "candidates_for_slot"]
