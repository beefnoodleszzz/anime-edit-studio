"""Technical hard gate (REFACTOR.md §9, §22.1): dark ≠ defect, subtitles are a
hard gate that no other score can rescue."""
from __future__ import annotations

import cv2
import numpy as np
import pytest

from studio.selection.config import load_thresholds
from studio.selection.technical_gate import (
    _longest_run,
    _watermark_probability,
    compute_technical_profile,
)


def _write_video(path, frames, fps=24.0):
    height, width = frames[0].shape[:2]
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height))
    for frame in frames:
        writer.write(frame)
    writer.release()


def _noisy_frame(width, height, mean, rng, seed):
    local_rng = np.random.default_rng(seed)
    base = np.clip(local_rng.normal(mean, 18, (height, width, 3)), 0, 255).astype(np.uint8)
    cv2.circle(base, (width // 2, height // 3), min(width, height) // 6, (200, 180, 160), -1)
    cv2.rectangle(base, (10, 10), (width - 10, height - 10), (max(mean - 40, 0),) * 3, 2)
    return base


def test_longest_run():
    assert _longest_run([False, True, True, False, True, True, True]) == 3
    assert _longest_run([False, False]) == 0
    assert _longest_run([True, True]) == 2


def test_dark_scene_is_not_penalized(tmp_path):
    """A genuinely dark (low-mean) scene must not be flagged just for being dark."""
    frames = [_noisy_frame(320, 240, mean=40, rng=None, seed=i) for i in range(10)]
    video = tmp_path / "dark.mp4"
    _write_video(video, frames)
    result = compute_technical_profile(video, start_sec=0.0, end_sec=0.4, sample_count=5)
    assert result.technical.black_clip_ratio < load_thresholds().technical_gate.maximum_black_clip_ratio
    assert "black_clip" not in result.technical.failure_reasons


def test_true_black_clip_is_flagged(tmp_path):
    frames = [np.zeros((240, 320, 3), dtype=np.uint8) for _ in range(10)]
    video = tmp_path / "black.mp4"
    _write_video(video, frames)
    result = compute_technical_profile(video, start_sec=0.0, end_sec=0.4, sample_count=5)
    assert result.technical.black_clip_ratio > 0.9
    assert not result.technical.passed
    assert "black_clip" in result.technical.failure_reasons


def test_window_too_short_fails_gate(tmp_path):
    frames = [_noisy_frame(320, 240, mean=120, rng=None, seed=i) for i in range(5)]
    video = tmp_path / "short.mp4"
    _write_video(video, frames)
    result = compute_technical_profile(video, start_sec=0.0, end_sec=0.05, sample_count=3)
    assert not result.technical.passed
    assert "window_too_short" in result.technical.failure_reasons


def test_watermark_probability_flags_stable_corner_content():
    class _Sample:
        def __init__(self, corner_edges):
            self.corner_edges = corner_edges

    stable = [_Sample((0.20, 0.20, 0.02, 0.02)) for _ in range(6)]
    assert _watermark_probability(stable) > 0.5

    noisy = [_Sample((0.20 + 0.15 * ((-1) ** i), 0.02, 0.02, 0.02)) for i in range(6)]
    assert _watermark_probability(noisy) == 0.0
