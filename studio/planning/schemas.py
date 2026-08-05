"""Minimal per-window contract GlobalSequencePlanner/MotionPlanner actually need.

Shots now arrive pre-selected by ID from the external anime-shot-library
catalog (already curated for quality/character/action there), so this is
deliberately not the old CV-derived ``ShotWindow`` (technical/portrait/
action evidence, sub-window search inside a Shot). It only carries the
narrow set of fields the beam search's continuity scoring and the AMVSpec
builder read: identity/dedup keys, timing, and entry/exit motion direction.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

MotionDirection = Literal[
    "left", "right", "up", "down",
    "up-left", "up-right", "down-left", "down-right", "none",
]

WindowKind = Literal["curated", "generic"]


@dataclass(frozen=True)
class TechnicalProfile:
    passed: bool = True


@dataclass(frozen=True)
class SubjectProfile:
    identity_cluster: str | None = None
    series_scope: str | None = None
    subject_scale: float | None = None
    subject_center: tuple[float, float] | None = None


@dataclass(frozen=True)
class EditabilityProfile:
    entry_motion: MotionDirection = "none"
    exit_motion: MotionDirection = "none"


@dataclass(frozen=True)
class ShotWindow:
    id: str
    shot_id: str
    asset_id: str
    start_sec: float
    end_sec: float
    anchor_sec: float
    kind: WindowKind = "curated"

    technical: TechnicalProfile = field(default_factory=TechnicalProfile)
    subject: SubjectProfile = field(default_factory=SubjectProfile)
    editability: EditabilityProfile = field(default_factory=EditabilityProfile)

    @property
    def duration_sec(self) -> float:
        return self.end_sec - self.start_sec


__all__ = [
    "EditabilityProfile",
    "MotionDirection",
    "ShotWindow",
    "SubjectProfile",
    "TechnicalProfile",
    "WindowKind",
]
