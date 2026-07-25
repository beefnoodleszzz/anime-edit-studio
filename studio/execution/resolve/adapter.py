"""ResolveAdapter —— 上层访问 Resolve 的唯一门面（AGENTS.md R1）。

对上：提供以**秒**为单位、以 asset_id 为引用的语义化操作。
对下：负责帧换算、对象查找、幂等、错误转译。

幂等性
------
所有 ensure_* 方法可重复调用：已存在则复用，不存在则创建。
Phase 1 的成功标准之一是「重跑幂等」，这里是实现点。

时基
----
源帧用素材自身 fps，时间线帧用交付 fps —— 两者绝不混用。
换算全部经 studio.core.timecode，本模块不自己算。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from studio.core.timecode import Timebase
from studio.execution.resolve import connection
from studio.execution.resolve.connection import ResolveUnavailable

log = logging.getLogger(__name__)

# marker customData 前缀，用于把 EditSpec clip_id 写进时间线（TARGET A15）
CLIP_MARKER_PREFIX = "aes:"


class ResolveOperationError(RuntimeError):
    """Resolve 返回了失败。消息中必须包含足够的定位信息。"""


@dataclass(frozen=True)
class MediaInfo:
    """素材的技术属性。fps 决定源帧换算，必须从 Resolve 实际读取而非假设。"""

    path: Path
    fps: Timebase
    width: int
    height: int
    duration_frames: int

    @property
    def duration_sec(self) -> float:
        return float(self.fps.to_seconds(self.duration_frames))


class ResolveAdapter:
    """Resolve 的语义化门面。

    用法::

        with ResolveAdapter.open() as rv:
            rv.ensure_project("my-project", timebase=..., width=..., height=...)
            rv.import_media([...])
            rv.ensure_timeline("main")
    """

    def __init__(self, resolve):
        self._resolve = resolve
        self._pm = resolve.GetProjectManager()
        self._project = None
        self._timeline = None
        self._media_cache: dict[str, object] = {}   # path -> MediaPoolItem

    # ---------- 生命周期 ----------

    @classmethod
    def open(cls, *, auto_launch: bool = False) -> "ResolveAdapter":
        return cls(connection.connect(auto_launch=auto_launch))

    def __enter__(self) -> "ResolveAdapter":
        return self

    def __exit__(self, *exc) -> None:
        # 不关闭工程：用户可能想在 Resolve 里查看结果
        return None

    @property
    def version(self) -> str:
        return self._resolve.GetVersionString()

    # ---------- 工程 ----------

    def ensure_project(
        self,
        name: str,
        *,
        timebase: Timebase,
        width: int,
        height: int,
        reset: bool = False,
    ):
        """幂等地打开或创建工程，并施加时间线设置。

        reset=True 时先删除同名工程 —— 用于测试与"从头重建"。
        """
        if reset:
            self._pm.CloseProject(self._pm.GetCurrentProject())
            self._pm.DeleteProject(name)

        project = self._pm.LoadProject(name) or self._pm.CreateProject(name)
        if project is None:
            raise ResolveOperationError(
                f"无法打开或创建工程 {name!r}。"
                f"现有工程: {self._pm.GetProjectListInCurrentFolder()}"
            )
        self._project = project

        # 工程级时间线设置必须在建时间线**之前**施加，否则新时间线会用旧设置
        self._set_settings(
            {
                "timelineFrameRate": self._fps_setting(timebase),
                "timelineResolutionWidth": str(width),
                "timelineResolutionHeight": str(height),
                "timelineOutputResolutionWidth": str(width),
                "timelineOutputResolutionHeight": str(height),
            }
        )
        log.info("project ready: %s (%dx%d @ %s)", name, width, height, timebase)
        return project

    @staticmethod
    def _fps_setting(tb: Timebase) -> str:
        """Resolve 的 timelineFrameRate 要字符串；NTSC 用名义值 + 单独的 DF 开关。"""
        if tb.is_ntsc:
            return f"{tb.nominal_fps:d}"
        return f"{tb.fps_float:g}"

    def _set_settings(self, settings: dict[str, str]) -> None:
        failed = {
            key: value
            for key, value in settings.items()
            if not self._project.SetSetting(key, value)
        }
        if failed:
            # 不致命：部分设置在某些工程状态下会被拒绝，记录以便排查
            log.warning("以下工程设置未生效: %s", failed)

    @property
    def project(self):
        if self._project is None:
            raise ResolveOperationError("尚未打开工程，请先调用 ensure_project()")
        return self._project

    # ---------- 媒体池 ----------

    def ensure_bin(self, name: str):
        """幂等地创建/获取 Bin。"""
        mp = self.project.GetMediaPool()
        root = mp.GetRootFolder()
        for folder in root.GetSubFolderList():
            if folder.GetName() == name:
                return folder
        folder = mp.AddSubFolder(root, name)
        if folder is None:
            raise ResolveOperationError(f"无法创建 Bin {name!r}")
        return folder

    def import_media(self, paths: list[Path], *, bin_name: str | None = None) -> dict[str, MediaInfo]:
        """导入媒体（已在池中则复用），返回 path -> MediaInfo。"""
        mp = self.project.GetMediaPool()
        if bin_name:
            mp.SetCurrentFolder(self.ensure_bin(bin_name))

        existing = self._index_media_pool()
        to_import = [str(p) for p in paths if str(p) not in existing]

        if to_import:
            imported = mp.ImportMedia(to_import)
            if not imported:
                raise ResolveOperationError(
                    f"ImportMedia 失败。路径: {to_import[:3]}"
                    f"{' …' if len(to_import) > 3 else ''}"
                )
            existing = self._index_media_pool()

        out: dict[str, MediaInfo] = {}
        for p in paths:
            key = str(p)
            item = existing.get(key)
            if item is None:
                raise ResolveOperationError(f"导入后仍找不到媒体: {key}")
            self._media_cache[key] = item
            out[key] = self._media_info(item, Path(key))
        return out

    def _index_media_pool(self) -> dict[str, object]:
        """递归索引媒体池，按文件路径建表。"""
        index: dict[str, object] = {}

        def walk(folder) -> None:
            for item in folder.GetClipList():
                path = item.GetClipProperty("File Path")
                if path:
                    index[path] = item
            for sub in folder.GetSubFolderList():
                walk(sub)

        walk(self.project.GetMediaPool().GetRootFolder())
        return index

    @staticmethod
    def _media_info(item, path: Path) -> MediaInfo:
        fps_raw = item.GetClipProperty("FPS")
        resolution = item.GetClipProperty("Resolution") or "0x0"
        width, _, height = resolution.partition("x")
        return MediaInfo(
            path=path,
            fps=Timebase.from_fps(float(fps_raw)),
            width=int(width or 0),
            height=int(height or 0),
            duration_frames=int(item.GetClipProperty("Frames") or 0),
        )

    def media_item(self, path: Path):
        key = str(path)
        if key not in self._media_cache:
            self._media_cache = self._index_media_pool()
        item = self._media_cache.get(key)
        if item is None:
            raise ResolveOperationError(f"媒体不在池中: {key}（请先 import_media）")
        return item

    # ---------- 时间线 ----------

    def ensure_timeline(self, name: str, *, reset: bool = False):
        """幂等地创建/获取时间线。reset=True 时删除重建。"""
        mp = self.project.GetMediaPool()
        existing = self._find_timeline(name)

        if existing and reset:
            mp.DeleteTimelines([existing])
            existing = None

        if existing:
            self.project.SetCurrentTimeline(existing)
            self._timeline = existing
            return existing

        timeline = mp.CreateEmptyTimeline(name)
        if timeline is None:
            raise ResolveOperationError(f"无法创建时间线 {name!r}")
        self._timeline = timeline
        return timeline

    def _find_timeline(self, name: str):
        for i in range(1, (self.project.GetTimelineCount() or 0) + 1):
            tl = self.project.GetTimelineByIndex(i)
            if tl and tl.GetName() == name:
                return tl
        return None

    @property
    def timeline(self):
        if self._timeline is None:
            raise ResolveOperationError("尚未创建时间线，请先调用 ensure_timeline()")
        return self._timeline

    def ensure_video_tracks(self, count: int) -> None:
        while (self.timeline.GetTrackCount("video") or 0) < count:
            if not self.timeline.AddTrack("video"):
                raise ResolveOperationError("无法新增视频轨")

    def ensure_audio_tracks(self, count: int) -> None:
        while (self.timeline.GetTrackCount("audio") or 0) < count:
            if not self.timeline.AddTrack("audio"):
                raise ResolveOperationError("无法新增音频轨")

    # ---------- 片段放置 ----------

    def append_clips(self, requests: list[dict]) -> list:
        """批量放置片段。

        requests 中每项::

            {
              "media_path": Path,        # 源文件
              "source_in_sec": float,    # 源入点（源时基）
              "source_out_sec": float,   # 源出点（源时基）
              "timeline_in_sec": float,  # 时间线落点（交付时基）
              "track_index": int,        # 1-based
              "media_fps": Timebase,     # 源时基
              "timeline_fps": Timebase,  # 交付时基
            }

        帧换算规则 —— 三个实测坑，每一个都会导致片段静默错位：

        P7  ``startFrame`` / ``endFrame`` 是**源帧**（媒体自身时基），
            而 ``recordFrame`` 是**时间线绝对帧**。
        P8  ``endFrame`` 是**开区间**。实测 startFrame=240, endFrame=288 → 48 帧。
            按闭区间处理会每个片段少一帧。
        P9  时间线起始帧默认是 **86400**（01:00:00:00），不是 0。
            recordFrame 必须加上 ``GetStartFrame()``，否则第二个及以后的片段
            会被静默丢弃（AppendToTimeline 返回 None）。

        P11 源帧数必须由**时长**算，不能对入点 floor、对出点 ceil。
            后者的不对称会让片段多出 1 帧：实测 29.97 素材取 2.0s，
            floor/ceil 得 61 源帧 → 套格到 24fps 时间线后变成 49 帧而非 48。
            Resolve 按时间套格，因此源时长必须精确等于目标时长。
        """
        timeline_origin = self.timeline.GetStartFrame() or 0   # P9

        payload = []
        for r in requests:
            src_fps: Timebase = r["media_fps"]
            tl_fps: Timebase = r["timeline_fps"]
            start_f = src_fps.to_frames(r["source_in_sec"])
            # P11：按时长取帧，保证套格后的时间线时长精确
            n_frames = src_fps.duration_frames(
                r["source_in_sec"], r["source_out_sec"] - r["source_in_sec"]
            )
            end_f = start_f + n_frames
            payload.append(
                {
                    "mediaPoolItem": self.media_item(Path(r["media_path"])),
                    "startFrame": start_f,
                    "endFrame": end_f,                          # P8：开区间，不减 1
                    "trackIndex": r.get("track_index", 1),
                    "recordFrame": timeline_origin + tl_fps.to_frames(r["timeline_in_sec"]),
                }
            )

        items = self.project.GetMediaPool().AppendToTimeline(payload)
        items = list(items or [])

        # AppendToTimeline 会对失败项返回 None 而不报错 —— 必须显式检查
        if len(items) != len(payload) or any(i is None for i in items):
            failed = [
                payload[idx]["recordFrame"]
                for idx, item in enumerate(items)
                if item is None
            ]
            raise ResolveOperationError(
                f"AppendToTimeline 期望放置 {len(payload)} 个片段，"
                f"实际成功 {sum(1 for i in items if i is not None)} 个"
                f"{f'；失败于 recordFrame {failed}' if failed else ''}。"
                f"（时间线起始帧={timeline_origin}）"
            )
        return items

    def timeline_items(self, track_index: int = 1) -> list:
        return self.timeline.GetItemListInTrack("video", track_index) or []

    def clear_video_track(self, track_index: int = 1) -> None:
        items = self.timeline_items(track_index)
        if items:
            self.timeline.DeleteClips(items)

    # ---------- 标记（IR ↔ Resolve 双向定位，TARGET A15） ----------

    def mark_clip(self, item, clip_id: str, *, color: str = "Blue", note: str = "") -> bool:
        """把 EditSpec clip_id 写进片段标记，供增量更新时定位。

        坑（实测 P6）：源素材自带的章节标记会被片段继承，
        且 Resolve **拒绝在已有标记的帧上再加标记**。
        蓝光/WEB 片源常在第 0 帧就有 "Chapter N"，因此不能盲目用 frame 0。
        这里向后找第一个空闲帧。
        """
        target = f"{CLIP_MARKER_PREFIX}{clip_id}"

        # 已经标过了（幂等重跑）
        existing = item.GetMarkers() or {}
        if any(m.get("customData") == target for m in existing.values()):
            return True

        occupied = set(existing.keys())
        duration = item.GetDuration() or 1
        for frame in range(0, max(1, duration)):
            if frame in occupied:
                continue
            if item.AddMarker(frame, color, clip_id, note or clip_id, 1, target):
                return True
        log.warning("clip %s: 片段内无可用帧写入标记（duration=%s）", clip_id, duration)
        return False

    def find_item_by_clip_id(self, clip_id: str, track_index: int = 1):
        """按 EditSpec clip_id 找回 Resolve 片段。

        只认带本系统前缀的 customData，源素材自带的章节标记会被忽略。
        """
        target = f"{CLIP_MARKER_PREFIX}{clip_id}"
        for item in self.timeline_items(track_index):
            for meta in (item.GetMarkers() or {}).values():
                if meta.get("customData") == target:
                    return item
        return None

    @staticmethod
    def source_in_seconds(item) -> float:
        """片段在源素材中的入点（秒）—— 与 EditSpec 同一语义。

        用 ``GetSourceStartTime()`` 而非帧号，因为：
        - ``GetSourceStartFrame()`` 含媒体起始时间码偏移
        - ``GetLeftOffset()`` 在媒体帧率≠时间线帧率时会被 Resolve 套格重映射
          （实测 29.97 素材放进 24fps 时间线，源帧 720 变成 leftOffset 576）

        ``GetSourceStartTime()`` 直接返回秒，跨帧率语义稳定。
        """
        return float(item.GetSourceStartTime())

    @staticmethod
    def source_out_seconds(item) -> float:
        return float(item.GetSourceEndTime())

    def add_timeline_marker(self, frame: int, kind: str, note: str, *, color: str = "Yellow") -> bool:
        return bool(self.timeline.AddMarker(frame, color, kind, note, 1))

    # ---------- 属性 ----------

    def set_properties(self, item, props: dict[str, float | int | str]) -> dict[str, bool]:
        return {key: bool(item.SetProperty(key, value)) for key, value in props.items()}


__all__ = ["ResolveAdapter", "ResolveOperationError", "ResolveUnavailable", "MediaInfo"]
