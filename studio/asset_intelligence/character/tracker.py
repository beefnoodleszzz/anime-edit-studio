"""Deterministic subject-box tracking for delivery reframing.

This is the explicit fallback after Resolve SmartReframe/MagicMask were
visually disproved.  It produces analysis data only; execution keyframes remain
gated until the Fusion Transform Recipe is accepted.
"""
from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from studio.asset_intelligence.visual.analyzer import VisualAnalyzer
from studio.core.contracts import SubjectBox

TRACKING_VERSION = "subject-track-1.1.0-square"


@dataclass(frozen=True)
class TrackPoint:
    sec: float
    box: SubjectBox
    pan_x: float
    pan_y: float
    zoom: float

    def to_dict(self) -> dict:
        return {
            "sec": self.sec,
            "box": self.box.model_dump(mode="json"),
            "pan_x": self.pan_x,
            "pan_y": self.pan_y,
            "zoom": self.zoom,
        }


class SubjectTracker:
    def __init__(self, *, sample_fps: float = 8.0, smoothing: float = 0.28):
        if sample_fps <= 0 or not 0 < smoothing <= 1:
            raise ValueError("invalid tracker parameters")
        self.sample_fps = sample_fps
        self.smoothing = smoothing

    def track(
        self,
        media: Path,
        *,
        start_sec: float,
        end_sec: float,
        target_aspect: float = 1.0,
    ) -> list[TrackPoint]:
        if end_sec <= start_sec:
            raise ValueError("end_sec 必须大于 start_sec")
        capture = cv2.VideoCapture(str(media))
        if not capture.isOpened():
            raise ValueError(f"无法打开媒体: {media}")
        times = np.arange(start_sec, end_sec, 1 / self.sample_fps)
        raw: list[tuple[float, SubjectBox, int, int]] = []
        try:
            for sec in times:
                capture.set(cv2.CAP_PROP_POS_MSEC, float(sec) * 1000)
                ok, frame = capture.read()
                if not ok:
                    continue
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
                box = VisualAnalyzer._subject_box(gray, hsv)
                raw.append((float(sec - start_sec), box, frame.shape[1], frame.shape[0]))
        finally:
            capture.release()
        if not raw:
            return []
        smoothed = self._smooth(raw)
        return [
            self._to_reframe(sec, box, width, height, target_aspect)
            for sec, box, width, height in smoothed
        ]

    def _smooth(
        self, raw: list[tuple[float, SubjectBox, int, int]]
    ) -> list[tuple[float, SubjectBox, int, int]]:
        output = []
        state = None
        for sec, box, width, height in raw:
            vector = np.array([box.x, box.y, box.width, box.height], dtype=np.float64)
            if state is None:
                state = vector
            else:
                previous_center = state[:2] + state[2:] / 2
                center = vector[:2] + vector[2:] / 2
                distance = float(np.linalg.norm(center - previous_center))
                # Reject implausible one-sample jumps; anime flashes/cuts often
                # produce saliency boxes on the wrong side for a single frame.
                weight = self.smoothing * (0.25 if distance > 0.35 else 1.0)
                state = (1 - weight) * state + weight * vector
            state[0] = np.clip(state[0], 0, 1 - state[2])
            state[1] = np.clip(state[1], 0, 1 - state[3])
            smooth_box = SubjectBox(
                x=float(state[0]), y=float(state[1]),
                width=float(state[2]), height=float(state[3]),
                confidence=box.confidence,
            )
            output.append((sec, smooth_box, width, height))
        return output

    @staticmethod
    def _to_reframe(
        sec: float,
        box: SubjectBox,
        source_width: int,
        source_height: int,
        target_aspect: float,
    ) -> TrackPoint:
        source_aspect = source_width / source_height
        if source_aspect > target_aspect:
            crop_width = target_aspect / source_aspect
            crop_height = 1.0
        else:
            crop_width = 1.0
            crop_height = source_aspect / target_aspect
        cx = box.x + box.width / 2
        cy = box.y + box.height / 2
        crop_x = min(max(cx - crop_width / 2, 0), 1 - crop_width)
        crop_y = min(max(cy - crop_height / 2, 0), 1 - crop_height)
        pan_x = 0.0 if crop_width == 1 else 2 * (crop_x / (1 - crop_width)) - 1
        pan_y = 0.0 if crop_height == 1 else 2 * (crop_y / (1 - crop_height)) - 1
        return TrackPoint(
            sec=round(sec, 6),
            box=box,
            pan_x=float(np.clip(pan_x, -1, 1)),
            pan_y=float(np.clip(pan_y, -1, 1)),
            zoom=max(1.0, 1.0 / crop_width, 1.0 / crop_height),
        )


def track_shot(
    conn: sqlite3.Connection,
    shot_id: str,
    *,
    tracker: SubjectTracker | None = None,
) -> dict:
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        """
        SELECT s.id,s.start_sec,s.end_sec,a.path,a.proxy_path
        FROM shots s JOIN assets a ON a.id=s.asset_id WHERE s.id=?
        """,
        (shot_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"shot 不存在: {shot_id}")
    media = next(
        (Path(value) for value in (row["proxy_path"], row["path"]) if value and Path(value).is_file()),
        None,
    )
    if media is None:
        raise FileNotFoundError(f"shot {shot_id} 的媒体不可达")
    tracker = tracker or SubjectTracker()
    points = tracker.track(
        media, start_sec=row["start_sec"], end_sec=row["end_sec"]
    )
    mean_confidence = (
        sum(point.box.confidence for point in points) / len(points) if points else 0.0
    )
    conn.execute(
        """
        INSERT INTO shot_tracks(shot_id,version,sample_fps,boxes_json,mean_confidence)
        VALUES (?,?,?,?,?)
        ON CONFLICT(shot_id,version) DO UPDATE SET
          sample_fps=excluded.sample_fps,boxes_json=excluded.boxes_json,
          mean_confidence=excluded.mean_confidence,
          created_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')
        """,
        (
            shot_id, TRACKING_VERSION, tracker.sample_fps,
            json.dumps([point.to_dict() for point in points], separators=(",", ":")),
            mean_confidence,
        ),
    )
    conn.commit()
    return {
        "shot_id": shot_id,
        "version": TRACKING_VERSION,
        "points": len(points),
        "mean_confidence": mean_confidence,
    }


__all__ = [
    "TRACKING_VERSION",
    "SubjectTracker",
    "TrackPoint",
    "track_shot",
]
