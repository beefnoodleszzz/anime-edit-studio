"""ShotWindow schema (REFACTOR.md §5.1): the selection stage's core unit.

Replaces "whole Shot" as the thing GlobalSequencePlanner chooses between.
A ShotWindow is a precise ``[start_sec, end_sec)`` slice inside one Shot,
carrying the technical/portrait/action/editability evidence needed to rank
it for a specific TimelineSlot.

Every uncertain field carries ``confidence`` + ``evidence`` alongside it
(REFACTOR.md §5.1: "不得把估计值伪装成真实标签") rather than a bare number.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

WindowKind = Literal[
    "portrait_hold",
    "direct_gaze",
    "turn_to_camera",
    "eye_reveal",
    "hero_pose",
    "anticipation",
    "action_peak",
    "impact",
    "transformation",
    "hero_landing",
    "generic",
]

GazeDirection = Literal[
    "viewer", "left", "right", "up", "down", "closed", "occluded", "uncertain",
]

MotionDirection = Literal[
    "left", "right", "up", "down",
    "up-left", "up-right", "down-left", "down-right", "none",
]


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BoundingBox(_Base):
    """Normalized [0,1] box, origin top-left, same convention as WD/OpenCV crops."""

    x: float = Field(..., ge=0.0, le=1.0)
    y: float = Field(..., ge=0.0, le=1.0)
    w: float = Field(..., gt=0.0, le=1.0)
    h: float = Field(..., gt=0.0, le=1.0)


class TechnicalProfile(_Base):
    passed: bool
    failure_reasons: list[str] = Field(default_factory=list)
    bad_frame_ratio: float = Field(0.0, ge=0.0, le=1.0)
    subtitle_frame_ratio: float = Field(0.0, ge=0.0, le=1.0)
    watermark_probability: float = Field(0.0, ge=0.0, le=1.0)
    black_clip_ratio: float = Field(0.0, ge=0.0, le=1.0)
    white_clip_ratio: float = Field(0.0, ge=0.0, le=1.0)
    sharpness_p10: float = Field(0.0, ge=0.0)
    compression_score: float = Field(0.0, ge=0.0, le=1.0)
    subject_visible_ratio: float = Field(0.0, ge=0.0, le=1.0)


class SubjectProfile(_Base):
    bbox_at_anchor: BoundingBox | None = None
    mean_bbox: BoundingBox | None = None
    subject_scale: float | None = Field(None, ge=0.0, le=1.0)
    subject_center: tuple[float, float] | None = None
    track_confidence: float = Field(0.0, ge=0.0, le=1.0)
    safe_crop_ratio: float = Field(0.0, ge=0.0, le=1.0)
    identity_cluster: str | None = None
    series_scope: str | None = None


class PortraitProfile(_Base):
    face_visible_ratio: float = Field(0.0, ge=0.0, le=1.0)
    frontal_probability: float = Field(0.0, ge=0.0, le=1.0)
    viewer_gaze_probability: float = Field(0.0, ge=0.0, le=1.0)
    gaze_direction: GazeDirection = "uncertain"
    eye_visible_ratio: float = Field(0.0, ge=0.0, le=1.0)
    expression_strength: float = Field(0.0, ge=0.0, le=1.0)
    temporal_stability: float = Field(0.0, ge=0.0, le=1.0)
    composition_score: float = Field(0.0, ge=0.0, le=1.0)
    portrait_score: float = Field(0.0, ge=0.0, le=1.0)


class ActionProfile(_Base):
    global_motion_peak: float = Field(0.0, ge=0.0)
    residual_motion_peak: float = Field(0.0, ge=0.0)
    acceleration_peak: float = 0.0
    silhouette_change: float = Field(0.0, ge=0.0, le=1.0)
    brightness_impulse: float = Field(0.0, ge=0.0, le=1.0)
    color_impulse: float = Field(0.0, ge=0.0, le=1.0)
    anticipation_sec: float | None = None
    impact_sec: float | None = None
    recovery_sec: float | None = None
    landing_sec: float | None = None
    landing_score: float = Field(0.0, ge=0.0, le=1.0)
    action_score: float = Field(0.0, ge=0.0, le=1.0)


class EditabilityProfile(_Base):
    handle_before_sec: float = Field(0.0, ge=0.0)
    handle_after_sec: float = Field(0.0, ge=0.0)
    stable_before_sec: float = Field(0.0, ge=0.0)
    stable_after_sec: float = Field(0.0, ge=0.0)
    entry_motion: MotionDirection = "none"
    exit_motion: MotionDirection = "none"
    motion_compatibility: float = Field(0.5, ge=0.0, le=1.0)
    duration_fit: float = Field(0.5, ge=0.0, le=1.0)
    editability_score: float = Field(0.0, ge=0.0, le=1.0)


class AnalysisMeta(_Base):
    versions: dict[str, str] = Field(default_factory=dict)
    confidence: float = Field(0.5, ge=0.0, le=1.0)
    evidence: list[str] = Field(default_factory=list)


class ShotWindow(_Base):
    id: str
    shot_id: str
    asset_id: str
    start_sec: float = Field(..., ge=0.0)
    end_sec: float = Field(..., gt=0.0)
    anchor_sec: float = Field(..., ge=0.0)
    kind: WindowKind = "generic"

    technical: TechnicalProfile
    subject: SubjectProfile = Field(default_factory=SubjectProfile)
    portrait: PortraitProfile = Field(default_factory=PortraitProfile)
    action: ActionProfile = Field(default_factory=ActionProfile)
    editability: EditabilityProfile = Field(default_factory=EditabilityProfile)
    analysis: AnalysisMeta = Field(default_factory=AnalysisMeta)

    @model_validator(mode="after")
    def _ordered(self):
        if self.end_sec <= self.start_sec:
            raise ValueError("end_sec must exceed start_sec")
        if not (self.start_sec <= self.anchor_sec <= self.end_sec):
            raise ValueError("anchor_sec must lie within [start_sec, end_sec]")
        return self

    @property
    def duration_sec(self) -> float:
        return self.end_sec - self.start_sec


__all__ = [
    "ActionProfile",
    "AnalysisMeta",
    "BoundingBox",
    "EditabilityProfile",
    "GazeDirection",
    "MotionDirection",
    "PortraitProfile",
    "ShotWindow",
    "SubjectProfile",
    "TechnicalProfile",
    "WindowKind",
]
