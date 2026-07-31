"""TimelineSlot: what the RhythmStyleMapper hands to the selection stage.

Not a stored spec object (REFACTOR.md's schemas are ReferenceBlueprint /
MusicTimeline / AMVSpec) — an internal planning structure consumed by
GlobalSequencePlanner and MotionPlanner within the same run.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

EntryMotion = Literal["carry", "reverse", "reset", "none"]


class TimelineSlot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    index: int = Field(..., ge=0)
    start_sec: float = Field(..., ge=0)
    duration_sec: float = Field(..., gt=0)
    target_energy: float = Field(..., ge=0, le=1)
    hold: bool = False
    entry_motion: EntryMotion = "none"
    music_event_sec: float | None = None
    music_event_kind: str | None = None

    @property
    def end_sec(self) -> float:
        return self.start_sec + self.duration_sec


__all__ = ["EntryMotion", "TimelineSlot"]
