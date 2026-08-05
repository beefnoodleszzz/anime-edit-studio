"""TimelineSlot: what the RhythmStyleMapper hands to the selection stage.

Not a stored spec object (REFACTOR.md's schemas are ReferenceBlueprint /
MusicTimeline / AMVSpec) — an internal planning structure consumed by
GlobalSequencePlanner and MotionPlanner within the same run.

REFACTOR.md §5.2 (SlotRequirement): a slot must carry the Demo's actual
screen-language requirements, not just target_energy/hold/entry_motion, so
selection.reference_fit / sequence_scoring have something real to compare a
ShotWindow against.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from studio.planning.schemas import MotionDirection

EntryMotion = Literal["carry", "reverse", "reset", "none"]
SlotKind = Literal["portrait", "eye", "action", "impact", "transition", "hold", "generic"]


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

    # REFACTOR.md §5.2 additions.
    slot_kind: SlotKind = "generic"
    slot_kind_probabilities: dict[str, float] = Field(default_factory=dict)

    target_subject_scale: float | None = Field(None, ge=0, le=1)
    target_subject_center: tuple[float, float] | None = None
    target_shot_scale: float | None = Field(None, ge=0, le=1)
    target_brightness: float | None = Field(None, ge=0, le=1)
    target_dominant_color: str | None = None
    target_color_embedding: list[float] | None = None

    required_identity: str | None = None
    required_series_scope: str | None = None
    preferred_subject_count: int | None = Field(None, ge=0)

    entry_direction: MotionDirection = "none"
    exit_direction: MotionDirection = "none"
    source_motion_preference: MotionDirection = "none"

    required_stable_frames: int = Field(0, ge=0)
    hero_frame_required: bool = False
    reference_shot_index: int | None = Field(None, ge=0)

    @property
    def end_sec(self) -> float:
        return self.start_sec + self.duration_sec


__all__ = ["EntryMotion", "SlotKind", "TimelineSlot"]
