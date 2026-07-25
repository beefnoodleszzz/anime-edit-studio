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

from studio.core.timecode import Timebase as CoreTimebase
from studio.editspec.schema import Clip, EditSpec
from studio.editspec.validator import validate
from studio.execution.resolve import ResolveAdapter, ResolveOperationError

log = logging.getLogger(__name__)

STATE_FILENAME = ".resolve_build_state.json"


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
    }
    import hashlib

    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()[:16]


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
        state_dir: Path | None = None,
    ):
        self.rv = adapter
        self.resolve_asset = resolve_asset
        self.state_dir = state_dir

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
        changed = [c for c in spec.clips if previous.get(c.id) != current[c.id]]
        added = [c for c in changed if c.id not in previous]

        report.clips_changed = len(changed)
        report.clips_added = len(added)
        report.clips_unchanged = len(spec.clips) - len(changed)
        report.clips_removed = sum(1 for cid in previous if cid not in current)
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
        report.markers_written = self._write_markers(spec, written)

    # ---------- 内部 ----------

    def _validate(self, spec: EditSpec) -> None:
        """R2：执行前必须校验通过。"""
        result = validate(spec, resolve_asset=self.resolve_asset)
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
                    "track_index": self._track_index(spec, clip.timeline.track),
                    "media_fps": info.fps,        # 源时基（如 23.976）
                    "timeline_fps": tl_fps,       # 交付时基
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
                tl_fps.to_frames(marker.sec), marker.kind, marker.note
            )
        return count

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
                    "fingerprints": {c.id: clip_fingerprint(c) for c in spec.clips},
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


__all__ = ["ResolveCompiler", "BuildReport", "clip_fingerprint", "merge_ranges"]
