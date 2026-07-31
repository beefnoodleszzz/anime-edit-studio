from __future__ import annotations

import cv2
import numpy as np

from studio.analysis.global_motion import estimate_global_motion


def _textured(rng, shift_x=0, shift_y=0, scale=1.0, rotation_deg=0.0):
    base = rng.integers(0, 255, size=(240, 320), dtype=np.uint8)
    cv2.rectangle(base, (100, 80), (220, 180), 10, -1)
    cv2.circle(base, (60, 60), 25, 250, -1)
    center = (160, 120)
    matrix = cv2.getRotationMatrix2D(center, rotation_deg, scale)
    matrix[0, 2] += shift_x
    matrix[1, 2] += shift_y
    return cv2.warpAffine(base, matrix, (320, 240), borderMode=cv2.BORDER_REPLICATE)


def test_detects_horizontal_pan_direction_and_magnitude():
    rng = np.random.default_rng(1)
    a = _textured(rng)
    b = _textured(rng, shift_x=12)
    estimate = estimate_global_motion(a, b)
    assert estimate.confidence > 0.5
    assert estimate.tx > 5


def test_detects_vertical_pan_direction():
    rng = np.random.default_rng(2)
    a = _textured(rng)
    b = _textured(rng, shift_y=-15)
    estimate = estimate_global_motion(a, b)
    assert estimate.confidence > 0.5
    assert estimate.ty < -5


def test_detects_zoom_in_as_positive_log_scale():
    rng = np.random.default_rng(3)
    a = _textured(rng)
    b = _textured(rng, scale=1.15)
    estimate = estimate_global_motion(a, b)
    assert estimate.confidence > 0.4
    assert estimate.log_scale > 0


def test_detects_rotation_sign():
    rng = np.random.default_rng(4)
    a = _textured(rng)
    b = _textured(rng, rotation_deg=8.0)
    estimate = estimate_global_motion(a, b)
    assert estimate.confidence > 0.3
    assert estimate.rotation_deg > 1.0


def test_low_texture_frame_yields_low_confidence_not_false_precision():
    flat_a = np.full((240, 320), 120, dtype=np.uint8)
    flat_b = np.full((240, 320), 121, dtype=np.uint8)
    estimate = estimate_global_motion(flat_a, flat_b)
    assert estimate.confidence < 0.3
    assert estimate.method == "ecc_fallback"
