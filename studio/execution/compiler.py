"""ResolveCompiler —— EditSpec → Resolve 时间线。

职责边界（AGENTS.md）：
    编译器只翻译，不做创意决策；
    所有 Resolve 访问经 ResolveAdapter；
    执行前强制过 validator（R2）。

增量更新的正确层次（实测修正）
------------------------------
最初的设计是「只删掉变化的片段，再插回原位」。**实测证明这条路走不通**：

    P10  ``AppendToTimeline`` 只能在时间线**末端**追加，
         无法填补轨道中间被删出的空洞 —— 该位置会静默返回 None。

于是把增量下沉一层。关键认识是：

    在 Resolve 里**搭时间线是廉价的，渲染才是昂贵的**。

因此：
    build()   总是全量重建时间线（清空 + 按序放置）。简单、幂等、无空洞问题。
    update()  同样全量重建时间线，但计算出**哪些时间区间发生了变化**，
              交给渲染层只渲这些区间。

这才是 MASTER PLAN §54「优先使用 Diff」的真正落点 ——
要避免的是「整份重新渲染」，不是「整份重新排时间线」。

clip.id ↔ Resolve 片段的映射（写进 marker customData，TARGET A15）依然保留：
它让 Critic 与用户反馈能按 clip_id 定位到时间线位置。
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from studio.core.hashing import stable_hash
from studio.core.timecode import Timebase as CoreTimebase
from studio.editspec.schema import Clip, EditSpec
from studio.editspec.validator import validate
from studio.execution.ffmpeg import prebake_audio, probe_media_json
from studio.execution.recipes import RecipeRegistry
from studio.execution.resolve import (
    ResolveAdapter,
    ResolveOperationError,
    append_prebaked_audio,
    apply_color_recipe,
    apply_fusion_recipe,
    apply_speed_ramp_recipe,
    apply_whip_blur_side,
)

log = logging.getLogger(__name__)

STATE_FILENAME = ".resolve_build_state.json"
REPO = Path(__file__).resolve().parents[2]

# EditSpec 枚举 → Resolve project.SetSetting('imageRetimeInterpolation', ...)
# 实测的精确字符串（大小写敏感，见 config/resolve_capabilities.yaml
# -> project_setting_retime_interpolation）。
_RETIME_INTERPOLATION_MAP = {
    "nearest": "nearest",
    "frame_blend": "frameBlend",
    "optical_flow": "opticalFlow",
}


def _audio_duration(path: Path) -> float:
    probe = probe_media_json(path)
    duration = float((probe.get("format") or {}).get("duration") or 0)
    if duration <= 0:
        raise ResolveOperationError(f"无法取得音频时长: {path}")
    return duration


@dataclass
class BuildReport:
    project: str
    timeline: str
    mode: str
    clips_total: int = 0
    clips_written: int = 0
    clips_unchanged: int = 0
    clips_changed: int = 0
    clips_added: int = 0
    clips_removed: int = 0
    markers_written: int = 0
    recipes_applied: int = 0
    motion_phrases_applied: int = 0
    audio_written: int = 0
    #: 发生变化的时间线区间（秒）。渲染层据此只渲这些段，而非整条片子。
    changed_ranges: list[tuple[float, float]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def changed_duration_sec(self) -> float:
        return sum(end - start for start, end in self.changed_ranges)

    def to_dict(self) -> dict:
        return {
            "project": self.project,
            "timeline": self.timeline,
            "mode": self.mode,
            "clips_total": self.clips_total,
            "clips_written": self.clips_written,
            "clips_unchanged": self.clips_unchanged,
            "clips_changed": self.clips_changed,
            "clips_added": self.clips_added,
            "clips_removed": self.clips_removed,
            "markers_written": self.markers_written,
            "recipes_applied": self.recipes_applied,
            "motion_phrases_applied": self.motion_phrases_applied,
            "audio_written": self.audio_written,
            "changed_ranges": [[round(a, 3), round(b, 3)] for a, b in self.changed_ranges],
            "changed_duration_sec": round(self.changed_duration_sec, 3),
            "warnings": self.warnings,
        }


def merge_ranges(ranges: list[tuple[float, float]], *, gap_tolerance: float = 0.0) -> list[tuple[float, float]]:
    """合并重叠或相邻的时间区间，避免渲染层重复渲同一段。"""
    if not ranges:
        return []
    ordered = sorted(ranges)
    merged = [ordered[0]]
    for start, end in ordered[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end + gap_tolerance:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


def clip_fingerprint(clip: Clip) -> str:
    """决定「这个 clip 是否需要重建」的指纹。

    只包含**影响 Resolve 时间线布局**的字段。
    decision.reasoning 之类的元数据变化不应触发重建 —— 这是增量更新的关键，
    否则 AI 每次改一句解释都会导致全量重渲。
    """
    payload = {
        "asset": clip.asset_id,
        "src_in": round(clip.source.in_sec, 6),
        "src_out": round(clip.source.out_sec, 6),
        "tl_in": round(clip.timeline.in_sec, 6),
        "tl_dur": round(clip.timeline.duration_sec, 6),
        "track": clip.timeline.track,
        "speed": round(clip.retime.speed, 6),
        "retime": clip.retime.type,
        "framing": clip.framing.model_dump(),
        "camera": clip.camera.model_dump(),
        "transition": clip.transition.model_dump(by_alias=True),
        "effects": [item.model_dump() for item in clip.effects],
        "color": clip.color.model_dump() if clip.color else None,
        "audio": clip.audio.model_dump(),
    }
    import hashlib

    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()[:16]


def spec_execution_fingerprint(spec: EditSpec) -> str:
    """Fingerprint non-clip fields that alter Resolve output."""
    return stable_hash(
        {
            "timebase": spec.timebase.model_dump(),
            "canvas": spec.canvas.model_dump(),
            "tracks": [track.model_dump() for track in spec.tracks],
            "audio": [layer.model_dump() for layer in spec.audio],
            "markers": [marker.model_dump() for marker in spec.markers],
            "captions": [caption.model_dump() for caption in spec.captions],
            "motion_phrases": [
                phrase.model_dump() for phrase in spec.motion_phrases
            ],
        }
    )[:16]


class ResolveCompiler:
    """把 EditSpec 编译进 Resolve。

    :param resolve_asset: asset_id → 媒体文件路径。Phase 1 由调用方注入；
                          Phase 2 起改由 Asset DB 提供。
    """

    def __init__(
        self,
        adapter: ResolveAdapter,
        resolve_asset: Callable[[str], Path | None],
        *,
        resolve_shot: Callable[[str], object | None] | None = None,
        recipe_registry: RecipeRegistry | None = None,
        state_dir: Path | None = None,
    ):
        self.rv = adapter
        self.resolve_asset = resolve_asset
        self.resolve_shot = resolve_shot
        self.recipe_registry = recipe_registry or RecipeRegistry.load()
        self.state_dir = state_dir.resolve() if state_dir is not None else None

    # ---------- 公共入口 ----------

    def build(
        self,
        spec: EditSpec,
        *,
        timeline_name: str = "main",
        reset_project: bool = False,
    ) -> BuildReport:
        """全量构建时间线。幂等 —— 同一 spec 跑两次结果完全一致。"""
        self._validate(spec)
        report = BuildReport(project=spec.id, timeline=timeline_name, mode="build")
        report.clips_total = len(spec.clips)
        report.clips_changed = len(spec.clips)
        report.changed_ranges = merge_ranges(
            [(c.timeline.in_sec, c.timeline.out_sec) for c in spec.clips]
        )

        self._rebuild_timeline(spec, timeline_name, report, reset_project=reset_project)
        self._save_state(spec)
        log.info("build 完成: %s", report.to_dict())
        return report

    def update(self, spec: EditSpec, *, timeline_name: str = "main") -> BuildReport:
        """基于 diff 的更新。

        时间线仍然全量重建（P10：Resolve 无法填补轨道空洞），
        但报告中给出 ``changed_ranges`` —— 渲染层只需渲这些区间。

        无历史状态时等价于 build。
        """
        self._validate(spec)
        previous = self._load_state(spec)
        if previous is None:
            log.info("无历史构建状态，按全量处理")
            return self.build(spec, timeline_name=timeline_name)

        report = BuildReport(project=spec.id, timeline=timeline_name, mode="update")
        report.clips_total = len(spec.clips)

        current = {c.id: clip_fingerprint(c) for c in spec.clips}
        spec_changed = (
            previous.get("__spec__") != spec_execution_fingerprint(spec)
        )
        changed = [c for c in spec.clips if previous.get(c.id) != current[c.id]]
        if spec_changed:
            changed = list(spec.clips)
        added = [c for c in changed if c.id not in previous]

        report.clips_changed = len(changed)
        report.clips_added = len(added)
        report.clips_unchanged = len(spec.clips) - len(changed)
        report.clips_removed = sum(
            1 for cid in previous if cid != "__spec__" and cid not in current
        )
        report.changed_ranges = merge_ranges(
            [(c.timeline.in_sec, c.timeline.out_sec) for c in changed]
        )

        if not changed and report.clips_removed == 0:
            log.info("spec 未变化，时间线无需重建")
            self._prepare_project(spec, timeline_name, reset_timeline=False)
            return report

        self._rebuild_timeline(spec, timeline_name, report)
        self._save_state(spec)
        log.info("update 完成: %s", report.to_dict())
        return report

    # ---------- 构建实现 ----------

    def _rebuild_timeline(
        self,
        spec: EditSpec,
        timeline_name: str,
        report: BuildReport,
        *,
        reset_project: bool = False,
    ) -> None:
        """清空并按 spec 重排时间线。

        必须按时间线位置升序放置：``AppendToTimeline`` 只能向末端延伸（P10），
        乱序放置会导致靠后的片段被静默丢弃。
        """
        self._prepare_project(
            spec, timeline_name, reset_project=reset_project, reset_timeline=True
        )
        media = self._import_media(spec)

        ordered = sorted(spec.clips, key=lambda c: (c.timeline.track, c.timeline.in_sec))
        written = self._append_clips(spec, ordered, media)

        report.clips_written = len(written)
        self._apply_clip_properties(spec, written, media, report)
        self._apply_recipes(spec, written, report)
        report.audio_written = self._append_audio(spec, written)
        report.markers_written = self._write_markers(spec, written)

    # ---------- 内部 ----------

    def _validate(self, spec: EditSpec) -> None:
        """R2：执行前必须校验通过。"""
        result = validate(
            spec,
            resolve_asset=self.resolve_asset,
            resolve_shot=self.resolve_shot,
            recipe_registry=self.recipe_registry,
        )
        for warning in result.warnings:
            log.warning("%s", warning)
        result.raise_if_failed()

    def _timebase(self, spec: EditSpec) -> CoreTimebase:
        return CoreTimebase(spec.timebase.num, spec.timebase.den, drop_frame=spec.timebase.drop_frame)

    def _prepare_project(
        self,
        spec: EditSpec,
        timeline_name: str,
        *,
        reset_project: bool = False,
        reset_timeline: bool = False,
    ) -> None:
        self.rv.ensure_project(
            spec.id,
            timebase=self._timebase(spec),
            width=spec.canvas.width,
            height=spec.canvas.height,
            reset=reset_project,
        )
        self.rv.ensure_timeline(timeline_name, reset=reset_timeline)
        video_tracks = len(spec.video_tracks())
        if video_tracks > 1:
            self.rv.ensure_video_tracks(video_tracks)
        self._apply_retime_interpolation(spec)

    def _apply_retime_interpolation(self, spec: EditSpec) -> None:
        """`imageRetimeInterpolation` 是工程级设置，不是逐 clip 的开关 ——
        一个工程里所有非 nearest 的 clip 必须要求同一档插值，否则无法用
        当前唯一验证过的 API 表达（见 ResolveAdapter.set_retime_interpolation
        docstring）。混用会在这里显式拒绝，而不是静默套用其中一个值。
        """
        requested = {c.retime.interpolation for c in spec.clips} - {"nearest"}
        if not requested:
            # No explicit speed change, but a frame-rate conform (source fps !=
            # timeline fps, e.g. 23.976 anime → 30fps delivery) still resamples
            # every clip.  Default that conform to optical flow so limited-anime
            # frames are interpolated instead of repeated (nearest) — repeated
            # frames are exactly what made the 30fps delivery judder.  With no
            # conform (source == timeline fps) this triggers no interpolation.
            self.rv.set_retime_interpolation("opticalFlow")
            return
        if len(requested) > 1:
            raise ValueError(
                f"retime.interpolation 在同一 EditSpec 里出现了多种非 nearest 取值 "
                f"{sorted(requested)}：Resolve 的插值方式是工程级设置，无法为不同 "
                f"clip 分别生效，请统一成一种"
            )
        self.rv.set_retime_interpolation(_RETIME_INTERPOLATION_MAP[requested.pop()])

    def _import_media(self, spec: EditSpec) -> dict[str, object]:
        """导入 spec 用到的全部素材，返回 asset_id → MediaInfo。"""
        asset_ids = {c.asset_id for c in spec.clips}
        paths: dict[str, Path] = {}
        for asset_id in sorted(asset_ids):
            path = self.resolve_asset(asset_id)
            if path is None:
                raise ResolveOperationError(f"无法解析 asset {asset_id}")
            paths[asset_id] = path

        infos = self.rv.import_media(list(paths.values()), bin_name="source")
        return {asset_id: infos[str(path)] for asset_id, path in paths.items()}

    def _track_index(self, spec: EditSpec, track_id: str) -> int:
        """V1 → 1, V2 → 2。Resolve 的轨道是 1-based 序号，不是名字。"""
        video = spec.video_tracks()
        try:
            return video.index(track_id) + 1
        except ValueError as exc:
            raise ResolveOperationError(
                f"轨道 {track_id!r} 不在 spec 声明的视频轨中: {video}"
            ) from exc

    def _append_clips(self, spec: EditSpec, clips: list[Clip], media: dict) -> list[tuple[Clip, object]]:
        if not clips:
            return []
        tl_fps = self._timebase(spec)

        requests = []
        for clip in clips:
            info = media[clip.asset_id]
            requests.append(
                {
                    "media_path": info.path,
                    "source_in_sec": clip.source.in_sec,
                    "source_out_sec": clip.source.out_sec,
                    "timeline_in_sec": clip.timeline.in_sec,
                    "timeline_duration_sec": clip.timeline.duration_sec,
                    "track_index": self._track_index(spec, clip.timeline.track),
                    "media_fps": info.fps,        # 源时基（如 23.976）
                    "timeline_fps": tl_fps,       # 交付时基
                    "media_type": 1,              # video only；音频必须由 EditSpec 明示
                }
            )

        items = self.rv.append_clips(requests)
        return list(zip(clips, items))

    def _write_markers(self, spec: EditSpec, written: list[tuple[Clip, object]]) -> int:
        """给每个片段打上 clip_id 标记，供后续增量更新定位。"""
        count = 0
        for clip, item in written:
            note = clip.decision.reasoning or clip.role or ""
            if self.rv.mark_clip(item, clip.id, note=note):
                count += 1

        # spec 级标记（drop / impact 等音乐结构点）
        tl_fps = self._timebase(spec)
        for marker in spec.markers:
            self.rv.add_timeline_marker(
                tl_fps.to_frames(marker.sec),
                marker.kind,
                marker.note,
                duration_frames=max(1, tl_fps.to_frames(marker.duration_sec)),
            )
        return count

    def _apply_clip_properties(
        self,
        spec: EditSpec,
        written: list[tuple[Clip, object]],
        media: dict,
        report: BuildReport,
    ) -> None:
        """Apply only static transform semantics proven by capability ``transform``."""
        for clip, item in written:
            info = media[clip.asset_id]
            source_aspect = info.width / max(info.height, 1)
            target_aspect = spec.canvas.width / spec.canvas.height
            cover_zoom = (
                source_aspect / target_aspect
                if source_aspect >= target_aspect
                else target_aspect / source_aspect
            )
            if clip.framing.mode == "fit":
                zoom = clip.framing.scale
            else:
                zoom = cover_zoom * clip.framing.scale
            properties: dict[str, float | int | str] = {
                "ZoomX": zoom,
                "ZoomY": zoom,
                "ZoomGang": True,
                "Pan": clip.framing.offset_x * spec.canvas.width,
                "Tilt": clip.framing.offset_y * spec.canvas.height,
            }
            results = self.rv.set_properties(item, properties)
            failed = sorted(key for key, value in results.items() if not value)
            if failed:
                report.warnings.append(
                    f"{clip.id}: transform 属性未生效: {', '.join(failed)}"
                )

    def _apply_recipes(
        self,
        spec: EditSpec,
        written: list[tuple[Clip, object]],
        report: BuildReport,
    ) -> None:
        """Apply only recipes already admitted by validator/R4."""
        self.rv.clear_color_groups()
        color_groups: dict[str, list[object]] = {}
        timeline_fps = self._timebase(spec)
        phrase_beats = {
            beat.clip_id: (phrase, beat)
            for phrase in spec.motion_phrases
            for beat in phrase.beats
        }
        eye_glow_clips = {
            marker.clip_id
            for marker in spec.markers
            if marker.kind == "eye_glow_cue" and marker.clip_id
        }
        demo_clips = {
            marker.clip_id: marker.note
            for marker in spec.markers
            if marker.kind == "demo_replica_clip" and marker.clip_id
        }
        demo_terminal_impacts = {
            clip.id: timeline_fps.to_frames(marker.sec - clip.timeline.in_sec)
            for clip in spec.clips
            for marker in spec.markers
            if marker.kind == "demo_replica_impact"
            and clip.timeline.in_sec + 1e-6 < marker.sec <= clip.timeline.out_sec
        }
        for clip, item in written:
            if clip.id in demo_clips:
                category = demo_clips[clip.id].split(":", 1)[-1]
                self.rv.build_demo_replica_comp(
                    item,
                    comp_name=f"aes:demo-replica:{category}",
                    category=category,
                    duration_frames=int(item.GetDuration() or 0),
                    speed_ramp=clip.retime.type == "speed_ramp",
                    terminal_impact_frame=demo_terminal_impacts.get(clip.id),
                )
                report.motion_phrases_applied += 1
                if clip.color is not None:
                    color_groups.setdefault(clip.color.recipe, []).append(item)
                continue
            phrase_row = phrase_beats.get(clip.id)
            if phrase_row is not None:
                phrase, beat = phrase_row
                impact_sec = (
                    clip.retime.impact_at_sec
                    if clip.retime.impact_at_sec is not None
                    else clip.timeline.duration_sec / 2
                )
                self.rv.build_motion_phrase_comp(
                    item,
                    comp_name=f"aes:motion:{phrase.id}:{beat.stage}",
                    stage=beat.stage,
                    direction=beat.direction or phrase.direction,
                    zoom_direction=beat.zoom_direction or phrase.zoom_direction,
                    intensity=beat.intensity,
                    duration_sec=clip.timeline.duration_sec,
                    accent_at_sec=beat.accent_at_sec,
                    anticipation_sec=beat.anticipation_sec,
                    release_sec=beat.release_sec,
                    entry_intensity=beat.entry_intensity,
                    entry_velocity=beat.entry_velocity,
                    exit_velocity=beat.exit_velocity,
                    duration_frames=int(item.GetDuration() or 0),
                    transition_frames=max(
                        1, timeline_fps.to_frames(phrase.cut_window_sec)
                    ),
                    translation=(
                        beat.translation
                        if beat.translation is not None
                        else phrase.translation
                    ),
                    scale_delta=(
                        beat.scale_delta
                        if beat.scale_delta is not None
                        else phrase.scale_delta
                    ),
                    rotation_deg=phrase.rotation_deg,
                    blur_strength=phrase.blur_strength,
                    eye_glow=clip.id in eye_glow_clips,
                    retime=(
                        {
                            "entry_speed": clip.retime.entry_speed,
                            "impact_speed": clip.retime.impact_speed,
                            "exit_speed": clip.retime.exit_speed,
                            "impact_frame": timeline_fps.to_frames(impact_sec),
                        }
                        if clip.retime.type == "speed_ramp"
                        else None
                    ),
                )
                report.motion_phrases_applied += 1
                if clip.retime.type == "speed_ramp":
                    report.recipes_applied += 1
                if clip.color is not None:
                    color_groups.setdefault(clip.color.recipe, []).append(item)
                continue
            for ref in clip.effects:
                apply_fusion_recipe(
                    self.rv,
                    self.recipe_registry,
                    item=item,
                    ref=ref,
                )
                report.recipes_applied += 1
            if clip.retime.type == "speed_ramp":
                duration_frames = int(item.GetDuration() or 0)
                impact_frame = timeline_fps.to_frames(
                    clip.retime.impact_at_sec
                    if clip.retime.impact_at_sec is not None
                    else clip.timeline.duration_sec / 2
                )
                apply_speed_ramp_recipe(
                    self.rv,
                    self.recipe_registry,
                    item=item,
                    duration_frames=duration_frames,
                    entry_speed=clip.retime.entry_speed,
                    impact_speed=clip.retime.impact_speed,
                    exit_speed=clip.retime.exit_speed,
                    impact_frame=impact_frame,
                )
                report.recipes_applied += 1
            for side, end in (
                ("in", clip.transition.in_),
                ("out", clip.transition.out),
            ):
                if end.recipe in {"hard_cut", "none"}:
                    continue
                if end.recipe not in {
                    "motion_blur_transition_v1",
                    "motion_blur_transition_v2",
                }:
                    raise ResolveOperationError(
                        f"transition recipe 尚无执行器: {end.recipe}"
                    )
                apply_whip_blur_side(
                    self.rv,
                    self.recipe_registry,
                    item=item,
                    recipe_id=end.recipe,
                    side=side,
                    duration_frames=int(item.GetDuration() or 0),
                    transition_frames=max(
                        1, timeline_fps.to_frames(end.duration_sec)
                    ),
                    params=end.params,
                )
                report.recipes_applied += 1
            # Per-shot camera curve: only when this clip has no other Fusion op
            # (effects/speed-ramp/transition all occupy the single comp slot).
            has_transition = any(
                end.recipe not in {"hard_cut", "none"}
                for end in (clip.transition.in_, clip.transition.out)
            )
            if (
                clip.camera.move != "none"
                and not clip.effects
                and clip.retime.type != "speed_ramp"
                and not has_transition
            ):
                zoom_span = clip.camera.to_scale - clip.camera.from_scale
                if clip.camera.move in {"push_in", "push_out"}:
                    direction = "in" if clip.camera.move == "push_in" else "out"
                    magnitude = abs(zoom_span) or 0.1
                else:
                    direction = clip.camera.move.removeprefix("pan_")
                    magnitude = abs(zoom_span) or 0.12
                self.rv.build_camera_curve_comp(
                    item,
                    comp_name=f"aes:camera:{clip.id}",
                    direction=direction,
                    magnitude=magnitude,
                    curve=clip.camera.curve,
                    duration_frames=int(item.GetDuration() or 0),
                )
                report.recipes_applied += 1
            if clip.color is not None:
                color_groups.setdefault(clip.color.recipe, []).append(item)
        for recipe_id, items in color_groups.items():
            apply_color_recipe(
                self.rv,
                self.recipe_registry,
                recipe_id=recipe_id,
                items=items,
            )
            report.recipes_applied += 1

    def _append_audio(
        self,
        spec: EditSpec,
        written: list[tuple[Clip, object]],
    ) -> int:
        """Place music and pre-baked SFX; Fairlight automation is never assumed."""
        audio_tracks = [track.id for track in spec.tracks if track.kind == "audio"]
        cue_rows: list[dict] = []
        for layer in spec.audio:
            source_path = (
                Path(layer.path).expanduser().resolve()
                if layer.path
                else self.resolve_asset(layer.asset_id or "")
            )
            if source_path is None:
                raise ResolveOperationError(f"无法解析 audio layer {layer.id}")
            duration = layer.duration_sec
            if duration is None:
                raise ResolveOperationError(
                    f"audio layer {layer.id} 必须给 duration_sec 才能确定性放置"
                )
            path = self._prebaked_audio(
                source_path,
                source_in_sec=layer.source_in_sec,
                duration_sec=duration,
                gain_db=layer.gain_db,
            )
            cue_rows.append(
                {
                    "timeline_in": layer.timeline_in_sec,
                    "path": path,
                    "duration": duration,
                    "track_index": audio_tracks.index(layer.track) + 1,
                }
            )
        sfx_track = len(audio_tracks) + 1
        sfx_rows: list[dict] = []
        for clip, _item in written:
            for cue in clip.audio.sfx:
                recipe = self.recipe_registry.get(cue.recipe)
                if recipe is None:
                    raise ResolveOperationError(f"Sound Recipe 未注册: {cue.recipe}")
                path = self.recipe_registry.artifact_path(cue.recipe)
                duration = _audio_duration(path)
                recipe_gain = float(
                    self.recipe_registry.resolved_params(cue.recipe, {}).get(
                        "gain_db", 0.0
                    )
                )
                path = self._prebaked_audio(
                    path,
                    source_in_sec=0.0,
                    duration_sec=duration,
                    gain_db=recipe_gain + cue.gain_db,
                )
                sfx_rows.append(
                    {
                        "timeline_in": clip.timeline.in_sec + cue.at_sec,
                        "path": path,
                        "duration": duration,
                    }
                )
        # Interval partitioning: overlapping SFX get separate tracks, non-overlapping
        # events reuse the lowest lane. This prevents silent AppendToTimeline failures.
        lane_ends: list[float] = []
        for row in sorted(sfx_rows, key=lambda value: value["timeline_in"]):
            start = row["timeline_in"]
            lane = next(
                (index for index, end in enumerate(lane_ends) if end <= start + 1e-9),
                len(lane_ends),
            )
            if lane == len(lane_ends):
                lane_ends.append(0.0)
            lane_ends[lane] = start + row["duration"]
            row["track_index"] = sfx_track + lane
            cue_rows.append(row)
        if not cue_rows:
            return 0
        self.rv.ensure_audio_tracks(max(row["track_index"] for row in cue_rows))
        paths = sorted({row["path"] for row in cue_rows})
        self.rv.import_media(paths, bin_name="aes-audio")
        tl_fps = self._timebase(spec)
        requests = [
            {
                "media_path": row["path"],
                "source_in_sec": 0.0,
                "source_out_sec": row["duration"],
                "timeline_in_sec": row["timeline_in"],
                "track_index": row["track_index"],
                "media_fps": tl_fps,
                "timeline_fps": tl_fps,
            }
            for row in sorted(
                cue_rows, key=lambda value: (value["track_index"], value["timeline_in"])
            )
        ]
        return len(append_prebaked_audio(self.rv, requests))

    def _prebaked_audio(
        self,
        source: Path,
        *,
        source_in_sec: float,
        duration_sec: float,
        gain_db: float,
    ) -> Path:
        cache_root = (
            self.state_dir / "audio-cache"
            if self.state_dir is not None
            else REPO / "library" / "cache" / "execution-audio"
        )
        key = stable_hash(
            {
                "source": str(source.resolve()),
                "mtime_ns": source.stat().st_mtime_ns,
                "source_in_sec": source_in_sec,
                "duration_sec": duration_sec,
                "gain_db": gain_db,
                "pipeline": "audio-prebake-1",
            }
        )[:24]
        return prebake_audio(
            source,
            cache_root / f"{key}.wav",
            source_in_sec=source_in_sec,
            duration_sec=duration_sec,
            gain_db=gain_db,
        )

    # ---------- 构建状态（增量更新的依据） ----------

    def _state_path(self, spec: EditSpec) -> Path | None:
        if self.state_dir is None:
            return None
        return self.state_dir / STATE_FILENAME

    def _save_state(self, spec: EditSpec) -> None:
        path = self._state_path(spec)
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "spec_id": spec.id,
                    "revision": spec.revision,
                    "fingerprints": {
                        "__spec__": spec_execution_fingerprint(spec),
                        **{c.id: clip_fingerprint(c) for c in spec.clips},
                    },
                },
                ensure_ascii=False,
                indent=2,
            )
        )

    def _load_state(self, spec: EditSpec) -> dict[str, str] | None:
        path = self._state_path(spec)
        if path is None or not path.exists():
            return None
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError:
            log.warning("构建状态文件损坏，退化为全量 build")
            return None
        if data.get("spec_id") != spec.id:
            return None
        return data.get("fingerprints")


__all__ = [
    "ResolveCompiler",
    "BuildReport",
    "clip_fingerprint",
    "merge_ranges",
    "spec_execution_fingerprint",
]
