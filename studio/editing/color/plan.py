"""Sequence-level colour-match plan and its acceptance QA.

Given each clip's measured tone, pick a common anchor, solve a bounded
correction per clip, and score the corrected sequence: adjacent shots should no
longer jump in black level, saturation, or warmth.  The plan carries the solved
corrections and (optionally) the per-clip LUT paths; wiring these into the
compiler waits on render-verifying the per-clip apply path.
"""
from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from .match import (
    COLOR_MATCH_VERSION,
    ClipColorStats,
    ColorCorrection,
    anchor_from_sequence,
    solve_color_correction,
    write_correction_lut,
)

# Above these adjacent deltas a raw cut reads as a source mismatch.
BLACK_JUMP_LIMIT = 0.10
SAT_JUMP_LIMIT = 0.18
WARMTH_JUMP_LIMIT = 0.10


class ClipColorEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    clip_id: str
    measured: ClipColorStats
    correction: ColorCorrection
    corrected: ClipColorStats
    lut_path: str | None = None


class ColorMatchPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str = COLOR_MATCH_VERSION
    anchor: ClipColorStats
    entries: list[ClipColorEntry] = Field(default_factory=list)


class ColorMatchCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")
    metric: str
    before: float
    after: float
    target: float
    passed: bool


class ColorMatchReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: str = COLOR_MATCH_VERSION
    clip_count: int
    checks: list[ColorMatchCheck]
    passed: bool


def _predict_corrected(stats: ClipColorStats, correction: ColorCorrection) -> ClipColorStats:
    """Cheap forward model of the correction on the aggregate stats.

    Mirrors :meth:`ColorCorrection.apply` on scalar black/white/sat so the QA
    can score the corrected sequence without re-decoding video.
    """
    black = max(0.0, min(1.0, (stats.black_point - correction.lift) * correction.gain))
    white = max(0.0, min(1.0, (stats.white_point - correction.lift) * correction.gain))
    sat = max(0.0, min(1.0, stats.saturation * correction.saturation))
    warmth = max(-1.0, min(1.0, stats.warmth + 2.0 * correction.warmth))
    return ClipColorStats(
        black_point=round(black, 6),
        white_point=round(white, 6),
        mean_luma=stats.mean_luma,
        contrast=stats.contrast,
        saturation=round(sat, 6),
        warmth=round(warmth, 6),
    )


def build_color_match_plan(
    measured: list[tuple[str, ClipColorStats]],
    *,
    anchor: ClipColorStats | None = None,
    lut_dir: Path | None = None,
    lut_size: int = 17,
) -> ColorMatchPlan:
    stats_only = [stats for _, stats in measured]
    anchor = anchor or anchor_from_sequence(stats_only)
    entries: list[ClipColorEntry] = []
    for clip_id, stats in measured:
        correction = solve_color_correction(stats, anchor)
        corrected = _predict_corrected(stats, correction)
        lut_path = None
        if lut_dir is not None and not correction.is_identity():
            path = write_correction_lut(
                correction, lut_dir / f"{clip_id}_match.cube", size=lut_size
            )
            lut_path = str(path)
        entries.append(
            ClipColorEntry(
                clip_id=clip_id,
                measured=stats,
                correction=correction,
                corrected=corrected,
                lut_path=lut_path,
            )
        )
    return ColorMatchPlan(anchor=anchor, entries=entries)


def _max_adjacent(values: list[float]) -> float:
    return max((abs(a - b) for a, b in zip(values, values[1:])), default=0.0)


def evaluate_color_match(plan: ColorMatchPlan) -> ColorMatchReport:
    before_black = _max_adjacent([e.measured.black_point for e in plan.entries])
    after_black = _max_adjacent([e.corrected.black_point for e in plan.entries])
    before_sat = _max_adjacent([e.measured.saturation for e in plan.entries])
    after_sat = _max_adjacent([e.corrected.saturation for e in plan.entries])
    before_warm = _max_adjacent([e.measured.warmth for e in plan.entries])
    after_warm = _max_adjacent([e.corrected.warmth for e in plan.entries])
    checks = [
        ColorMatchCheck(
            metric="max_black_jump", before=round(before_black, 6),
            after=round(after_black, 6), target=BLACK_JUMP_LIMIT,
            passed=after_black <= BLACK_JUMP_LIMIT,
        ),
        ColorMatchCheck(
            metric="max_saturation_jump", before=round(before_sat, 6),
            after=round(after_sat, 6), target=SAT_JUMP_LIMIT,
            passed=after_sat <= SAT_JUMP_LIMIT,
        ),
        ColorMatchCheck(
            metric="max_warmth_jump", before=round(before_warm, 6),
            after=round(after_warm, 6), target=WARMTH_JUMP_LIMIT,
            passed=after_warm <= WARMTH_JUMP_LIMIT,
        ),
    ]
    return ColorMatchReport(
        clip_count=len(plan.entries),
        checks=checks,
        passed=all(check.passed for check in checks),
    )


__all__ = [
    "ClipColorEntry",
    "ColorMatchPlan",
    "ColorMatchCheck",
    "ColorMatchReport",
    "build_color_match_plan",
    "evaluate_color_match",
]
