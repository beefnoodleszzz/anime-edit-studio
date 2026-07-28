"""Versioned editing-style profile compiled from a reference fingerprint.

The fingerprint describes what was measured.  This profile is the portable
editing grammar consumed by planning: rhythm, beat affinity, visual contrast,
and effect density.  Keeping the two contracts separate lets future reference
analyzers evolve without coupling Sequence Planner to one video or one model.
"""
from __future__ import annotations

from typing import Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator

from studio.core.hashing import stable_hash

from .fingerprint import MotionCurvePoint, StyleFingerprint

EDITING_STYLE_PROFILE_VERSION = "editing-style-profile-1.4.0"


class EditingStyleProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str = EDITING_STYLE_PROFILE_VERSION
    id: str = "balanced-default"
    name: str = "Balanced"
    source: Literal["default", "reference", "curated"] = "default"
    source_fingerprint_version: str | None = None

    # Rhythm grammar
    target_cut_density: float = Field(1.1, gt=0, le=8)
    median_shot_length: float = Field(0.85, gt=0)
    min_shot_length: float = Field(0.35, gt=0)
    max_shot_length: float = Field(1.8, gt=0)
    duration_pattern: list[float] = Field(
        default_factory=lambda: [1.0, 0.82, 1.12, 1.0, 1.35, 0.72]
    )
    normalized_cut_positions: list[float] = Field(default_factory=list)
    beat_sync_target: float = Field(0.55, ge=0, le=1)
    beat_tolerance_sec: float = Field(0.08, gt=0, le=0.25)
    impact_snap_priority: float = Field(1.0, ge=0, le=2)
    beat_grid_subdivision: Literal["adaptive", "section_1_2_4"] = "adaptive"
    hook_duration_ratio: float = Field(0.075, ge=0.03, le=0.2)
    hook_event_count: int = Field(5, ge=1, le=8)
    ending_duration_ratio: float = Field(0.24, ge=0.08, le=0.4)
    ending_deceleration_pattern: list[float] = Field(
        default_factory=lambda: [0.9, 0.9, 1.35, 1.8, 2.7, 3.9]
    )

    # Visual grammar. These remain soft scoring signals, never hard filters.
    hard_cut_ratio: float = Field(1.0, ge=0, le=1)
    scale_contrast_target: float = Field(0.18, ge=0, le=1)
    motion_change_ratio: float = Field(0.55, ge=0, le=1)
    shot_scale_pattern: list[float] = Field(default_factory=list)
    motion_direction_pattern: list[str] = Field(default_factory=list)
    motion_intensity_pattern: list[float] = Field(default_factory=list)
    motion_curve: list[MotionCurvePoint] = Field(default_factory=list)
    motion_peaks: list[float] = Field(default_factory=list)
    motion_zero_crossings: list[float] = Field(default_factory=list)
    direction_reversals: list[float] = Field(default_factory=list)
    cut_carry_vectors: list[dict] = Field(default_factory=list)
    motion_median_target: float = Field(0.3, ge=0)
    motion_p75_target: float = Field(0.8, ge=0)
    motion_dynamic_range_target: float = Field(2.0, ge=1)
    hold_ratio_target: float = Field(0.18, ge=0, le=1)
    direction_balance_target: float = Field(0.5, ge=0, le=1)
    direction_reversal_target: float = Field(0.35, ge=0, le=1)
    speed_ramp_density: float = Field(0.0, ge=0, le=4)
    sound_event_density: float = Field(0.0, ge=0, le=12)

    @model_validator(mode="after")
    def _valid_profile(self):
        if self.max_shot_length < self.min_shot_length:
            raise ValueError("max_shot_length 必须 >= min_shot_length")
        if not self.duration_pattern or any(value <= 0 for value in self.duration_pattern):
            raise ValueError("duration_pattern 必须包含正数")
        if (
            not self.ending_deceleration_pattern
            or any(value <= 0 for value in self.ending_deceleration_pattern)
        ):
            raise ValueError("ending_deceleration_pattern 必须包含正数")
        if any(
            right <= left
            for left, right in zip(
                self.normalized_cut_positions,
                self.normalized_cut_positions[1:],
            )
        ):
            raise ValueError("normalized_cut_positions 必须严格递增")
        if any(value <= 0 or value >= 1 for value in self.normalized_cut_positions):
            raise ValueError("normalized_cut_positions 必须位于 (0,1)")
        return self


def default_editing_style() -> EditingStyleProfile:
    return EditingStyleProfile()


def _motion_change_ratio(values: list[str]) -> float:
    if len(values) < 2:
        return 0.5
    changes = sum(left != right for left, right in zip(values, values[1:]))
    return changes / (len(values) - 1)


def _scale_contrast(values: list[float]) -> float:
    if len(values) < 2:
        return 0.18
    deltas = sorted(abs(left - right) for left, right in zip(values, values[1:]))
    return deltas[len(deltas) // 2]


def _percentile_pattern(values: list[float]) -> list[float]:
    """Map analyzer-specific magnitudes onto a portable 0..1 ordering."""
    if not values:
        return []
    ordered = sorted(enumerate(values), key=lambda item: (item[1], item[0]))
    denominator = max(1, len(values) - 1)
    ranks = [0.0] * len(values)
    for rank, (index, _) in enumerate(ordered):
        ranks[index] = rank / denominator
    return ranks


def _horizontal_motion_stats(
    directions: list[str],
    magnitudes: list[float],
) -> tuple[float, float, float]:
    moving = [
        direction for direction, magnitude in zip(directions, magnitudes)
        if magnitude >= 0.3 and direction != "static"
    ]
    horizontal = [
        -1 if direction == "left" else 1
        for direction in moving
        if direction in {"left", "right"}
    ]
    if horizontal:
        left = horizontal.count(-1)
        right = horizontal.count(1)
        balance = min(left, right) / max(left, right)
    else:
        balance = 0.5
    reversals = sum(first != second for first, second in zip(horizontal, horizontal[1:]))
    reversal_rate = reversals / max(1, len(horizontal) - 1)
    holds = sum(magnitude < 0.3 for magnitude in magnitudes)
    return balance, reversal_rate, holds / max(1, len(magnitudes))


def compile_editing_style(
    fingerprint: StyleFingerprint,
    *,
    name: str | None = None,
) -> EditingStyleProfile:
    """Compile measured reference traits into a portable planning contract."""
    median = max(0.08, fingerprint.median_shot_length)
    distribution = fingerprint.shot_length_distribution
    p10 = max(0.08, distribution.get("p10", median * 0.55))
    p25 = max(p10, distribution.get("p25", median * 0.78))
    p75 = max(median, distribution.get("p75", median * 1.25))
    p90 = max(p75, distribution.get("p90", median * 1.8))
    pattern = [
        p25 / median,
        1.0,
        p10 / median,
        1.0,
        p75 / median,
        1.0,
        p90 / median,
        p25 / median,
    ]
    cut_positions = [
        value / fingerprint.duration_sec
        for value in fingerprint.cut_timestamps
        if 0 < value < fingerprint.duration_sec
    ]
    identity = stable_hash(
        {
            "fingerprint_version": fingerprint.version,
            "duration": fingerprint.duration_sec,
            "cuts": cut_positions,
            "density": fingerprint.cut_density,
            "beat_sync": fingerprint.beat_sync_ratio,
        }
    )[:12]
    magnitudes = (
        fingerprint.motion_sample_magnitudes
        or fingerprint.motion_magnitude_sequence
    )
    motion_median = float(np.median(magnitudes)) if magnitudes else 0.3
    motion_p75 = float(np.percentile(magnitudes, 75)) if magnitudes else 0.8
    balance, reversal_rate, hold_ratio = _horizontal_motion_stats(
        (
            fingerprint.motion_sample_directions
            or fingerprint.motion_direction_sequence
        ),
        magnitudes,
    )
    return EditingStyleProfile(
        id=f"reference-{identity}",
        name=name or f"Reference {identity[:6]}",
        source="reference",
        source_fingerprint_version=fingerprint.version,
        target_cut_density=max(0.2, min(8.0, fingerprint.cut_density)),
        median_shot_length=median,
        min_shot_length=max(0.08, min(p10, median)),
        max_shot_length=max(median, p90),
        duration_pattern=[max(0.2, min(4.0, value)) for value in pattern],
        normalized_cut_positions=cut_positions,
        beat_sync_target=fingerprint.beat_sync_ratio,
        hard_cut_ratio=fingerprint.hard_cut_ratio,
        scale_contrast_target=max(
            0.02, min(1.0, _scale_contrast(fingerprint.shot_scale_sequence))
        ),
        motion_change_ratio=_motion_change_ratio(
            fingerprint.motion_direction_sequence
        ),
        shot_scale_pattern=_percentile_pattern(fingerprint.shot_scale_sequence),
        motion_direction_pattern=fingerprint.motion_direction_sequence,
        motion_intensity_pattern=_percentile_pattern(
            fingerprint.motion_magnitude_sequence
        ),
        motion_curve=fingerprint.motion_curve,
        motion_peaks=fingerprint.motion_peaks,
        motion_zero_crossings=fingerprint.motion_zero_crossings,
        direction_reversals=fingerprint.direction_reversals,
        cut_carry_vectors=fingerprint.cut_carry_vectors,
        motion_median_target=motion_median,
        motion_p75_target=motion_p75,
        motion_dynamic_range_target=max(
            1.0, motion_p75 / max(motion_median, 0.08)
        ),
        hold_ratio_target=hold_ratio,
        direction_balance_target=balance,
        direction_reversal_target=reversal_rate,
        speed_ramp_density=len(fingerprint.speed_ramp_locations)
        / max(fingerprint.duration_sec, 1e-6),
        sound_event_density=fingerprint.sound_effect_density,
    )


__all__ = [
    "EDITING_STYLE_PROFILE_VERSION",
    "EditingStyleProfile",
    "compile_editing_style",
    "default_editing_style",
]
