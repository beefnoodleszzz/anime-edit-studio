"""Heuristic subtitle / watermark risk estimation for local review."""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np


def assess_keyframe(path: str | None) -> dict:
    if not path:
        return _empty()
    image = cv2.imread(str(Path(path)))
    if image is None:
        return _empty()
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape
    if h < 24 or w < 24:
        return _empty()
    subtitle_bands = {
        "top": gray[: h // 5, :],
        "middle": gray[h // 3 : h * 2 // 3, :],
        "bottom": gray[h * 4 // 5 :, :],
    }
    subtitle_scores = {}
    for name, band in subtitle_bands.items():
        edges = cv2.Canny(band, 80, 160)
        contrast = float(np.std(band) / 255.0)
        occupancy = float(np.mean(edges > 0))
        subtitle_scores[name] = min(1.0, occupancy * 8 + contrast * 0.4)
    subtitle_risk = max(subtitle_scores["bottom"], subtitle_scores["middle"] * 0.8)

    corner = min(h, w) // 6
    watermark_regions = {
        "top_left": gray[:corner, :corner],
        "top_right": gray[:corner, w - corner :],
        "bottom_left": gray[h - corner :, :corner],
        "bottom_right": gray[h - corner :, w - corner :],
    }
    watermark_scores = {}
    for name, band in watermark_regions.items():
        edges = cv2.Canny(band, 100, 200)
        watermark_scores[name] = min(1.0, float(np.mean(edges > 0)) * 12)
    watermark_risk = max(watermark_scores.values()) if watermark_scores else 0.0

    crop_avoidable = subtitle_scores["bottom"] > 0.45 and subtitle_scores["top"] < 0.2
    suggested_action = "人工审查"
    if subtitle_risk >= 0.65:
        suggested_action = "使用同源无字版本"
    elif crop_avoidable:
        suggested_action = "竖屏裁切规避"
    elif watermark_risk >= 0.45:
        suggested_action = "降低优先级"
    return {
        "subtitle_risk": round(subtitle_risk, 4),
        "subtitle_regions": {k: round(v, 4) for k, v in subtitle_scores.items()},
        "watermark_risk": round(watermark_risk, 4),
        "watermark_regions": {k: round(v, 4) for k, v in watermark_scores.items()},
        "crop_avoidable": crop_avoidable,
        "suggested_action": suggested_action,
    }


def _empty() -> dict:
    return {
        "subtitle_risk": 0.0,
        "subtitle_regions": {},
        "watermark_risk": 0.0,
        "watermark_regions": {},
        "crop_avoidable": False,
        "suggested_action": "人工审查",
    }
