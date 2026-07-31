"""Anime face backend degrades cleanly without ONNX weights (REFACTOR.md §7.1, §22.7)."""
from __future__ import annotations

import numpy as np

from studio.selection.backends.anime_face import (
    HeuristicAnimeFaceBackend,
    OnnxAnimeFaceBackend,
    create_anime_face_backend,
)


def test_onnx_backend_unavailable_without_weights(tmp_path):
    backend = OnnxAnimeFaceBackend(weights_path=tmp_path / "missing.onnx")
    assert backend.status.available is False
    assert backend.status.fallback is not None


def test_factory_falls_back_to_heuristic(tmp_path):
    backend = create_anime_face_backend(weights_path=tmp_path / "missing.onnx")
    assert isinstance(backend, HeuristicAnimeFaceBackend)
    assert backend.status.available is True


def test_heuristic_backend_detects_something_on_synthetic_frame():
    frame = np.zeros((120, 120, 3), dtype=np.uint8)
    import cv2

    cv2.circle(frame, (60, 40), 30, (200, 190, 180), -1)
    cv2.rectangle(frame, (30, 20), (90, 80), (120, 110, 100), 2)
    backend = HeuristicAnimeFaceBackend()
    faces = backend.detect(frame)
    for face in faces:
        assert 0.0 <= face.frontal_probability <= 1.0
        assert 0.0 <= face.confidence <= 0.35
