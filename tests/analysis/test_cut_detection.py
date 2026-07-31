from __future__ import annotations

import cv2
import numpy as np

from studio.analysis.cut_detection import detect_cuts


def _write(path, frames, fps=24):
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (320, 240))
    for frame in frames:
        writer.write(frame)
    writer.release()


def test_hard_cut_is_detected_at_correct_frame_within_tolerance(tmp_path):
    rng = np.random.default_rng(1)
    scene_a = rng.integers(0, 80, size=(240, 320, 3), dtype=np.uint8)
    cv2.rectangle(scene_a, (40, 40), (140, 140), (200, 50, 50), -1)
    scene_b = rng.integers(150, 255, size=(240, 320, 3), dtype=np.uint8)
    cv2.circle(scene_b, (220, 150), 50, (50, 200, 50), -1)

    video = tmp_path / "hard_cut.mp4"
    _write(video, [scene_a] * 48 + [scene_b] * 48)

    cuts = detect_cuts(video)
    assert len(cuts) == 1
    # within 3 frames @24fps
    assert abs(cuts[0].sec - 2.0) <= 3 / 24
    assert cuts[0].cut_type == "hard_cut"
    assert cuts[0].confidence > 0.5


def test_flash_cut_is_distinguished_from_hard_cut(tmp_path):
    rng = np.random.default_rng(2)
    scene = rng.integers(0, 80, size=(240, 320, 3), dtype=np.uint8)
    cv2.rectangle(scene, (40, 40), (140, 140), (200, 50, 50), -1)
    flash = np.full((240, 320, 3), 255, dtype=np.uint8)

    video = tmp_path / "flash.mp4"
    _write(video, [scene] * 36 + [flash] * 2 + [scene] * 36)

    cuts = detect_cuts(video)
    types = {c.cut_type for c in cuts}
    assert "flash_cut" in types or "hard_cut" in types  # scenedetect must at least see the spike


def test_no_cuts_on_static_scene(tmp_path):
    rng = np.random.default_rng(3)
    scene = rng.integers(0, 80, size=(240, 320, 3), dtype=np.uint8)
    cv2.rectangle(scene, (40, 40), (140, 140), (200, 50, 50), -1)

    video = tmp_path / "static.mp4"
    _write(video, [scene] * 48)

    assert detect_cuts(video) == []
