"""Versioned editing-style profile compiled from a reference fingerprint.

The fingerprint describes what was measured.  This profile is the portable
editing grammar consumed by planning: rhythm, beat affinity, visual contrast,
and effect density.  Keeping the two contracts separate lets future reference
analyzers evolve without coupling Sequence Planner to one video or one model.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from studio.core.hashing import stable_hash

from .fingerprint import StyleFingerprint

EDITING_STYLE_PROFILE_VERSION = "editing-style-profile-1.0.0"


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

    # Visual grammar. These remain soft scoring signals, never hard filters.
    hard_cut_ratio: float = Field(1.0, ge=0, le=1)
    scale_contrast_target: float = Field(0.18, ge=0, le=1)
    motion_change_ratio: float = Field(0.55, ge=0, le=1)
    speed_ramp_density: float = Field(0.0, ge=0, le=4)
    sound_event_density: float = Field(0.0, ge=0, le=12)

    @model_validator(mode="after")
    def _valid_profile(self):
        if self.max_shot_length < self.min_shot_length:
            raise ValueError("max_shot_length 必须 >= min_shot_length")
        if not self.duration_pattern or any(value <= 0 for value in self.duration_pattern):
            raise ValueError("duration_pattern 必须包含正数")
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
