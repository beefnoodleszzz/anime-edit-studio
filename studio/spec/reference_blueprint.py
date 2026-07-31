"""ReferenceBlueprint —— what the Demo actually does, measured, not assumed.

REFACTOR.md §5.1. A flattened Demo video cannot distinguish a shot's native
camera motion from post-added virtual camera work with certainty, so every
motion estimate here carries ``confidence`` and ``evidence`` alongside the
point value. Low-confidence estimates must make the Planner fall back to
conservative behaviour instead of being treated as ground truth.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from studio.spec import SPEC_VERSION

CutType = Literal["hard_cut", "flash_cut", "whip_hidden_cut", "dissolve", "unknown"]
CutRelation = Literal["carry", "reverse", "reset", "unknown"]
TransitionDirection = Literal[
    "left", "right", "up", "down",
    "up-left", "up-right", "down-left", "down-right", "none",
]


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Estimate(_Base):
    """A measured quantity that cannot be asserted with certainty.

    ``evidence`` names the signals that produced ``value`` (e.g.
    ``["lk_ransac_inlier_ratio=0.82", "ecc_fallback=false"]``) so a low
    confidence can be diagnosed instead of silently trusted.
    """

    value: float
    confidence: float = Field(..., ge=0.0, le=1.0)
    evidence: list[str] = Field(default_factory=list)


class TechnicalProfile(_Base):
    width: int = Field(..., gt=0)
    height: int = Field(..., gt=0)
    fps_num: int = Field(..., gt=0)
    fps_den: int = Field(1, gt=0)
    duration_sec: float = Field(..., gt=0)
    aspect: str

    @property
    def fps(self) -> float:
        return self.fps_num / self.fps_den


class ShotObservation(_Base):
    index: int = Field(..., ge=0)
    start_sec: float = Field(..., ge=0)
    end_sec: float = Field(..., gt=0)
    duration_sec: float = Field(..., gt=0)
    visual_energy: float = Field(..., ge=0)
    brightness: float = Field(..., ge=0)
    subject_scale: float | None = Field(None, ge=0)
    subject_center: tuple[float, float] | None = None
    native_motion_estimate: Estimate
    global_motion_estimate: Estimate
    motion_confidence: float = Field(..., ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _range_is_ordered(self):
        if self.end_sec <= self.start_sec:
            raise ValueError(f"shot {self.index}: end_sec must exceed start_sec")
        return self


class CutObservation(_Base):
    sec: float = Field(..., ge=0)
    type: CutType
    confidence: float = Field(..., ge=0.0, le=1.0)
    nearest_music_event: str | None = None
    music_offset_sec: float | None = None
    outgoing_motion: Estimate | None = None
    incoming_motion: Estimate | None = None
    relation: CutRelation = "unknown"
    visual_peak_offset_sec: float | None = None
    settle_offset_sec: float | None = None


class MotionSample(_Base):
    sec: float = Field(..., ge=0)
    tx: float
    ty: float
    log_scale: float
    rotation: float
    velocity: float = Field(..., ge=0)
    acceleration: float
    confidence: float = Field(..., ge=0.0, le=1.0)


class TransitionPairObservation(_Base):
    cut_sec: float = Field(..., ge=0)
    relation: CutRelation
    direction: TransitionDirection
    anticipation_sec: float = Field(..., ge=0)
    release_sec: float = Field(..., ge=0)
    outgoing_envelope: list[float] = Field(default_factory=list)
    incoming_envelope: list[float] = Field(default_factory=list)
    overshoot: float = Field(0.0, ge=0)
    blur_envelope: list[float] = Field(default_factory=list)
    confidence: float = Field(..., ge=0.0, le=1.0)


class StyleSummary(_Base):
    """Statistical grammar of the Demo, portable across a different target track.

    This is what Style Transfer mode (§7.2) migrates when the target music
    does not match the Demo's timeline 1:1 — never the Demo's raw timestamps.
    """

    cut_density: float = Field(..., ge=0, description="cuts per second")
    shot_duration_distribution: dict[str, float] = Field(default_factory=dict)
    music_sync_distribution: dict[str, float] = Field(default_factory=dict)
    motion_coverage: float = Field(..., ge=0, le=1)
    hold_ratio: float = Field(..., ge=0, le=1)
    direction_distribution: dict[str, float] = Field(default_factory=dict)
    reversal_ratio: float = Field(..., ge=0, le=1)
    scale_motion_ratio: float = Field(..., ge=0, le=1)
    blur_usage: float = Field(..., ge=0, le=1)
    visual_peak_delay_distribution: dict[str, float] = Field(default_factory=dict)
    settle_delay_distribution: dict[str, float] = Field(default_factory=dict)


class ReferenceBlueprint(_Base):
    version: str = SPEC_VERSION
    source_hash: str

    technical: TechnicalProfile
    music_timeline_ref: str | None = Field(
        None, description="hash of the Demo's own extracted-audio MusicTimeline, if analyzed"
    )

    shots: list[ShotObservation] = Field(default_factory=list)
    cuts: list[CutObservation] = Field(default_factory=list)
    motion_curve: list[MotionSample] = Field(default_factory=list)
    transition_pairs: list[TransitionPairObservation] = Field(default_factory=list)
    style_summary: StyleSummary

    @model_validator(mode="after")
    def _shots_are_ordered_and_indexed(self):
        for expected, shot in enumerate(self.shots):
            if shot.index != expected:
                raise ValueError(
                    f"shots must be contiguously indexed from 0; got {shot.index} at position {expected}"
                )
        return self


__all__ = [
    "CutObservation",
    "CutRelation",
    "CutType",
    "Estimate",
    "MotionSample",
    "ReferenceBlueprint",
    "ShotObservation",
    "StyleSummary",
    "TechnicalProfile",
    "TransitionDirection",
    "TransitionPairObservation",
]
