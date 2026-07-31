"""MusicTimeline —— structural analysis of a target track (REFACTOR.md §5.2).

Not every onset becomes a visual instruction: ``accents`` is a curated,
typed subset of musically meaningful moments that the RhythmStyleMapper is
allowed to react to. Raw onsets/beats stay available for continuous curves
(hold vs. motion windows) but must not be individually promoted to cuts.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from studio.spec import SPEC_VERSION

AccentKind = Literal[
    "beat",
    "downbeat",
    "impact",
    "section_boundary",
    "break_entry",
    "break_exit",
    "riser_peak",
    "silence_hit",
]


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TempoCandidate(_Base):
    bpm: float = Field(..., gt=0)
    confidence: float = Field(..., ge=0.0, le=1.0)


class Section(_Base):
    start_sec: float = Field(..., ge=0)
    end_sec: float = Field(..., gt=0)
    label: str

    @model_validator(mode="after")
    def _ordered(self):
        if self.end_sec <= self.start_sec:
            raise ValueError("section end_sec must exceed start_sec")
        return self


class Interval(_Base):
    start_sec: float = Field(..., ge=0)
    end_sec: float = Field(..., gt=0)

    @model_validator(mode="after")
    def _ordered(self):
        if self.end_sec <= self.start_sec:
            raise ValueError("interval end_sec must exceed start_sec")
        return self


class Accent(_Base):
    sec: float = Field(..., ge=0)
    kind: AccentKind
    strength: float = Field(..., ge=0.0, le=1.0)
    confidence: float = Field(..., ge=0.0, le=1.0)
    anticipation_sec: float = Field(0.0, ge=0)
    release_sec: float = Field(0.0, ge=0)


class MusicTimeline(_Base):
    version: str = SPEC_VERSION
    source_hash: str
    duration_sec: float = Field(..., gt=0)

    tempo_candidates: list[TempoCandidate] = Field(default_factory=list)
    selected_tempo: float = Field(..., gt=0)
    tempo_confidence: float = Field(..., ge=0.0, le=1.0)

    beats: list[float] = Field(default_factory=list)
    downbeats: list[float] = Field(default_factory=list)
    bars: list[float] = Field(default_factory=list)
    onsets: list[float] = Field(default_factory=list)

    sections: list[Section] = Field(default_factory=list)
    energy_curve: list[tuple[float, float]] = Field(default_factory=list)
    spectral_novelty: list[tuple[float, float]] = Field(default_factory=list)

    silences: list[Interval] = Field(default_factory=list)
    breaks: list[Interval] = Field(default_factory=list)
    risers: list[Interval] = Field(default_factory=list)
    phrases: list[Interval] = Field(default_factory=list)

    accents: list[Accent] = Field(default_factory=list)

    @model_validator(mode="after")
    def _monotonic_series(self):
        for name in ("beats", "downbeats", "bars", "onsets"):
            series = getattr(self, name)
            if series != sorted(series):
                raise ValueError(f"{name} must be sorted ascending")
        return self


__all__ = [
    "Accent",
    "AccentKind",
    "Interval",
    "MusicTimeline",
    "Section",
    "TempoCandidate",
]
