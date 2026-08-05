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

# Single source of truth for turning a bucketed MotionDirection back into a
# unit-ish 2D vector — every module that reasons about motion continuity
# (candidate scoring, beam continuity, transition keyframes) must agree on
# the same geometry, or "same direction" silently means different things in
# different places.
DIRECTION_VECTORS: dict[MotionDirection, tuple[float, float]] = {
    "none": (0.0, 0.0),
    "left": (-1.0, 0.0), "right": (1.0, 0.0),
    "up": (0.0, -1.0), "down": (0.0, 1.0),
    "up-left": (-0.7071, -0.7071), "up-right": (0.7071, -0.7071),
    "down-left": (-0.7071, 0.7071), "down-right": (0.7071, 0.7071),
}


def direction_cosine(a: MotionDirection, b: MotionDirection) -> float:
    """Cosine similarity in [-1, 1] between two bucketed directions. Either
    side being "none" (no measured motion) means no directional claim can be
    made — returns 0.0 (neutral), not a guess."""
    if a == "none" or b == "none":
        return 0.0
    va, vb = DIRECTION_VECTORS[a], DIRECTION_VECTORS[b]
    return va[0] * vb[0] + va[1] * vb[1]


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
    "DIRECTION_VECTORS",
    "EditabilityProfile",
    "MotionDirection",
    "ShotWindow",
    "SubjectProfile",
    "TechnicalProfile",
    "WindowKind",
    "direction_cosine",
]
