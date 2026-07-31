"""ShotWindow generation from coarse portrait/action spans (REFACTOR.md §8.3)."""
from __future__ import annotations

import cv2
import numpy as np

from studio.selection.backends.protocols import BackendStatus, FaceDetection
from studio.selection.schemas import BoundingBox
from studio.selection.temporal_windows import action_spans, generate_spans, portrait_spans

_SIZE = (160, 120)


class _BrightnessGatedFaceBackend:
    """Frontal iff the sampled frame's mean brightness crosses a threshold —
    lets a test drive turn_to_camera / direct_gaze deterministically from a
    plain synthetic video without a real detector."""

    def __init__(self, threshold: float = 128.0):
        self.status = BackendStatus(backend="fake", available=True, version="test")
        self.threshold = threshold

    def detect(self, frame):
        frontal = float(np.mean(frame)) >= self.threshold
        return [
            FaceDetection(
                bbox=BoundingBox(x=0.3, y=0.1, w=0.4, h=0.4),
                frontal_probability=0.9 if frontal else 0.1,
                eyes_visible_ratio=0.9 if frontal else 0.1,
                gaze="viewer" if frontal else "uncertain",
                confidence=0.9,
            )
        ]


def _write_video(path, frames, fps=10.0):
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, _SIZE)
    for frame in frames:
        writer.write(frame)
    writer.release()


def test_frontal_from_start_is_direct_gaze(tmp_path):
    frames = [np.full((_SIZE[1], _SIZE[0], 3), 200, dtype=np.uint8) for _ in range(20)]
    video = tmp_path / "bright.mp4"
    _write_video(video, frames)
    spans = portrait_spans(video, start_sec=0.0, end_sec=2.0, face_backend=_BrightnessGatedFaceBackend())
    assert any(span.kind == "direct_gaze" for span in spans)


def test_dark_to_bright_transition_is_turn_to_camera(tmp_path):
    frames = [
        np.full((_SIZE[1], _SIZE[0], 3), 20 if i < 10 else 220, dtype=np.uint8)
        for i in range(20)
    ]
    video = tmp_path / "turn.mp4"
    _write_video(video, frames)
    spans = portrait_spans(video, start_sec=0.0, end_sec=2.0, face_backend=_BrightnessGatedFaceBackend())
    assert any(span.kind == "turn_to_camera" for span in spans)


def test_short_frontal_blip_below_min_stable_sec_is_dropped(tmp_path):
    frames = [np.full((_SIZE[1], _SIZE[0], 3), 20, dtype=np.uint8) for _ in range(20)]
    frames[10] = np.full((_SIZE[1], _SIZE[0], 3), 220, dtype=np.uint8)
    video = tmp_path / "blip.mp4"
    _write_video(video, frames)
    spans = portrait_spans(video, start_sec=0.0, end_sec=2.0, face_backend=_BrightnessGatedFaceBackend())
    assert spans == []


def test_generate_spans_falls_back_to_generic_when_nothing_found(tmp_path):
    frames = [np.full((_SIZE[1], _SIZE[0], 3), 60, dtype=np.uint8) for _ in range(10)]
    video = tmp_path / "flat.mp4"
    _write_video(video, frames)
    unavailable = _BrightnessGatedFaceBackend()
    unavailable.status = BackendStatus(backend="fake", available=False)
    spans = generate_spans(video, start_sec=0.0, end_sec=1.0, face_backend=unavailable)
    assert len(spans) == 1
    assert spans[0].kind == "generic"
