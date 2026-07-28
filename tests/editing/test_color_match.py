from pathlib import Path

import cv2
import numpy as np
import pytest

from studio.editing.color import (
    ClipColorStats,
    ColorCorrection,
    anchor_from_sequence,
    build_color_match_plan,
    evaluate_color_match,
    measure_clip_color,
    measure_frame_color,
    solve_color_correction,
    write_correction_lut,
)


def _solid(bgr):
    frame = np.zeros((64, 96, 3), np.uint8)
    frame[:] = bgr
    return frame


def test_measure_frame_detects_brightness_and_warmth():
    dark = measure_frame_color(_solid((20, 20, 20)))
    bright = measure_frame_color(_solid((200, 200, 200)))
    assert bright.mean_luma > dark.mean_luma
    warm = measure_frame_color(_solid((10, 60, 200)))  # BGR -> lots of red
    cool = measure_frame_color(_solid((200, 60, 10)))  # lots of blue
    assert warm.warmth > cool.warmth


def _write_video(path: Path, bgr, *, fps=10, frames=10):
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (96, 64))
    for _ in range(frames):
        writer.write(_solid(bgr))
    writer.release()


def test_measure_clip_color_from_video(tmp_path: Path):
    video = tmp_path / "c.mp4"
    _write_video(video, (40, 80, 120))
    stats = measure_clip_color(video, in_sec=0.1, out_sec=0.9)
    assert 0 <= stats.black_point <= stats.white_point <= 1
    assert 0 <= stats.saturation <= 1


def test_correction_moves_stats_toward_anchor():
    crushed = ClipColorStats(
        black_point=0.0, white_point=0.6, mean_luma=0.3, contrast=0.4,
        saturation=0.3, warmth=0.2,
    )
    anchor = ClipColorStats(
        black_point=0.05, white_point=0.9, mean_luma=0.5, contrast=0.5,
        saturation=0.5, warmth=0.0,
    )
    correction = solve_color_correction(crushed, anchor)
    # White point was low -> gain should lift it.
    assert correction.gain > 1.0
    # Saturation was low -> boost.
    assert correction.saturation > 1.0
    # Too warm -> cool it down.
    assert correction.warmth < 0.0


def test_correction_bounds_are_respected():
    extreme = ClipColorStats(
        black_point=0.0, white_point=0.05, mean_luma=0.02, contrast=0.0,
        saturation=0.01, warmth=0.9,
    )
    anchor = ClipColorStats(
        black_point=0.1, white_point=0.95, mean_luma=0.5, contrast=0.5,
        saturation=0.6, warmth=-0.5,
    )
    correction = solve_color_correction(extreme, anchor)
    assert 0.75 <= correction.gain <= 1.35
    assert 0.6 <= correction.saturation <= 1.5
    assert abs(correction.warmth) <= 0.12


def test_lut_roundtrip_matches_apply(tmp_path: Path):
    correction = ColorCorrection(lift=0.02, gain=1.2, saturation=1.1, warmth=0.03)
    path = write_correction_lut(correction, tmp_path / "c.cube", size=9)
    text = path.read_text().splitlines()
    assert text[0] == "LUT_3D_SIZE 9"
    entries = [line for line in text if len(line.split()) == 3 and "." in line]
    assert len(entries) == 9 ** 3
    # First data entry is the transform of black (r=g=b=0).
    first = np.array([float(x) for x in entries[0].split()])
    expected = correction.apply(np.zeros((1, 3)))[0]
    assert np.allclose(first, expected, atol=1e-5)


def test_color_match_plan_reduces_jumps():
    # Two shots that disagree sharply on black and saturation.
    a = ClipColorStats(
        black_point=0.0, white_point=0.7, mean_luma=0.3, contrast=0.4,
        saturation=0.2, warmth=0.15,
    )
    b = ClipColorStats(
        black_point=0.18, white_point=0.95, mean_luma=0.6, contrast=0.5,
        saturation=0.6, warmth=-0.1,
    )
    plan = build_color_match_plan([("c1", a), ("c2", b)])
    report = evaluate_color_match(plan)
    black = next(c for c in report.checks if c.metric == "max_black_jump")
    sat = next(c for c in report.checks if c.metric == "max_saturation_jump")
    assert black.after <= black.before
    assert sat.after <= sat.before


def test_plan_writes_luts_when_dir_given(tmp_path: Path):
    a = ClipColorStats(
        black_point=0.0, white_point=0.6, mean_luma=0.3, contrast=0.4,
        saturation=0.2, warmth=0.2,
    )
    b = ClipColorStats(
        black_point=0.1, white_point=0.9, mean_luma=0.5, contrast=0.5,
        saturation=0.5, warmth=0.0,
    )
    plan = build_color_match_plan([("c1", a), ("c2", b)], lut_dir=tmp_path)
    written = [e.lut_path for e in plan.entries if e.lut_path]
    assert written
    for path in written:
        assert Path(path).is_file()
