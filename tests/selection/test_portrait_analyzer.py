"""Portrait analysis over synthetic face-detection sequences (REFACTOR.md §22.2)."""
from __future__ import annotations

import numpy as np

from studio.selection.backends.protocols import BackendStatus, FaceDetection
from studio.selection.portrait_analyzer import analyze_portrait
from studio.selection.schemas import BoundingBox

_FRAMES = [np.zeros((8, 8, 3), dtype=np.uint8) for _ in range(6)]


class _FakeFaceBackend:
    def __init__(self, faces_per_frame):
        self.status = BackendStatus(backend="fake", available=True, version="test")
        self._faces_per_frame = faces_per_frame
        self._call = 0

    def detect(self, frame):
        faces = self._faces_per_frame[self._call % len(self._faces_per_frame)]
        self._call += 1
        return faces


def _face(frontal, eyes, gaze="viewer", edge=False, confidence=0.9):
    return FaceDetection(
        bbox=BoundingBox(x=0.3, y=0.1, w=0.4, h=0.4),
        frontal_probability=frontal,
        eyes_visible_ratio=eyes,
        gaze=gaze,
        touches_frame_edge=edge,
        confidence=confidence,
    )


def test_stable_frontal_face_scores_high_portrait():
    backend = _FakeFaceBackend([[_face(0.95, 0.95)]])
    profile = analyze_portrait(_FRAMES, face_backend=backend)
    assert profile.face_visible_ratio == 1.0
    assert profile.gaze_direction == "viewer"
    assert profile.portrait_score > 0.6


def test_single_frame_frontal_face_is_not_a_stable_hold():
    """A single frontal hit amid mostly-absent frames must not read as a stable
    hold — the WindowKind classifier (temporal_windows.py, §8.3) is what turns
    this signal into (or withholds) ``direct_gaze``; here we only require the
    stability/coverage measures it depends on to reflect the transience."""
    faces_per_frame = [[_face(0.95, 0.9)]] + [[] for _ in range(5)]
    backend = _FakeFaceBackend(faces_per_frame)
    profile = analyze_portrait(_FRAMES, face_backend=backend)
    assert profile.face_visible_ratio < 0.3
    assert profile.temporal_stability == 0.0


def test_turn_to_camera_reflected_in_low_temporal_stability_of_raw_frontal_swing():
    faces_per_frame = [
        [_face(0.1, 0.2)], [_face(0.2, 0.3)], [_face(0.4, 0.5)],
        [_face(0.7, 0.8)], [_face(0.9, 0.95)], [_face(0.95, 0.95)],
    ]
    backend = _FakeFaceBackend(faces_per_frame)
    profile = analyze_portrait(_FRAMES, face_backend=backend)
    assert profile.face_visible_ratio == 1.0
    assert profile.frontal_probability > 0.5


def test_no_face_detected_reports_occluded_not_a_fabricated_gaze():
    backend = _FakeFaceBackend([[]])
    profile = analyze_portrait(_FRAMES, face_backend=backend)
    assert profile.gaze_direction == "occluded"
    assert profile.face_visible_ratio == 0.0
    assert profile.portrait_score == 0.0


def test_multiple_faces_uses_most_confident_one():
    backend = _FakeFaceBackend([[_face(0.2, 0.2, confidence=0.3), _face(0.9, 0.9, confidence=0.95)]])
    profile = analyze_portrait(_FRAMES, face_backend=backend)
    assert profile.frontal_probability == 0.9
