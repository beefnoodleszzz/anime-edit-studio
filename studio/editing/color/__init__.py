"""Deterministic shot-to-shot colour matching (measure → solve → QA)."""

from .match import (
    COLOR_MATCH_VERSION,
    ClipColorStats,
    ColorCorrection,
    anchor_from_sequence,
    measure_clip_color,
    measure_frame_color,
    solve_color_correction,
    write_correction_lut,
)
from .plan import (
    ClipColorEntry,
    ColorMatchCheck,
    ColorMatchPlan,
    ColorMatchReport,
    build_color_match_plan,
    evaluate_color_match,
)

__all__ = [
    "COLOR_MATCH_VERSION",
    "ClipColorStats",
    "ColorCorrection",
    "anchor_from_sequence",
    "measure_clip_color",
    "measure_frame_color",
    "solve_color_correction",
    "write_correction_lut",
    "ClipColorEntry",
    "ColorMatchCheck",
    "ColorMatchPlan",
    "ColorMatchReport",
    "build_color_match_plan",
    "evaluate_color_match",
]
