"""EditSpec 校验 —— AGENTS.md R2 的执行点。

任何 EditSpec 在进入 Execution 层前必须通过校验。
禁止"先执行，出错再说"。

校验分三类：
    STRUCTURE   时间轴自洽、ID 唯一、轨道存在
    MEDIA       asset 可解析且文件可达
    CAPABILITY  用到的能力在 resolve_capabilities.yaml 中已 verified（R3）

设计取舍：校验器返回**全部**问题而非首个，
因为 AI 生成的 spec 往往一次错一批，逐个报错会让修订循环变得很慢。
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable

from studio.core.timecode import Timebase as CoreTimebase
from studio.editspec.schema import EditSpec
from studio.execution.recipes import RecipeRegistry

# 1 帧的容差基准；比这更细的重叠属于浮点噪声，不算错误
_EPS_FRAMES = 0.5


class Severity(str, Enum):
    ERROR = "error"      # 阻断执行
    WARNING = "warning"  # 允许执行但需记录


@dataclass(frozen=True)
class Issue:
    code: str
    severity: Severity
    message: str
    clip_id: str | None = None

    def __str__(self) -> str:
        where = f" [{self.clip_id}]" if self.clip_id else ""
        return f"{self.severity.value.upper()} {self.code}{where}: {self.message}"


@dataclass
class ValidationResult:
    issues: list[Issue] = field(default_factory=list)

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity is Severity.ERROR]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.severity is Severity.WARNING]

    @property
    def ok(self) -> bool:
        return not self.errors

    def raise_if_failed(self) -> None:
        if not self.ok:
            raise ValidationError(self)

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "error_count": len(self.errors),
            "warning_count": len(self.warnings),
            "issues": [
                {
                    "code": i.code,
                    "severity": i.severity.value,
                    "message": i.message,
                    "clip_id": i.clip_id,
                }
                for i in self.issues
            ],
        }


class ValidationError(RuntimeError):
    def __init__(self, result: ValidationResult):
        self.result = result
        super().__init__(
            f"EditSpec 校验失败（{len(result.errors)} 个错误）:\n"
            + "\n".join(f"  {i}" for i in result.errors)
        )


# ─────────────────────────── 结构 ───────────────────────────

def check_structure(spec: EditSpec) -> list[Issue]:
    issues: list[Issue] = []

    if not spec.clips:
        issues.append(Issue("EMPTY_SPEC", Severity.ERROR, "EditSpec 不含任何 clip"))
        return issues

    # clip.id 必须唯一 —— diff 靠它定位
    dupes = [cid for cid, n in Counter(c.id for c in spec.clips).items() if n > 1]
    for cid in dupes:
        issues.append(
            Issue("DUPLICATE_CLIP_ID", Severity.ERROR, f"clip id 重复: {cid}", cid)
        )

    known_tracks = {t.id for t in spec.tracks}
    track_kinds = {t.id: t.kind for t in spec.tracks}
    phrase_by_clip = {
        beat.clip_id: phrase
        for phrase in spec.motion_phrases
        for beat in phrase.beats
    }
    ordered_clips = sorted(spec.clips, key=lambda item: item.timeline.in_sec)
    for index, clip in enumerate(ordered_clips):
        if clip.timeline.track not in known_tracks:
            issues.append(
                Issue(
                    "UNKNOWN_TRACK",
                    Severity.ERROR,
                    f"引用了未声明的轨道 {clip.timeline.track!r}；已声明: {sorted(known_tracks)}",
                    clip.id,
                )
            )
        elif track_kinds[clip.timeline.track] != "video":
            issues.append(
                Issue(
                    "TRACK_KIND_MISMATCH",
                    Severity.ERROR,
                    f"视频 clip 被放到 {track_kinds[clip.timeline.track]} 轨 "
                    f"{clip.timeline.track!r}",
                    clip.id,
                )
            )
        for cue in clip.audio.sfx:
            if cue.at_sec > clip.timeline.duration_sec:
                issues.append(
                    Issue(
                        "SFX_OUTSIDE_CLIP",
                        Severity.ERROR,
                        f"SFX {cue.recipe!r} 位于 {cue.at_sec:.3f}s，"
                        f"超过 clip 时长 {clip.timeline.duration_sec:.3f}s",
                        clip.id,
                    )
                )
        for point in clip.audio.volume_automation:
            if point.sec > clip.timeline.duration_sec:
                issues.append(
                    Issue(
                        "AUTOMATION_OUTSIDE_CLIP",
                        Severity.ERROR,
                        f"音量点 {point.sec:.3f}s 超过 clip 时长 "
                        f"{clip.timeline.duration_sec:.3f}s",
                        clip.id,
                    )
                )
        if clip.source_selection is not None and not (
            clip.source.in_sec - 1e-6
            <= clip.source_selection.anchor_sec
            <= clip.source.out_sec + 1e-6
        ):
            issues.append(
                Issue(
                    "SOURCE_ANCHOR_OUTSIDE_RANGE",
                    Severity.ERROR,
                    f"source_selection.anchor_sec={clip.source_selection.anchor_sec:.3f} "
                    f"不在源区间 {clip.source.in_sec:.3f}–{clip.source.out_sec:.3f}",
                    clip.id,
                )
            )
        if (
            index == 0
            and clip.incoming_cut is not None
            and clip.incoming_cut.kind != "establish"
        ):
            issues.append(
                Issue(
                    "FIRST_CUT_RELATION",
                    Severity.ERROR,
                    "时间线首镜的 incoming_cut 必须是 establish",
                    clip.id,
                )
            )
        if (
            index > 0
            and clip.incoming_cut is not None
            and clip.incoming_cut.kind == "establish"
        ):
            issues.append(
                Issue(
                    "LATE_ESTABLISH_RELATION",
                    Severity.ERROR,
                    "非首镜不能把 incoming_cut 标为 establish",
                    clip.id,
                )
            )
        fusion_operations = (
            len(clip.effects)
            + sum(
                end.recipe not in {"hard_cut", "none"}
                for end in (clip.transition.in_, clip.transition.out)
            )
            + int(clip.id in phrase_by_clip)
            + int(clip.retime.type == "speed_ramp" and clip.id not in phrase_by_clip)
        )
        if fusion_operations > 1:
            issues.append(
                Issue(
                    "FUSION_STACK_UNSUPPORTED",
                    Severity.ERROR,
                    "同一 clip 请求了多个 Fusion 效果/变速/转场；Resolve 将 comp "
                    "视为版本而非串联效果，请由 Recipe Planner 分配到不同镜头",
                    clip.id,
                )
            )
    clip_ids = {clip.id for clip in spec.clips}
    for phrase in spec.motion_phrases:
        ids = [beat.clip_id for beat in phrase.beats]
        missing = [clip_id for clip_id in ids if clip_id not in clip_ids]
        if missing:
            issues.append(
                Issue(
                    "MOTION_PHRASE_CLIP_NOT_FOUND",
                    Severity.ERROR,
                    f"MotionPhrase {phrase.id!r} 引用了不存在的 clips: {missing}",
                )
            )
            continue
        ordered = sorted(
            (spec.clip_by_id(clip_id) for clip_id in ids),
            key=lambda clip: clip.timeline.in_sec,
        )
        if [clip.id for clip in ordered] != ids:
            issues.append(
                Issue(
                    "MOTION_PHRASE_ORDER",
                    Severity.ERROR,
                    f"MotionPhrase {phrase.id!r} beats 必须按时间线顺序排列",
                )
            )
        for left, right in zip(ordered, ordered[1:]):
            if (
                left.timeline.track != right.timeline.track
                or abs(left.timeline.out_sec - right.timeline.in_sec) > 1 / 24
            ):
                issues.append(
                    Issue(
                        "MOTION_PHRASE_NOT_CONTIGUOUS",
                        Severity.ERROR,
                        f"MotionPhrase {phrase.id!r} 的 {left.id} → {right.id} "
                        "必须同轨且相邻",
                        right.id,
                    )
                )
    for layer in spec.audio:
        if layer.track not in known_tracks:
            issues.append(
                Issue(
                    "UNKNOWN_TRACK",
                    Severity.ERROR,
                    f"音频层 {layer.id} 引用了未声明的轨道 {layer.track!r}",
                )
            )
        elif track_kinds[layer.track] != "audio":
            issues.append(
                Issue(
                    "TRACK_KIND_MISMATCH",
                    Severity.ERROR,
                    f"音频层 {layer.id} 被放到非 audio 轨 {layer.track!r}",
                )
            )

    for caption in spec.captions:
        if caption.track not in known_tracks:
            issues.append(
                Issue(
                    "UNKNOWN_TRACK",
                    Severity.ERROR,
                    f"字幕 {caption.id} 引用了未声明的轨道 {caption.track!r}",
                )
            )
        elif track_kinds[caption.track] != "subtitle":
            issues.append(
                Issue(
                    "TRACK_KIND_MISMATCH",
                    Severity.ERROR,
                    f"字幕 {caption.id} 被放到非 subtitle 轨 {caption.track!r}",
                )
            )

    clip_ids = {clip.id for clip in spec.clips}
    for marker in spec.markers:
        if marker.clip_id and marker.clip_id not in clip_ids:
            issues.append(
                Issue(
                    "MARKER_CLIP_NOT_FOUND",
                    Severity.ERROR,
                    f"marker 引用了不存在的 clip {marker.clip_id!r}",
                )
            )
        if marker.sec > spec.duration_sec:
            issues.append(
                Issue(
                    "MARKER_OUTSIDE_TIMELINE",
                    Severity.ERROR,
                    f"marker {marker.sec:.3f}s 超过成片时长 {spec.duration_sec:.3f}s",
                )
            )

    issues.extend(_check_timeline_continuity(spec))
    return issues


def _check_timeline_continuity(spec: EditSpec) -> list[Issue]:
    """同轨不得重叠；空洞降级为警告（黑场有时是有意的）。"""
    issues: list[Issue] = []
    tb = CoreTimebase(spec.timebase.num, spec.timebase.den)
    eps = _EPS_FRAMES / tb.fps_float

    for track in spec.video_tracks():
        clips = spec.clips_on_track(track)
        for prev, cur in zip(clips, clips[1:]):
            gap = cur.timeline.in_sec - prev.timeline.out_sec
            if gap < -eps:
                issues.append(
                    Issue(
                        "TIMELINE_OVERLAP",
                        Severity.ERROR,
                        f"与 {prev.id} 在轨道 {track} 上重叠 {-gap:.4f}s"
                        f"（{prev.id} 结束于 {prev.timeline.out_sec:.4f}s，"
                        f"本片段始于 {cur.timeline.in_sec:.4f}s）",
                        cur.id,
                    )
                )
            elif gap > eps:
                issues.append(
                    Issue(
                        "TIMELINE_GAP",
                        Severity.WARNING,
                        f"与 {prev.id} 之间有 {gap:.4f}s 空隙（将呈现为黑场）",
                        cur.id,
                    )
                )
    return issues


# ─────────────────────────── 媒体 ───────────────────────────

def check_media(
    spec: EditSpec,
    resolve_asset: Callable[[str], Path | None] | None = None,
    resolve_shot: Callable[[str], object | None] | None = None,
) -> list[Issue]:
    """asset_id 必须能解析到实际存在的文件。

    resolve_asset 由调用方注入（Phase 1 用文件系统，Phase 2 起用 Asset DB），
    以免校验器直接依赖数据库。
    """
    issues: list[Issue] = []
    if resolve_asset is None:
        issues.append(
            Issue(
                "MEDIA_UNCHECKED",
                Severity.WARNING,
                "未提供 asset 解析器，跳过媒体可达性检查",
            )
        )
        return issues

    cache: dict[str, Path | None] = {}
    for clip in spec.clips:
        if clip.asset_id not in cache:
            try:
                cache[clip.asset_id] = resolve_asset(clip.asset_id)
            except Exception as exc:  # noqa: BLE001
                cache[clip.asset_id] = None
                issues.append(
                    Issue(
                        "ASSET_LOOKUP_FAILED",
                        Severity.ERROR,
                        f"解析 asset {clip.asset_id} 时出错: {exc}",
                        clip.id,
                    )
                )
        path = cache[clip.asset_id]
        if path is None:
            issues.append(
                Issue(
                    "ASSET_NOT_FOUND",
                    Severity.ERROR,
                    f"asset {clip.asset_id} 无法解析到媒体文件",
                    clip.id,
                )
            )
        elif not path.exists():
            issues.append(
                Issue(
                    "MEDIA_MISSING",
                    Severity.ERROR,
                    f"asset {clip.asset_id} 的文件不存在: {path}",
                    clip.id,
                )
            )
        if clip.shot_id and resolve_shot is not None:
            try:
                shot = resolve_shot(clip.shot_id)
            except Exception as exc:  # noqa: BLE001
                shot = None
                issues.append(
                    Issue(
                        "SHOT_LOOKUP_FAILED",
                        Severity.ERROR,
                        f"解析 shot {clip.shot_id} 时出错: {exc}",
                        clip.id,
                    )
                )
            if shot is None:
                issues.append(
                    Issue(
                        "SHOT_NOT_FOUND",
                        Severity.ERROR,
                        f"shot {clip.shot_id} 不存在",
                        clip.id,
                    )
                )
    for layer in spec.audio:
        if layer.path:
            path = Path(layer.path).expanduser()
            if not path.is_file():
                issues.append(
                    Issue(
                        "MEDIA_MISSING",
                        Severity.ERROR,
                        f"audio layer {layer.id} 的文件不存在: {path}",
                    )
                )
        elif layer.asset_id:
            try:
                path = resolve_asset(layer.asset_id)
            except Exception as exc:  # noqa: BLE001
                path = None
                issues.append(
                    Issue(
                        "ASSET_LOOKUP_FAILED",
                        Severity.ERROR,
                        f"解析 audio asset {layer.asset_id} 时出错: {exc}",
                    )
                )
            if path is None:
                issues.append(
                    Issue(
                        "ASSET_NOT_FOUND",
                        Severity.ERROR,
                        f"audio asset {layer.asset_id} 无法解析到媒体文件",
                    )
                )
            elif not path.exists():
                issues.append(
                    Issue(
                        "MEDIA_MISSING",
                        Severity.ERROR,
                        f"audio asset {layer.asset_id} 的文件不存在: {path}",
                    )
                )
    return issues


# ─────────────────────────── 能力 ───────────────────────────

# clip 上的字段 → 所需 capability。未 verified 即拦截（AGENTS.md R3）。
_FEATURE_CAPABILITY: dict[str, str] = {
    "smart_reframe": "portrait_reframe",
    "magic_mask_track": "subject_tracking",
    "speed_ramp": "timespeed_recipe",
    "effects": "add_fusion_comp",
    "color": "color_recipe",
    "transition": "transition",
    "sfx": "sound_recipe_prebake",
    "camera_move": "camera_move_recipe",
    "constant_retime": "timespeed_recipe",
    "audio_automation": "fairlight_automation",
    "captions": "fusion_title_generator",
    "retime_interpolation": "project_setting_retime_interpolation",
    "motion_phrase": "motion_phrase_compositor",
}


def check_capabilities(spec: EditSpec, is_verified: Callable[[str], bool]) -> list[Issue]:
    issues: list[Issue] = []

    def need(feature: str, clip_id: str, detail: str) -> None:
        cap = _FEATURE_CAPABILITY[feature]
        if not is_verified(cap):
            issues.append(
                Issue(
                    "CAPABILITY_NOT_VERIFIED",
                    Severity.ERROR,
                    f"{detail} 需要能力 {cap!r}，但它尚未 verified —— "
                    f"按 AGENTS.md R3 禁止生成该指令",
                    clip_id,
                )
            )

    for clip in spec.clips:
        if clip.framing.mode == "smart_reframe":
            need("smart_reframe", clip.id, "framing.mode=smart_reframe")
        if clip.framing.mode == "magic_mask_track":
            need("magic_mask_track", clip.id, "framing.mode=magic_mask_track")
        if clip.retime.type == "speed_ramp":
            need("speed_ramp", clip.id, "retime.type=speed_ramp")
        elif abs(clip.retime.speed - 1.0) > 1e-9:
            need("constant_retime", clip.id, f"retime.speed={clip.retime.speed}")
        if clip.retime.interpolation != "nearest":
            need(
                "retime_interpolation",
                clip.id,
                f"retime.interpolation={clip.retime.interpolation}",
            )
        if clip.camera.move != "none":
            need("camera_move", clip.id, f"camera.move={clip.camera.move}")
        if clip.effects:
            need("effects", clip.id, f"effects({len(clip.effects)} 个 recipe)")
        if clip.color is not None:
            need("color", clip.id, f"color recipe {clip.color.recipe!r}")
        if clip.audio.sfx:
            need("sfx", clip.id, f"audio.sfx({len(clip.audio.sfx)} 条)")
        if clip.audio.source_gain_db is not None or clip.audio.volume_automation:
            need("audio_automation", clip.id, "audio gain/automation")
        for end_name, end in (("in", clip.transition.in_), ("out", clip.transition.out)):
            if end.recipe not in ("hard_cut", "none"):
                need("transition", clip.id, f"transition.{end_name}={end.recipe!r}")
    if spec.captions:
        need("captions", "spec", f"captions({len(spec.captions)} 条)")
    if spec.motion_phrases:
        need(
            "motion_phrase",
            "spec",
            f"motion_phrases({len(spec.motion_phrases)} 条)",
        )

    return issues


# ─────────────────────────── Recipe ───────────────────────────

def check_recipes(spec: EditSpec, registry: RecipeRegistry) -> list[Issue]:
    issues: list[Issue] = []

    def check(recipe_id: str, params: dict, kind: str, clip_id: str | None) -> None:
        for problem in registry.validate(recipe_id, params, expected_kind=kind):
            issues.append(
                Issue(problem.code, Severity.ERROR, problem.message, clip_id)
            )

    for clip in spec.clips:
        for ref in clip.effects:
            check(ref.recipe, ref.params, "effect", clip.id)
        if clip.color:
            check(clip.color.recipe, clip.color.params, "color", clip.id)
        for cue in clip.audio.sfx:
            check(cue.recipe, {}, "sound", clip.id)
        for end in (clip.transition.in_, clip.transition.out):
            if end.recipe not in ("hard_cut", "none"):
                check(end.recipe, end.params, "transition", clip.id)
    for caption in spec.captions:
        if caption.style:
            check(caption.style.recipe, caption.style.params, "title", None)
    return issues


# ─────────────────────────── 入口 ───────────────────────────

def validate(
    spec: EditSpec,
    *,
    resolve_asset: Callable[[str], Path | None] | None = None,
    resolve_shot: Callable[[str], object | None] | None = None,
    is_verified: Callable[[str], bool] | None = None,
    recipe_registry: RecipeRegistry | None = None,
) -> ValidationResult:
    """完整校验。返回全部问题，不在首个错误处停止。"""
    if is_verified is None:
        from studio.core.capabilities import is_verified as default_is_verified

        is_verified = default_is_verified
    if recipe_registry is None:
        recipe_registry = RecipeRegistry.load()

    issues = check_structure(spec)
    issues += check_media(spec, resolve_asset, resolve_shot)
    issues += check_recipes(spec, recipe_registry)
    issues += check_capabilities(spec, is_verified)
    return ValidationResult(issues)
