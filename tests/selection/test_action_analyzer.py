"""Action-phase analysis: camera motion must not masquerade as subject action
(REFACTOR.md §11, §22.3)."""
from __future__ import annotations

import cv2
import numpy as np
import pytest

from studio.selection.action_analyzer import analyze_action

_SIZE = (320, 240)


def _textured_background(seed=7):
    rng = np.random.default_rng(seed)
    base = rng.integers(0, 255, (_SIZE[1] + 60, _SIZE[0] + 60, 3), dtype=np.uint8)
    # Blocky checker texture gives goodFeaturesToTrack plenty of real corners.
    for y in range(0, base.shape[0], 16):
        for x in range(0, base.shape[1], 16):
            if (x // 16 + y // 16) % 2 == 0:
                base[y : y + 16, x : x + 16] = (base[y : y + 16, x : x + 16] // 2) + 40
    return base


def _write_video(path, frames, fps=20.0):
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, _SIZE)
    for frame in frames:
        writer.write(frame)
    writer.release()


def test_camera_pan_alone_scores_low_action(tmp_path):
    background = _textured_background()
    frames = []
    for i in range(30):
        shift_x = i * 2
        frame = background[30 : 30 + _SIZE[1], shift_x : shift_x + _SIZE[0]].copy()
        frames.append(frame)
    video = tmp_path / "pan.mp4"
    _write_video(video, frames)
    profile = analyze_action(video, start_sec=0.0, end_sec=1.5, sample_hz=10.0)
    assert profile.global_motion_peak > 0.5
    assert profile.residual_motion_peak < profile.global_motion_peak
    assert profile.action_score < 0.5


def test_independent_subject_motion_scores_higher_residual(tmp_path):
    background = _textured_background()[30 : 30 + _SIZE[1], 30 : 30 + _SIZE[0]].copy()
    frames = []
    for i in range(30):
        frame = background.copy()
        cx = 40 + i * 7
        cv2.circle(frame, (min(cx, _SIZE[0] - 10), 120), 22, (10, 10, 220), -1)
        frames.append(frame)
    video = tmp_path / "subject_motion.mp4"
    _write_video(video, frames)
    profile = analyze_action(video, start_sec=0.0, end_sec=1.5, sample_hz=10.0)
    assert profile.residual_motion_peak > 0.0
    assert profile.impact_sec is not None


def test_too_few_samples_returns_default_profile(tmp_path):
    background = _textured_background()[30 : 30 + _SIZE[1], 30 : 30 + _SIZE[0]].copy()
    video = tmp_path / "one_frame.mp4"
    _write_video(video, [background])
    profile = analyze_action(video, start_sec=0.0, end_sec=0.05, sample_hz=10.0)
    assert profile.action_score == 0.0
    assert profile.impact_sec is None
