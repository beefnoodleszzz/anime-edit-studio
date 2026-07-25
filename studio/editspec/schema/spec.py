"""EditSpec —— 系统的稳定中间表示（IR）。

设计原则（TARGET_ARCHITECTURE §3.1，均由实测支撑）：

A1  **秒是权威**，帧由 compiler 换算。源素材 23.976 与交付时基不同，
    帧制 IR 必然累积漂移（AGENTS.md P4）。
A2  **引用而非路径**。clip 引用 asset_id/shot_id，媒体路径由 Execution 层解析，
    素材移动不会让历史 EditSpec 失效。
A3  **Recipe 而非实现**。效果/调色/音效只写 recipe_id + 参数，永不写节点图。
A4  **决策元数据随行**。confidence / reasoning / alternatives 支撑 Critic 与偏好学习。
A5  **可 Diff**。clip.id 稳定，修订以 patch 表达，不重建整条时间线。

Phase 1 只启用最小子集：source / timeline / track。
framing / retime / effects / color / audio 字段已定义但被 validator 拦截，
直到对应 capability 在 resolve_capabilities.yaml 中转为 verified（AGENTS.md R3）。
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SPEC_VERSION = "2.0.0"

ShotRole = Literal[
    "opening", "character_intro", "build", "pre_drop", "impact", "release", "ending",
]
TrackKind = Literal["video", "audio", "subtitle"]
DecisionSource = Literal["ai", "user", "rule"]


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")   # 字段随意增长是 v1 的病，这里禁止


class Timebase(_Base):
    """交付时基。用有理数精确表达，禁止裸 float fps。"""

    num: int = Field(..., gt=0, description="每秒帧数的分子，如 24000")
    den: int = Field(1, gt=0, description="分母；23.976 用 1001")
    drop_frame: bool = False

    @property
    def fps(self) -> float:
        return self.num / self.den


class Canvas(_Base):
    width: int = Field(..., gt=0)
    height: int = Field(..., gt=0)
    aspect: str | None = None


class Track(_Base):
    id: str = Field(..., description="V1 / A1 …")
    kind: TrackKind
    role: str | None = Field(None, description="music / sfx / source / overlay")


class SourceRange(_Base):
    """源素材上的取材区间，秒制，基于素材自身时基。"""

    in_sec: float = Field(..., ge=0)
    out_sec: float = Field(..., gt=0)

    @model_validator(mode="after")
    def _check_order(self):
        if self.out_sec <= self.in_sec:
            raise ValueError(f"source.out_sec({self.out_sec}) 必须大于 in_sec({self.in_sec})")
        return self

    @property
    def duration_sec(self) -> float:
        return self.out_sec - self.in_sec


class TimelinePlacement(_Base):
    """成片时间线上的落点，秒制，基于交付时基。"""

    in_sec: float = Field(..., ge=0)
    duration_sec: float = Field(..., gt=0)
    track: str = "V1"

    @property
    def out_sec(self) -> float:
        return self.in_sec + self.duration_sec


# ---------- 以下为 Phase 1 之后才启用的表达能力 ----------

class Framing(_Base):
    mode: Literal["fit", "crop", "smart_reframe", "magic_mask_track", "manual"] = "crop"
    subject: str | None = None
    offset_x: float = 0.0
    offset_y: float = 0.0
    scale: float = Field(1.0, gt=0)


class CameraMove(_Base):
    move: Literal["none", "push_in", "push_out", "pan_left", "pan_right"] = "none"
    from_scale: float = Field(1.0, gt=0)
    to_scale: float = Field(1.0, gt=0)
    curve: Literal["linear", "ease_in", "ease_out", "ease_in_out"] = "linear"


class Retime(_Base):
    type: Literal["constant", "speed_ramp"] = "constant"
    speed: float = Field(1.0, gt=0, description="constant 时的倍率")
    entry_speed: float = Field(1.0, gt=0)
    impact_speed: float = Field(1.0, gt=0)
    exit_speed: float = Field(1.0, gt=0)
    impact_at_sec: float | None = Field(None, ge=0, description="相对 clip 起点")
    interpolation: Literal["nearest", "frame_blend", "optical_flow"] = "nearest"


class TransitionEnd(_Base):
    recipe: str = "hard_cut"
    duration_sec: float = Field(0.0, ge=0)
    params: dict = Field(default_factory=dict)


class Transition(_Base):
    in_: TransitionEnd = Field(default_factory=TransitionEnd, alias="in")
    out: TransitionEnd = Field(default_factory=TransitionEnd)

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class RecipeRef(_Base):
    """AGENTS.md R4：AI 只能输出 recipe id + 参数，永不输出实现。"""

    recipe: str
    params: dict = Field(default_factory=dict)


class SfxCue(_Base):
    recipe: str
    at_sec: float = Field(0.0, ge=0, description="相对 clip 起点")
    gain_db: float = 0.0


class VolumePoint(_Base):
    sec: float = Field(..., ge=0)
    db: float


class ClipAudio(_Base):
    sfx: list[SfxCue] = Field(default_factory=list)
    source_gain_db: float | None = None
    volume_automation: list[VolumePoint] = Field(default_factory=list)


class Decision(_Base):
    """这个镜头为什么在这里 —— 供 Critic 与偏好学习消费。"""

    source: DecisionSource = "rule"
    confidence: float | None = Field(None, ge=0, le=1)
    reasoning: str | None = None
    alternatives: list[str] = Field(default_factory=list, description="落选 shot_id，用于 pairwise")
    locked: bool = Field(False, description="用户锁定后 Revision 不得改动")


class Clip(_Base):
    id: str = Field(..., description="稳定 ID，diff 的锚点，禁止重排后重新编号")
    asset_id: str
    shot_id: str | None = None

    source: SourceRange
    timeline: TimelinePlacement

    role: ShotRole | None = None

    framing: Framing = Field(default_factory=Framing)
    camera: CameraMove = Field(default_factory=CameraMove)
    retime: Retime = Field(default_factory=Retime)
    transition: Transition = Field(default_factory=Transition)
    effects: list[RecipeRef] = Field(default_factory=list)
    color: RecipeRef | None = None
    audio: ClipAudio = Field(default_factory=ClipAudio)

    decision: Decision = Field(default_factory=Decision)

    @model_validator(mode="after")
    def _check_speed_consistency(self):
        """时长自洽：源区间 ÷ 速度 应约等于时间线时长。

        这是 v1 从未校验、导致成片时长莫名对不上的一类 bug。
        """
        if self.retime.type == "constant":
            expected = self.source.duration_sec / self.retime.speed
            actual = self.timeline.duration_sec
            # 容差 1 帧 @24fps，避免过严
            if abs(expected - actual) > 1 / 24:
                raise ValueError(
                    f"clip {self.id}: 源时长 {self.source.duration_sec:.3f}s ÷ 速度 "
                    f"{self.retime.speed} = {expected:.3f}s，"
                    f"与时间线时长 {actual:.3f}s 不符"
                )
        return self


class AudioLayer(_Base):
    id: str
    asset_id: str | None = None
    path: str | None = Field(None, description="外部音乐/音效；素材库内的用 asset_id")
    track: str = "A1"
    timeline_in_sec: float = Field(0.0, ge=0)
    source_in_sec: float = Field(0.0, ge=0)
    duration_sec: float | None = Field(None, gt=0)
    gain_db: float = 0.0

    @model_validator(mode="after")
    def _need_a_source(self):
        if not self.asset_id and not self.path:
            raise ValueError(f"audio layer {self.id}: asset_id 与 path 必须提供其一")
        return self


class Marker(_Base):
    sec: float = Field(..., ge=0)
    kind: str = "note"
    note: str = ""
    clip_id: str | None = Field(None, description="写入 Resolve marker customData，实现 IR↔Resolve 双向定位")


class SpecMeta(_Base):
    recipe_versions: dict[str, str] = Field(default_factory=dict)
    model_versions: dict[str, str] = Field(default_factory=dict)
    pipeline_version: str = SPEC_VERSION


class CreatedFrom(_Base):
    director_plan: str | None = None
    style_fingerprint: str | None = None
    music_map: str | None = None
    preference_profile: str | None = None


class EditSpec(_Base):
    spec_version: str = SPEC_VERSION
    id: str
    revision: int = Field(1, ge=1, description="每次 diff 应用后 +1")

    created_from: CreatedFrom = Field(default_factory=CreatedFrom)
    timebase: Timebase
    canvas: Canvas

    tracks: list[Track] = Field(
        default_factory=lambda: [
            Track(id="V1", kind="video"),
            Track(id="A1", kind="audio", role="music"),
        ]
    )
    clips: list[Clip] = Field(default_factory=list)
    audio: list[AudioLayer] = Field(default_factory=list)
    markers: list[Marker] = Field(default_factory=list)

    meta: SpecMeta = Field(default_factory=SpecMeta)

    # ---------- 派生量（不存储，永远从 clips 算，杜绝不一致） ----------

    @property
    def duration_sec(self) -> float:
        ends = [c.timeline.out_sec for c in self.clips]
        ends += [
            a.timeline_in_sec + a.duration_sec
            for a in self.audio
            if a.duration_sec is not None
        ]
        return max(ends, default=0.0)

    def clip_by_id(self, clip_id: str) -> Clip | None:
        return next((c for c in self.clips if c.id == clip_id), None)

    def clips_on_track(self, track: str) -> list[Clip]:
        return sorted(
            (c for c in self.clips if c.timeline.track == track),
            key=lambda c: c.timeline.in_sec,
        )

    def video_tracks(self) -> list[str]:
        return [t.id for t in self.tracks if t.kind == "video"]
