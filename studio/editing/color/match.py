"""Deterministic shot-to-shot colour matching.

Anime edits pull shots from different episodes, scenes, and encoders; spliced
raw they read as a "素材合集" — one shot crushed, the next milky, one cold, the
next yellow — even when the cuts are on the beat.  This module measures each
clip's tone deterministically, solves a bounded correction toward a common
anchor, and can bake that correction into a per-clip 3D LUT — which the verified
``color_recipe`` path already applies through ``ColorGroup.PostClip.SetLUT``.

Correction here is the Node 01–03 stage (black/white, balance, saturation) that
precedes the stylistic Look; it does not replace the Look Recipe.  Nothing is
emitted into an executable EditSpec until the per-clip-correction apply path is
render-verified — this module is the deterministic measurement and solve half.
"""
from __future__ import annotations

from pathlib import Path
from statistics import median

import cv2
import numpy as np
from pydantic import BaseModel, ConfigDict, Field

COLOR_MATCH_VERSION = "color-match-1.0.0"

# Corrections are deliberately gentle: matching, not regrading.  Clamp so one
# mismeasured shot cannot blow out the whole sequence.
LIFT_LIMIT = 0.12
GAIN_MIN, GAIN_MAX = 0.75, 1.35
SAT_MIN, SAT_MAX = 0.6, 1.5
WARMTH_LIMIT = 0.12


class ClipColorStats(BaseModel):
    model_config = ConfigDict(extra="forbid")

    black_point: float = Field(..., ge=0, le=1)
    white_point: float = Field(..., ge=0, le=1)
    mean_luma: float = Field(..., ge=0, le=1)
    contrast: float = Field(..., ge=0, le=1)
    saturation: float = Field(..., ge=0, le=1)
    # Warm(+) / cool(-) proxy: normalized mean(R-B).  A crude but stable
    # colour-temperature signal that needs no white-point estimation.
    warmth: float = Field(..., ge=-1, le=1)


class ColorCorrection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lift: float = 0.0          # additive black-point shift
    gain: float = 1.0          # multiplicative white-point scale
    saturation: float = 1.0    # saturation multiplier
    warmth: float = 0.0        # additive R(+)/B(-) balance shift

    def is_identity(self, *, eps: float = 1e-4) -> bool:
        return (
            abs(self.lift) < eps
            and abs(self.gain - 1.0) < eps
            and abs(self.saturation - 1.0) < eps
            and abs(self.warmth) < eps
        )

    def apply(self, rgb: np.ndarray) -> np.ndarray:
        """Apply the correction to an RGB lattice/image in [0,1] float."""
        out = (rgb - self.lift) * self.gain
        # Warmth: push red up and blue down (or vice versa) symmetrically.
        out[..., 0] = out[..., 0] + self.warmth
        out[..., 2] = out[..., 2] - self.warmth
        if abs(self.saturation - 1.0) > 1e-6:
            luma = (
                0.2126 * out[..., 0] + 0.7152 * out[..., 1] + 0.0722 * out[..., 2]
            )[..., None]
            out = luma + (out - luma) * self.saturation
        return np.clip(out, 0.0, 1.0)


def measure_frame_color(frame_bgr: np.ndarray) -> ClipColorStats:
    rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB).astype(np.float64) / 255.0
    luma = 0.2126 * rgb[..., 0] + 0.7152 * rgb[..., 1] + 0.0722 * rgb[..., 2]
    black = float(np.percentile(luma, 1))
    white = float(np.percentile(luma, 99))
    hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV).astype(np.float64)
    saturation = float(hsv[..., 1].mean()) / 255.0
    warmth = float(np.clip((rgb[..., 0].mean() - rgb[..., 2].mean()), -1, 1))
    return ClipColorStats(
        black_point=round(black, 6),
        white_point=round(white, 6),
        mean_luma=round(float(luma.mean()), 6),
        contrast=round(float(np.clip(luma.std() * 2.0, 0, 1)), 6),
        saturation=round(saturation, 6),
        warmth=round(warmth, 6),
    )


def _aggregate(samples: list[ClipColorStats]) -> ClipColorStats:
    return ClipColorStats(
        black_point=round(median(s.black_point for s in samples), 6),
        white_point=round(median(s.white_point for s in samples), 6),
        mean_luma=round(median(s.mean_luma for s in samples), 6),
        contrast=round(median(s.contrast for s in samples), 6),
        saturation=round(median(s.saturation for s in samples), 6),
        warmth=round(median(s.warmth for s in samples), 6),
    )


def measure_clip_color(
    media: Path, *, in_sec: float, out_sec: float, samples: int = 5
) -> ClipColorStats:
    if out_sec <= in_sec:
        raise ValueError("out_sec 必须大于 in_sec")
    capture = cv2.VideoCapture(str(media))
    if not capture.isOpened():
        raise ValueError(f"无法打开媒体: {media}")
    times = np.linspace(in_sec, out_sec, num=max(2, samples) + 2)[1:-1]
    collected: list[ClipColorStats] = []
    try:
        for sec in times:
            capture.set(cv2.CAP_PROP_POS_MSEC, float(sec) * 1000)
            ok, frame = capture.read()
            if ok:
                collected.append(measure_frame_color(frame))
    finally:
        capture.release()
    if not collected:
        raise ValueError(f"clip 未取到任何帧: {media}")
    return _aggregate(collected)


def anchor_from_sequence(stats: list[ClipColorStats]) -> ClipColorStats:
    """A robust common target: the per-metric median across the sequence."""
    if not stats:
        raise ValueError("需要至少一个 clip 的色彩统计")
    return _aggregate(stats)


def solve_color_correction(
    stats: ClipColorStats, anchor: ClipColorStats
) -> ColorCorrection:
    """Bounded correction moving ``stats`` toward ``anchor``."""
    lift = float(np.clip(stats.black_point - anchor.black_point, -LIFT_LIMIT, LIFT_LIMIT))
    span = max(stats.white_point - stats.black_point, 1e-3)
    anchor_span = max(anchor.white_point - anchor.black_point, 1e-3)
    gain = float(np.clip(anchor_span / span, GAIN_MIN, GAIN_MAX))
    sat = float(np.clip(
        anchor.saturation / max(stats.saturation, 1e-3), SAT_MIN, SAT_MAX
    ))
    warmth = float(np.clip((anchor.warmth - stats.warmth) * 0.5, -WARMTH_LIMIT, WARMTH_LIMIT))
    return ColorCorrection(
        lift=round(lift, 6),
        gain=round(gain, 6),
        saturation=round(sat, 6),
        warmth=round(warmth, 6),
    )


def write_correction_lut(
    correction: ColorCorrection, path: Path, *, size: int = 17
) -> Path:
    """Bake a correction into a .cube 3D LUT for the verified SetLUT path."""
    axis = np.linspace(0.0, 1.0, size)
    r, g, b = np.meshgrid(axis, axis, axis, indexing="ij")
    lattice = np.stack([r, g, b], axis=-1)
    mapped = correction.apply(lattice.reshape(-1, 3)).reshape(size, size, size, 3)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"LUT_3D_SIZE {size}", "DOMAIN_MIN 0.0 0.0 0.0", "DOMAIN_MAX 1.0 1.0 1.0"]
    for bi in range(size):
        for gi in range(size):
            for ri in range(size):
                red, green, blue = mapped[ri, gi, bi]
                lines.append(f"{red:.6f} {green:.6f} {blue:.6f}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


__all__ = [
    "COLOR_MATCH_VERSION",
    "ClipColorStats",
    "ColorCorrection",
    "measure_frame_color",
    "measure_clip_color",
    "anchor_from_sequence",
    "solve_color_correction",
    "write_correction_lut",
]
