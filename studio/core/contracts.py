"""Typed cross-layer contracts.  These are data, never hidden prompt formats."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ConfidenceValue(_Base):
    value: float | str | bool | list | dict | None
    confidence: float = Field(..., ge=0, le=1)
    method: str
    model_version: str


class SubjectBox(_Base):
    """Normalised xywh in source coordinates."""

    x: float = Field(..., ge=0, le=1)
    y: float = Field(..., ge=0, le=1)
    width: float = Field(..., gt=0, le=1)
    height: float = Field(..., gt=0, le=1)
    confidence: float = Field(..., ge=0, le=1)


class ShotAnalysis(_Base):
    shot_id: str
    analysis_version: str
    shot_scale: ConfidenceValue
    subject_motion: ConfidenceValue
    compression_score: ConfidenceValue
    color_palette: ConfidenceValue
    subtitle_region: ConfidenceValue
    pose_quality: ConfidenceValue
    face_visibility: ConfidenceValue
    eye_visibility: ConfidenceValue
    visual_energy: ConfidenceValue
    composition: ConfidenceValue
    image_quality: ConfidenceValue
    blur_score: ConfidenceValue
    camera_motion: ConfidenceValue
    cutability: ConfidenceValue


__all__ = ["ConfidenceValue", "ShotAnalysis", "SubjectBox"]
