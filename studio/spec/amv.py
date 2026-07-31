"""AMVSpec —— the only execution-facing IR of the new chain (REFACTOR.md §5.3).

Deliberately excludes everything that only served the old human workflow:
candidate alternatives, preference metadata, fixed narrative-role enums,
captions, a generic renderer indirection, RecipeRef, migration metadata,
growth metadata, revision locks, and CreatedFrom pointers into the old
chain. A clip's motion is fully described by its own keyframes plus the
TransitionPair that spans it and its neighbour — there is no per-effect
"recipe id" indirection here; primitives are internal to the Fusion
compiler (REFACTOR.md §11).
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from studio.spec import SPEC_VERSION

TransitionDirection = Literal[
    "left", "right", "up", "down",
    "up-left", "up-right", "down-left", "down-right", "none",
]
RetimeMode = Literal["constant", "speed_ramp"]
RetimeInterpolation = Literal["nearest", "frame_blend", "optical_flow"]


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Timebase(_Base):
    num: int = Field(..., gt=0)
    den: int = Field(1, gt=0)

    @property
    def fps(self) -> float:
        return self.num / self.den


class Canvas(_Base):
    width: int = Field(..., gt=0)
    height: int = Field(..., gt=0)
    aspect: str


class MusicRef(_Base):
    path: str
    timeline_hash: str
    gain_db: float = 0.0


class SourceRange(_Base):
    in_sec: float = Field(..., ge=0)
    out_sec: float = Field(..., gt=0)

    @model_validator(mode="after")
    def _ordered(self):
        if self.out_sec <= self.in_sec:
            raise ValueError("source.out_sec must exceed in_sec")
        return self


class TimelinePlacement(_Base):
    in_sec: float = Field(..., ge=0)
    duration_sec: float = Field(..., gt=0)
    track: str = "V1"


class Framing(_Base):
    scale: float = Field(1.0, gt=0)
    center_x: float = 0.5
    center_y: float = 0.5
    rotation: float = 0.0


class TransformKeyframe(_Base):
    sec: float = Field(..., ge=0)
    center_x: float
    center_y: float
    scale: float = Field(..., gt=0)
    rotation: float = 0.0


class MotionBlurKeyframe(_Base):
    sec: float = Field(..., ge=0)
    shutter_angle: float = Field(..., ge=0, le=360)


class DirectionalBlurKeyframe(_Base):
    sec: float = Field(..., ge=0)
    angle: float
    strength: float = Field(..., ge=0, le=1)


class ExposureKeyframe(_Base):
    sec: float = Field(..., ge=0)
    gain: float


class EffectEvent(_Base):
    """One of the small set of code-level primitives owned by the compiler.

    ``kind`` must be one of the Fusion compositor's internal primitives
    (flash / rgb_split / glow / directional_blur / native_motion_blur /
    color_adjust). This is not a user- or Planner-selectable Recipe ID.
    """

    kind: str
    at_sec: float = Field(..., ge=0)
    duration_sec: float = Field(..., gt=0)
    params: dict = Field(default_factory=dict)


class RetimeSpeedKeyframe(_Base):
    sec: float = Field(..., ge=0)
    speed: float = Field(..., gt=0)


class Retime(_Base):
    mode: RetimeMode = "constant"
    interpolation: RetimeInterpolation = "nearest"
    speed_keyframes: list[RetimeSpeedKeyframe] = Field(default_factory=list)


class Motion(_Base):
    transform_keyframes: list[TransformKeyframe] = Field(default_factory=list)
    native_motion_blur_keyframes: list[MotionBlurKeyframe] = Field(default_factory=list)
    directional_blur_keyframes: list[DirectionalBlurKeyframe] = Field(default_factory=list)
    exposure_keyframes: list[ExposureKeyframe] = Field(default_factory=list)
    optional_effect_events: list[EffectEvent] = Field(default_factory=list)


class Clip(_Base):
    id: str
    asset_id: str
    shot_id: str | None = None

    # REFACTOR.md §17: identifies which ShotWindow this clip came from and
    # where its anchor sits, without carrying the full scoring/analysis
    # payload into the execution IR (that stays in the selection DB).
    window_id: str | None = None
    window_kind: str | None = None
    anchor_sec: float | None = None

    source: SourceRange
    timeline: TimelinePlacement

    framing: Framing = Field(default_factory=Framing)
    retime: Retime = Field(default_factory=Retime)
    motion: Motion = Field(default_factory=Motion)


class TransitionPair(_Base):
    id: str
    cut_sec: float = Field(..., ge=0)
    outgoing_clip_id: str
    incoming_clip_id: str
    direction: TransitionDirection
    outgoing_keyframes: list[TransformKeyframe] = Field(default_factory=list)
    incoming_keyframes: list[TransformKeyframe] = Field(default_factory=list)
    blur_keyframes: list[DirectionalBlurKeyframe] = Field(default_factory=list)
    safe_scale: float = Field(..., ge=1.0)
    overshoot: float = Field(0.0, ge=0)
    confidence: float = Field(..., ge=0.0, le=1.0)


class GlobalColor(_Base):
    lut_path: str | None = None
    params: dict = Field(default_factory=dict)


class RenderSettings(_Base):
    preset: str = "H.264 Master"
    output_path: str


class InputHashes(_Base):
    demo: str
    music: str | None = None
    materials_index: str


class AMVSpec(_Base):
    version: str = SPEC_VERSION
    id: str
    input_hashes: InputHashes

    timebase: Timebase
    canvas: Canvas
    duration_sec: float = Field(..., gt=0)

    music: MusicRef
    clips: list[Clip] = Field(default_factory=list)
    transition_pairs: list[TransitionPair] = Field(default_factory=list)
    global_color: GlobalColor = Field(default_factory=GlobalColor)
    render: RenderSettings

    @model_validator(mode="after")
    def _clip_ids_unique(self):
        ids = [c.id for c in self.clips]
        if len(ids) != len(set(ids)):
            raise ValueError("clip id must be unique")
        clip_ids = set(ids)
        for pair in self.transition_pairs:
            if pair.outgoing_clip_id not in clip_ids or pair.incoming_clip_id not in clip_ids:
                raise ValueError(
                    f"transition_pair {pair.id} references a clip id not present in clips"
                )
        return self

    def clip_by_id(self, clip_id: str) -> Clip | None:
        return next((c for c in self.clips if c.id == clip_id), None)


__all__ = [
    "AMVSpec",
    "Canvas",
    "Clip",
    "DirectionalBlurKeyframe",
    "EffectEvent",
    "ExposureKeyframe",
    "Framing",
    "GlobalColor",
    "InputHashes",
    "Motion",
    "MotionBlurKeyframe",
    "MusicRef",
    "RenderSettings",
    "Retime",
    "RetimeSpeedKeyframe",
    "SourceRange",
    "Timebase",
    "TimelinePlacement",
    "TransformKeyframe",
    "TransitionDirection",
    "TransitionPair",
]
