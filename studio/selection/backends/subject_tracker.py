"""Subject tracking backend, refinement-only (REFACTOR.md §7.3).

CoTracker/SAM2 are precision tools for the shortlist a slot has already
narrowed to (top 5-8 candidates); they are never run library-wide. This
module ships:

- ``CoTrackerSubjectTrackerBackend``: real backend, ``available`` only when
  the ``cotracker`` package is importable.
- ``LucasKanadeSubjectTrackerBackend``: dependency-free fallback (classical
  point tracking seeded inside the initial box) used whenever CoTracker
  isn't installed, with a visibly lower confidence ceiling.
"""
from __future__ import annotations

import cv2
import numpy as np

from studio.selection.backends.protocols import BackendStatus, TrackPoint
from studio.selection.schemas import BoundingBox

SUBJECT_TRACKER_VERSION = "subject-tracker-1.0.0"
_MAX_HEURISTIC_CONFIDENCE = 0.55


class CoTrackerSubjectTrackerBackend:
    def __init__(self) -> None:
        fallback: str | None = None
        available = False
        try:
            import cotracker  # noqa: F401

            available = True
        except Exception as exc:  # noqa: BLE001
            fallback = f"{type(exc).__name__}: {exc}"
        self.status = BackendStatus(
            backend="subject_tracker_cotracker",
            available=available,
            version=SUBJECT_TRACKER_VERSION,
            fallback=fallback,
        )

    def track(self, frames: list[np.ndarray], initial_bbox: BoundingBox) -> list[TrackPoint]:
        if not self.status.available:
            raise RuntimeError("CoTrackerSubjectTrackerBackend unavailable; check .status first")
        raise NotImplementedError(
            "wire this to the CoTracker point-tracking API once the package is "
            "provisioned; use LucasKanadeSubjectTrackerBackend until then"
        )


class LucasKanadeSubjectTrackerBackend:
    """Zero-weight fallback: LK optical flow on grid points inside the initial box."""

    def __init__(self) -> None:
        self.status = BackendStatus(
            backend="subject_tracker_lk",
            available=True,
            version=SUBJECT_TRACKER_VERSION,
            fallback="CoTracker not installed; using Lucas-Kanade point tracking",
        )

    def track(
        self, frames: list[np.ndarray], initial_bbox: BoundingBox, *, fps: float = 1.0
    ) -> list[TrackPoint]:
        if not frames:
            return []
        height, width = frames[0].shape[:2]
        x0, y0 = initial_bbox.x * width, initial_bbox.y * height
        w0, h0 = initial_bbox.w * width, initial_bbox.h * height
        grid = np.array(
            [
                [x0 + fx * w0, y0 + fy * h0]
                for fx in (0.25, 0.5, 0.75)
                for fy in (0.25, 0.5, 0.75)
            ],
            dtype=np.float32,
        ).reshape(-1, 1, 2)

        points = [
            TrackPoint(sec=0.0, bbox=initial_bbox, confidence=_MAX_HEURISTIC_CONFIDENCE)
        ]
        prev_gray = cv2.cvtColor(frames[0], cv2.COLOR_BGR2GRAY)
        current = grid
        for index, frame in enumerate(frames[1:], start=1):
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            tracked, status, _ = cv2.calcOpticalFlowPyrLK(
                prev_gray, gray, current, None, winSize=(21, 21), maxLevel=2,
            )
            valid = tracked[status.reshape(-1).astype(bool)] if tracked is not None else np.empty((0, 2))
            if len(valid) == 0:
                break
            xs, ys = valid[:, 0], valid[:, 1]
            bbox = BoundingBox(
                x=max(0.0, float(xs.min()) / width),
                y=max(0.0, float(ys.min()) / height),
                w=max(1e-3, min(1.0, float(xs.max() - xs.min()) / width)),
                h=max(1e-3, min(1.0, float(ys.max() - ys.min()) / height)),
            )
            confidence = _MAX_HEURISTIC_CONFIDENCE * (len(valid) / len(current))
            points.append(TrackPoint(sec=index / fps, bbox=bbox, confidence=confidence))
            prev_gray = gray
            current = tracked
        return points


def create_subject_tracker_backend():
    cotracker = CoTrackerSubjectTrackerBackend()
    if cotracker.status.available:
        return cotracker
    return LucasKanadeSubjectTrackerBackend()


__all__ = [
    "SUBJECT_TRACKER_VERSION",
    "CoTrackerSubjectTrackerBackend",
    "LucasKanadeSubjectTrackerBackend",
    "create_subject_tracker_backend",
]
