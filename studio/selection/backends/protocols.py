"""Protocol/Adapter boundary for open-source vision backends (REFACTOR.md §7).

Every heavy or optional model is accessed through one of these Protocols so
that: (1) tests can inject a Fake backend without downloading weights, and
(2) an unavailable backend degrades the caller (lower confidence, recorded
in ``ShotWindow.analysis``) instead of raising or silently pretending to
have run.
"""
from __future__ import annotations

from typing import Protocol

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from studio.selection.schemas import BoundingBox, GazeDirection


class BackendStatus(BaseModel):
    """One row of ``aes doctor vision`` output (REFACTOR.md §18)."""

    model_config = ConfigDict(extra="forbid")

    backend: str
    available: bool
    version: str | None = None
    device: str = "cpu"
    weights_path: str | None = None
    fallback: str | None = None


class FaceDetection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bbox: BoundingBox
    frontal_probability: float = Field(..., ge=0.0, le=1.0)
    eyes_visible_ratio: float = Field(..., ge=0.0, le=1.0)
    gaze: GazeDirection = "uncertain"
    touches_frame_edge: bool = False
    confidence: float = Field(..., ge=0.0, le=1.0)


class AnimeFaceBackend(Protocol):
    """Anime-domain face detector (REFACTOR.md §7.1). Not WD-tagger tags."""

    status: BackendStatus

    def detect(self, frame: np.ndarray) -> list[FaceDetection]: ...


class DenseEmbeddingBackend(Protocol):
    """Whole-frame / subject-crop / face-crop embeddings (REFACTOR.md §7.2)."""

    status: BackendStatus

    def embed(self, frame: np.ndarray) -> np.ndarray | None: ...


class TrackPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sec: float = Field(..., ge=0.0)
    bbox: BoundingBox
    confidence: float = Field(..., ge=0.0, le=1.0)


class SubjectTrackerBackend(Protocol):
    """Refinement-only point/box tracker (CoTracker/SAM2 territory, REFACTOR.md §7.3)."""

    status: BackendStatus

    def track(
        self, frames: list[np.ndarray], initial_bbox: BoundingBox
    ) -> list[TrackPoint]: ...


class VideoQualityBackend(Protocol):
    """Auxiliary technical/aesthetic quality only (DOVER, REFACTOR.md §7.4).

    Must never override hard gates (subtitles, wrong character, unsafe crop,
    no stable landing) — callers may only use this as one more soft signal.
    """

    status: BackendStatus

    def score(self, frames: list[np.ndarray]) -> dict[str, float]: ...


__all__ = [
    "AnimeFaceBackend",
    "BackendStatus",
    "DenseEmbeddingBackend",
    "FaceDetection",
    "SubjectTrackerBackend",
    "TrackPoint",
    "VideoQualityBackend",
]
