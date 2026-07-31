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
import math
import time
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


@dataclass(frozen=True)
class RenderResult:
    job_id: str
    output: Path
    status: dict


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

        # Right after Create/DeleteProject in quick succession, GetMediaPool()
        # can transiently return None for a couple hundred ms before the
        # project is fully live — poll briefly rather than fail spuriously.
        deadline = time.monotonic() + 15.0
        while project.GetMediaPool() is None and time.monotonic() < deadline:
            time.sleep(0.1)
        if project.GetMediaPool() is None:
            raise ResolveOperationError(f"工程 {name!r} 的 MediaPool 在 15s 内未就绪")

        # 工程级时间线设置必须在建时间线**之前**施加，否则新时间线会用旧设置
        self._set_settings(
            {
                "timelineFrameRate": self._fps_setting(timebase),
                "timelinePlaybackFrameRate": self._fps_setting(timebase),
                "timelineResolutionWidth": str(width),
                "timelineResolutionHeight": str(height),
                "timelineOutputResolutionWidth": str(width),
                "timelineOutputResolutionHeight": str(height),
            }
        )
        deadline = time.monotonic() + 3.0
        actual_fps = float(project.GetSetting("timelineFrameRate") or 0)
        while abs(actual_fps - timebase.fps_float) > 0.002 and time.monotonic() < deadline:
            time.sleep(0.15)
            actual_fps = float(project.GetSetting("timelineFrameRate") or 0)
        if abs(actual_fps - timebase.fps_float) > 0.002:
            raise ResolveOperationError(
                f"Resolve 时间线时基未生效: 请求 {timebase.fps_float:.6f}，"
                f"实际 {actual_fps:g}"
            )
        log.info("project ready: %s (%dx%d @ %s)", name, width, height, timebase)
        return project

    @staticmethod
    def _fps_setting(tb: Timebase) -> str:
        """Resolve accepts the displayed NTSC rate, never the nominal timecode fps."""
        ntsc = {
            (24000, 1001): "23.976",
            (30000, 1001): "29.97",
            (60000, 1001): "59.94",
        }
        if (tb.num, tb.den) in ntsc:
            return ntsc[(tb.num, tb.den)]
        return f"{tb.fps_float:g}"

    def _set_settings(self, settings: dict[str, str]) -> None:
        # Right after a fast Close/Delete/Create sequence, Resolve's project
        # context can still be mid-switch: SetSetting returns False for
        # *every* key even though GetMediaPool() already succeeded. That is
        # a transient race, not a real rejection, so retry briefly before
        # treating any remaining failures as genuine (e.g. a setting Resolve
        # actually refuses in this project state).
        remaining = dict(settings)
        deadline = time.monotonic() + 3.0
        while remaining and time.monotonic() < deadline:
            remaining = {
                key: value
                for key, value in remaining.items()
                if not self._project.SetSetting(key, value)
            }
            if remaining:
                time.sleep(0.15)
        if remaining:
            # 不致命：部分设置在某些工程状态下会被拒绝，记录以便排查
            log.warning("以下工程设置未生效: %s", remaining)

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
            # A freshly created/reset project's root folder returns None
            # (not []) from GetClipList()/GetSubFolderList() while empty.
            for item in folder.GetClipList() or []:
                path = item.GetClipProperty("File Path")
                if path:
                    index[path] = item
            for sub in folder.GetSubFolderList() or []:
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
              "timeline_duration_sec": float, # 目标时间线时长
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
            source_duration = r["source_out_sec"] - r["source_in_sec"]
            if r.get("timeline_duration_sec") is not None:
                # The placement boundary is authoritative.  Converting source
                # duration independently can produce a one-frame gap when its
                # rounding phase differs from recordFrame.  First derive the
                # exact target-frame count, then rebase that duration to the
                # source timebase.
                target_frames = tl_fps.duration_frames(
                    r["timeline_in_sec"], r["timeline_duration_sec"]
                )
                n_frames = src_fps.frames_for_rebased_duration(
                    target_frames,
                    tl_fps,
                )
            else:
                # P11：按时长取帧，保证套格后的时间线时长精确
                n_frames = src_fps.duration_frames(
                    r["source_in_sec"], source_duration
                )
            end_f = start_f + n_frames
            payload.append(
                {
                    "mediaPoolItem": self.media_item(Path(r["media_path"])),
                    "startFrame": start_f,
                    "endFrame": end_f,                          # P8：开区间，不减 1
                    "trackIndex": r.get("track_index", 1),
                    "recordFrame": timeline_origin + tl_fps.to_frames(r["timeline_in_sec"]),
                    **(
                        {"mediaType": int(r["media_type"])}
                        if r.get("media_type") is not None
                        else {}
                    ),
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

    def append_audio(self, requests: list[dict]) -> list:
        """Place pre-baked audio-only media using the same P8/P9/P11 rules."""
        return self.append_clips(
            [{**request, "media_type": 2} for request in requests]
        )

    def timeline_items(self, track_index: int = 1) -> list:
        return self.timeline.GetItemListInTrack("video", track_index) or []

    def timeline_frame_range(
        self,
        *,
        duration_sec: float,
        timebase: Timebase,
    ) -> tuple[int, int]:
        """Return Resolve's inclusive render range for an EditSpec duration."""
        start = int(self.timeline.GetStartFrame() or 0)
        frame_count = timebase.duration_frames(0.0, duration_sec)
        if frame_count <= 0:
            raise ValueError("渲染时长必须至少为一帧")
        return start, start + frame_count - 1

    def audio_items(self, track_index: int = 1) -> list:
        return self.timeline.GetItemListInTrack("audio", track_index) or []

    def clear_video_track(self, track_index: int = 1) -> None:
        items = self.timeline_items(track_index)
        if items:
            self.timeline.DeleteClips(items)

    # ---------- 变速插值（retime_interpolation，2026-07-28 实测） ----------

    _RETIME_INTERPOLATION_VALUES = {"nearest", "frameBlend", "opticalFlow"}
    _MOTION_ESTIMATION_VALUES = {
        "standardFaster", "standardBetter", "enhancedFaster", "enhancedBetter",
    }

    def set_retime_interpolation(
        self, interpolation: str, *, motion_estimation: str = "enhancedBetter"
    ) -> None:
        """设置素材 fps 与时间线 fps 不一致时的插帧方式。

        这是**工程级**设置（``project.SetSetting``），不是逐 clip 的开关 ——
        对本工程里所有需要 conform 的素材统一生效。per-clip
        ``TimelineItem.SetProperty('RetimeProcess'/'MotionEstimation', int)``
        实测走不通：只能触达 nearest/frame_blend 两种效果，摸不到真正的光流
        （见 config/resolve_capabilities.yaml -> retime_interpolation_mapping，
        证据 docs/probes/retime_mapping_probe.json）。

        真正生效的是这条工程级字符串设置（同一份证据文件确认，见
        project_setting_retime_interpolation），但有一个前提：**调用之前不能
        对目标 clip 用过 per-clip SetProperty('RetimeProcess'/'MotionEstimation', ...)**，
        否则那个残留的 clip 级值会盖过工程级设置，本方法读回校验也测不出来
        （返回值仍是 True，只是渲染时没生效）。正常通过 append_clips 放置、
        从未被逐 clip 调过速度/插值属性的素材不受影响。

        Args:
            interpolation: "nearest" | "frameBlend" | "opticalFlow"。
                没有 "speedWarp" —— 实测这个值在这个设置项里一律被拒，
                目前找不到任何脚本入口能激活 Speed Warp。
            motion_estimation: "standardFaster" | "standardBetter" |
                "enhancedFaster" | "enhancedBetter"。仅在
                interpolation="opticalFlow" 时影响画质/速度权衡。
        """
        if interpolation not in self._RETIME_INTERPOLATION_VALUES:
            raise ValueError(
                f"interpolation 必须是 {sorted(self._RETIME_INTERPOLATION_VALUES)} 之一，"
                f"收到 {interpolation!r}（注意没有 'speedWarp'，脚本层暂时无法激活）"
            )
        if motion_estimation not in self._MOTION_ESTIMATION_VALUES:
            raise ValueError(
                f"motion_estimation 必须是 {sorted(self._MOTION_ESTIMATION_VALUES)} 之一，"
                f"收到 {motion_estimation!r}"
            )
        if not self.project.SetSetting("imageRetimeInterpolation", interpolation):
            raise ResolveOperationError(
                f"project.SetSetting('imageRetimeInterpolation', {interpolation!r}) 被拒绝"
            )
        if not self.project.SetSetting("imageMotionEstimationMode", motion_estimation):
            raise ResolveOperationError(
                f"project.SetSetting('imageMotionEstimationMode', {motion_estimation!r}) 被拒绝"
            )

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

    def add_timeline_marker(
        self,
        frame: int,
        kind: str,
        note: str,
        *,
        duration_frames: int = 1,
        color: str = "Yellow",
    ) -> bool:
        return bool(
            self.timeline.AddMarker(
                frame, color, kind, note, max(1, duration_frames)
            )
        )

    # ---------- 属性 ----------

    def set_properties(self, item, props: dict[str, float | int | str]) -> dict[str, bool]:
        return {key: bool(item.SetProperty(key, value)) for key, value in props.items()}

    def replace_fusion_comp(
        self,
        item,
        artifact: Path,
        *,
        comp_name: str,
        parameters: dict[str, float | int | bool] | None = None,
    ):
        """Import a versioned Fusion comp and verify every injected input."""
        if not artifact.is_file():
            raise ResolveOperationError(f"Fusion Recipe 产物不存在: {artifact}")
        names = item.GetFusionCompNameList() or []
        if comp_name in names and not item.DeleteFusionCompByName(comp_name):
            raise ResolveOperationError(f"无法替换已有 Fusion comp: {comp_name}")
        comp = item.ImportFusionComp(str(artifact))
        if not comp:
            raise ResolveOperationError(f"ImportFusionComp 失败: {artifact}")
        tools = comp.GetToolList(False) or {}
        for address, value in (parameters or {}).items():
            tool_name, separator, input_name = address.partition(".")
            if not separator:
                raise ValueError(f"Fusion 参数地址必须是 Tool.Input: {address}")
            tool = tools.get(tool_name)
            if tool is None:
                tool = next(
                    (
                        candidate
                        for candidate in tools.values()
                        if (candidate.GetAttrs() or {}).get("TOOLS_Name") == tool_name
                    ),
                    None,
                )
            if tool is None:
                raise ResolveOperationError(f"Fusion Recipe 找不到工具: {tool_name}")
            tool.SetInput(input_name, value)
            if tool.GetInput(input_name) != value:
                raise ResolveOperationError(f"Fusion 参数回读不一致: {address}")
        return comp

    @staticmethod
    def _optional_fusion_tool(comp, name: str):
        """Resolve one named tool if present; return None otherwise."""
        tools = comp.GetToolList(False) or {}
        tool = tools.get(name)
        if tool is not None:
            return tool
        return next(
            (
                candidate
                for candidate in tools.values()
                if (candidate.GetAttrs() or {}).get("TOOLS_Name") == name
            ),
            None,
        )

    @classmethod
    def fusion_tool(cls, comp, name: str):
        """Resolve one named tool from an imported Recipe composition."""
        tool = cls._optional_fusion_tool(comp, name)
        if tool is None:
            raise ResolveOperationError(f"Fusion Recipe 找不到工具: {name}")
        return tool

    def configure_speed_ramp(
        self,
        comp,
        *,
        duration_frames: int,
        entry_speed: float,
        impact_speed: float,
        exit_speed: float,
        impact_frame: int,
    ) -> str:
        """Configure a deterministic three-point source-time curve."""
        if duration_frames < 2:
            raise ValueError("speed ramp 至少需要 2 帧")
        impact_frame = max(1, min(int(impact_frame), duration_frames - 2))
        tail = duration_frames - 1 - impact_frame
        first_slope = (impact_speed - entry_speed) / impact_frame
        second_slope = (exit_speed - impact_speed) / max(tail, 1)
        first_area = entry_speed * impact_frame + 0.5 * first_slope * impact_frame**2
        total_area = (
            first_area
            + impact_speed * tail
            + 0.5 * second_slope * tail**2
        )
        if total_area <= 0:
            raise ValueError("speed ramp 积分必须大于 0")
        # Fusion truncates the rendered clip when SourceTime runs past MediaIn.
        # Normalize relative speed weights so the last timeline frame maps
        # exactly to the last available source frame.
        scale = (duration_frames - 1) / total_area
        expression = (
            f"iif(time <= {impact_frame}, "
            f"({scale:.12f})*(({entry_speed:.9f})*time "
            f"+ 0.5*({first_slope:.9f})*time*time), "
            f"({scale:.12f})*(({first_area:.9f}) "
            f"+ ({impact_speed:.9f})*(time-{impact_frame}) "
            f"+ 0.5*({second_slope:.9f})*(time-{impact_frame})*(time-{impact_frame})))"
        )
        tool = self.fusion_tool(comp, "SpeedRamp")
        source_time = getattr(tool, "SourceTime", None)
        if source_time is None:
            raise ResolveOperationError("SpeedRamp.SourceTime 不可访问")
        source_time.SetExpression(expression)
        if source_time.GetExpression() != expression:
            raise ResolveOperationError("SpeedRamp.SourceTime 表达式回读不一致")
        return expression

    def configure_whip_blur_side(
        self,
        comp,
        *,
        side: str,
        duration_frames: int,
        transition_frames: int,
        length: float,
        angle: float,
        settle_scale: float = 0.0,
    ) -> str:
        """Place the accepted blur Recipe at one side of a clip boundary."""
        if side not in {"in", "out"}:
            raise ValueError(f"未知 transition side: {side}")
        if duration_frames < 1 or transition_frames < 1:
            raise ValueError("transition duration 必须至少 1 帧")
        center = 0 if side == "in" else duration_frames - 1
        width = max(1, transition_frames - 1)
        expression = (
            f"({length:.9f}) * max(0, 1 - abs(time - ({center})) / ({width}))"
        )
        tool = self.fusion_tool(comp, "MotionBlurTransition")
        tool.SetInput("Angle", angle)
        blur_length = getattr(tool, "Length", None)
        if blur_length is None:
            raise ResolveOperationError("MotionBlurTransition.Length 不可访问")
        blur_length.SetExpression(expression)
        if blur_length.GetExpression() != expression:
            raise ResolveOperationError("MotionBlurTransition.Length 表达式回读不一致")
        self._configure_settle_landing(
            comp, side=side, width=width, settle_scale=settle_scale
        )
        return expression

    def _configure_settle_landing(
        self, comp, *, side: str, width: int, settle_scale: float
    ) -> None:
        """Ease an entering clip's overshoot zoom down to rest.

        Only recipe v2 comps carry a SettleTransform tool; v1 comps have
        none, so this quietly no-ops and v1 stays exactly as accepted.
        """
        tool = self._optional_fusion_tool(comp, "SettleTransform")
        if tool is None:
            return
        size = getattr(tool, "Size", None)
        if size is None:
            raise ResolveOperationError("SettleTransform.Size 不可访问")
        if side == "in" and settle_scale > 0:
            t = f"min(1,max(0,time/{width}))"
            decay = f"(1-({t}))*(1-({t}))*(1-({t}))"
            expression = f"1 + ({settle_scale:.9f})*({decay})"
        else:
            expression = "1"
        size.SetExpression(expression)
        if size.GetExpression() != expression:
            raise ResolveOperationError("SettleTransform.Size 表达式回读不一致")

    @staticmethod
    def _reverse_scale_expression(scale: float, ease_in: str) -> str:
        """Push away from safe overscan to fill; never scale below 1."""
        return f"1 + ({scale:.9f})*(1-({ease_in}))"

    @staticmethod
    def _reverse_offset_expression(
        signed_distance: float, ease_in: str
    ) -> str:
        """Return from the prior Pull endpoint to center without a cut jump."""
        return f"({signed_distance:.9f})*(1-({ease_in}))"

    @staticmethod
    def _localized_cut_envelopes(last: int, width: int) -> tuple[str, str]:
        """Cubic entry decay and exit acceleration localized around clip edges."""
        safe_width = max(1, width)
        entry_progress = f"min(1,max(0,time/({safe_width})))"
        exit_progress = (
            f"min(1,max(0,(time-({last - safe_width}))/({safe_width})))"
        )
        entry_decay = f"(1-({entry_progress}))*(1-({entry_progress}))*(1-({entry_progress}))"
        exit_rise = f"({exit_progress})*({exit_progress})*({exit_progress})"
        return entry_decay, exit_rise

    @classmethod
    def _velocity_smooth_shake_curves(
        cls,
        *,
        duration_frames: int,
        sign: float,
        translation: float,
        scale_delta: float,
        rotation_deg: float,
        blur_strength: float,
        intensity: float,
    ) -> dict[str, dict[float, float]]:
        last = duration_frames - 1
        fast_settle = max(1, round(last * 0.25))
        stable = max(fast_settle + 1, round(last * 0.50))
        anticipation = max(stable + 1, round(last * 0.69))
        points = (0, fast_settle, stable, anticipation, last)
        distance = translation * intensity
        scale = scale_delta * intensity
        rotation = rotation_deg * intensity
        blur = blur_strength * intensity
        curves = {
            "center_x": {
                float(points[0]): 0.5 - sign * distance,
                float(points[1]): 0.5 - sign * distance * 0.14,
                float(points[2]): 0.5,
                float(points[3]): 0.5 + sign * distance * 0.14,
                float(points[4]): 0.5 + sign * distance,
            },
            "center_y": {float(frame): 0.5 for frame in points},
            "size": {
                float(points[0]): 1.04 + scale,
                float(points[1]): 1.04 + scale / 12,
                float(points[2]): 1.04,
                float(points[3]): 1.04 + scale / 12,
                float(points[4]): 1.04 + scale * 5 / 6,
            },
            "angle": {
                float(points[0]): -sign * rotation,
                float(points[1]): -sign * rotation / 8,
                float(points[2]): 0.0,
                float(points[3]): sign * rotation / 8,
                float(points[4]): sign * rotation,
            },
        }
        if blur > 0:
            curves["blur"] = {
                float(points[0]): blur,
                float(points[1]): blur / 8,
                float(points[2]): 0.0,
                float(points[3]): blur / 8,
                float(points[4]): blur,
            }
        return curves

    @staticmethod
    def _create_scalar_spline(comp, name: str, values: dict[float, float]):
        spline = comp.BezierSpline()
        if not spline:
            raise ResolveOperationError(f"{name}: BezierSpline 创建失败")
        spline.SetAttrs({"TOOLS_Name": name})
        spline.SetKeyFrames(
            {float(frame): {1: float(value)} for frame, value in values.items()}
        )
        actual = spline.GetKeyFrames() or {}
        if set(actual) != set(values):
            raise ResolveOperationError(f"{name}: BezierSpline 关键帧回读不一致")
        return spline

    @staticmethod
    def _liquid_motion_curves(
        *,
        duration_frames: int,
        x_sign: float,
        y_sign: float,
        distance: float,
        scale: float,
        zoom_direction: str,
        rotation: float,
        blur_peak: float,
        reverse: bool = False,
        peak_phase: float = 0.65,
        anticipation_ratio: float = 0.20,
        release_ratio: float = 0.22,
        inherited_velocity: float = 0.0,
        settle: bool = False,
        shake: float = 0.0,
    ) -> dict[str, dict[float, float]]:
        """Sample the demo-measured liquid position curve into Fusion splines.

        Dense samples are intentional: Resolve's BezierSpline then interpolates
        a continuous value field instead of evaluating piecewise min/max ramps
        whose first derivative jumps at every segment boundary.
        """
        if duration_frames < 2:
            raise ValueError("liquid motion 至少需要 2 帧")
        last = duration_frames - 1
        peak_phase = max(0.08, min(0.92, peak_phase))
        attack_start = max(0.0, peak_phase - anticipation_ratio)
        release_end = min(1.0, peak_phase + release_ratio)

        def smooth(value: float) -> float:
            value = max(0.0, min(1.0, value))
            return value * value * (3.0 - 2.0 * value)

        velocity_samples: list[float] = []
        for frame in range(duration_frames):
            phase = frame / last
            if phase < attack_start:
                accent = 0.0
            elif phase <= peak_phase:
                accent = smooth(
                    (phase - attack_start)
                    / max(1e-6, peak_phase - attack_start)
                )
            elif phase <= release_end:
                accent = 1.0 - smooth(
                    (phase - peak_phase)
                    / max(1e-6, release_end - peak_phase)
                )
            else:
                accent = 0.0
            # A low positive bed keeps the camera travelling on a straight
            # path.  The first 3–6 frames may inherit cut velocity from the
            # outgoing shot; this is the spatial relay that keeps a hard cut
            # from reading as a fresh camera move.
            inherited = (
                max(0.0, min(1.0, inherited_velocity))
                * (1.0 - smooth(min(1.0, phase / 0.18)))
            )
            velocity_samples.append(0.08 + 0.92 * max(accent, inherited))
        cumulative = [0.0]
        for left, right in zip(
            velocity_samples[:-1], velocity_samples[1:], strict=True
        ):
            cumulative.append(cumulative[-1] + (left + right) * 0.5)
        total = cumulative[-1] or 1.0
        base_progress = {
            float(frame): cumulative[frame] / total
            for frame in range(duration_frames)
        }
        progress = (
            {
                frame: math.sin(math.pi * value)
                for frame, value in base_progress.items()
            }
            if reverse else base_progress
        )
        velocity = {
            float(frame): (
                0.0 if frame in {0, last}
                else abs(progress[float(frame + 1)] - progress[float(frame - 1)])
            )
            for frame in range(duration_frames)
        }
        max_velocity = max(velocity.values()) or 1.0
        blur = {}
        for frame, value in velocity.items():
            inherited = (
                blur_peak * 0.35 * max(0.0, 1.0 - frame / 3.0)
                if frame < 3 else 0.0
            )
            blur[frame] = max(
                inherited, blur_peak * value / max_velocity
            )
        blur[float(last)] = 0.0
        if settle:
            # Incoming half of a beat pulse: inherit the enlarged impact frame,
            # continue for a few frames, then elastically recover most (not all)
            # of the zoom so the image never exposes canvas edges.
            size = {
                frame: 1.0 + scale * (
                    1.0 - smooth(frame / max(1, min(last, 1)))
                )
                for frame in progress
            }
        else:
            # TikTok impact transition: remain optically clean through the shot,
            # then punch only over the final two frames into the cut.
            pulse_progress = {
                frame: smooth(
                    (frame - max(0, last - 2)) / max(1, min(2, last))
                )
                for frame in progress
            }
            size = {
                frame: (
                    1.0 + scale * pulse_progress[frame]
                    if zoom_direction == "in"
                    else 1.0 + scale * (1.0 - pulse_progress[frame])
                )
                for frame in progress
            }
        shake_curve = {
            frame: (
                max(0.0, min(0.025, shake))
                * velocity[frame] / max_velocity
                * math.sin(4.0 * math.pi * frame / last)
            )
            for frame in progress
        }
        center_x = {
            frame: (
                0.5 + x_sign * distance * value
                + (shake_curve[frame] if abs(x_sign) < 1e-9 else 0.0)
            )
            for frame, value in progress.items()
        }
        center_y = {
            frame: (
                0.5 + y_sign * distance * value
                + (shake_curve[frame] if abs(y_sign) < 1e-9 else 0.0)
            )
            for frame, value in progress.items()
        }
        return {
            "center_x": center_x,
            "center_y": center_y,
            "size": size,
            "angle": {
                frame: rotation * value for frame, value in progress.items()
            },
            "blur": blur,
        }

    @classmethod
    def _connect_scalar_spline(
        cls, comp, input_object, name: str, values: dict[float, float]
    ):
        spline = cls._create_scalar_spline(comp, name, values)
        output = (spline.GetOutputList() or {}).get(1)
        if output is None or not input_object.ConnectTo(output):
            raise ResolveOperationError(f"{name}: BezierSpline 连接失败")
        return spline

    def _fresh_transform_comp(self, item, previous_name_hint: str = ""):
        """Delete stale comps, add one, and return (comp, media_in, media_out)."""
        for existing in item.GetFusionCompNameList() or []:
            if not item.DeleteFusionCompByName(existing):
                # Resolve can refuse to delete the active/selected composition.
                # Clear that comp in place so stale effects are still removed.
                comp = item.GetFusionCompByName(existing)
                if comp is None:
                    raise ResolveOperationError(f"无法删除旧 Fusion comp: {existing}")
                # Fusion exposes no DeleteTool call through Resolve's remote
                # proxy. Rewire MediaOut to a new clean chain; disconnected old
                # tools are not evaluated and therefore cannot affect pixels.
                remaining = comp.GetToolList(False) or {}
                media_in = next(
                    (t for t in remaining.values()
                     if (t.GetAttrs() or {}).get("TOOLS_RegID") == "MediaIn"), None)
                media_out = next(
                    (t for t in remaining.values()
                     if (t.GetAttrs() or {}).get("TOOLS_RegID") == "MediaOut"), None)
                if media_in is None or media_out is None:
                    raise ResolveOperationError("旧 comp 清空后缺 MediaIn/MediaOut")
                return comp, media_in, media_out
        comp = item.AddFusionComp()
        if not comp:
            raise ResolveOperationError("AddFusionComp 失败")
        tools = comp.GetToolList(False) or {}
        media_in = next(
            (t for t in tools.values()
             if (t.GetAttrs() or {}).get("TOOLS_RegID") == "MediaIn"), None)
        media_out = next(
            (t for t in tools.values()
             if (t.GetAttrs() or {}).get("TOOLS_RegID") == "MediaOut"), None)
        if media_in is None or media_out is None:
            raise ResolveOperationError("comp 缺 MediaIn/MediaOut")
        return comp, media_in, media_out

    def build_camera_curve_comp(
        self,
        item,
        *,
        comp_name: str,
        direction: str,
        magnitude: float,
        curve: str,
        duration_frames: int,
    ) -> str:
        """Render one clip's virtual camera move as an eased Transform curve.

        This is the missing per-shot movement: the compiler previously only set a
        static zoom and never read ``clip.camera``, so every shot was frozen.
        Here the whole shot pans/pushes along ``direction`` following ``curve``
        (ease-in = accelerate out toward the cut, ease-out = decelerate in from
        it), so adjacent shots whose directions match carry motion *through* the
        cut — the "被拖向下一镜" feel.  A base zoom keeps pans from exposing canvas.
        """
        if duration_frames < 2:
            raise ValueError("camera curve clip 至少需要 2 帧")
        comp, media_in, media_out = self._fresh_transform_comp(item)
        transform = comp.AddTool("Transform")
        transform.SetAttrs({"TOOLS_Name": "CameraCurve"})
        if not transform.ConnectInput("Input", media_in):
            raise ResolveOperationError("CameraCurve Transform 连接失败")
        if not media_out.ConnectInput("Input", transform):
            raise ResolveOperationError("CameraCurve MediaOut 连接失败")
        last = duration_frames - 1
        t = f"(time/{last})"
        if curve == "ease_in":
            e = f"({t})*({t})"
        elif curve == "ease_out":
            e = f"(1-(1-{t})*(1-{t}))"
        elif curve == "ease_in_out":
            e = f"({t})*({t})*(3-2*({t}))"
        else:
            e = t
        axis_x = direction in {"left", "right"}
        push = direction in {"in", "out"}
        # Transform.Center moves content opposite to the visual direction.
        sign = 1.0 if direction in {"left", "up", "out"} else -1.0
        mag = max(0.0, min(0.4, magnitude))
        if push:
            # Pure push-in/out: zoom only. Both directions stay at or above
            # the fill baseline. A pull starts enlarged and settles to 1.0;
            # a push starts at 1.0 and grows. The former implementation drove
            # push_out below 1.0 (exposing canvas) and made push_in start
            # already enlarged before growing a second magnitude.
            if direction == "in":
                size = f"1.000000 + ({mag:.6f})*({e})"
            else:
                size = f"{1.0 + mag:.6f} - ({mag:.6f})*({e})"
            center = "Point(0.5, 0.5)"
        else:
            offset = f"({sign * mag:.6f})*({e})"
            # Base zoom so the pan never reveals the canvas edge.
            size = f"{1.0 + mag * 0.8:.6f}"
            center = (
                f"Point(0.5 + ({offset}), 0.5)" if axis_x
                else f"Point(0.5, 0.5 + ({offset}))"
            )
        transform.Center.SetExpression(center)
        transform.Size.SetExpression(size)
        for input_object, expected, label in (
            (transform.Center, center, "Center"),
            (transform.Size, size, "Size"),
        ):
            if input_object.GetExpression() != expected:
                raise ResolveOperationError(
                    f"CameraCurve Transform.{label} 表达式回读不一致"
                )
        current = item.GetFusionCompNameList() or []
        if current and current[0] != comp_name:
            item.RenameFusionCompByName(current[0], comp_name)
        return center if not push else size

    def build_motion_phrase_comp(
        self,
        item,
        *,
        comp_name: str,
        stage: str,
        direction: str,
        zoom_direction: str = "in",
        intensity: float,
        duration_sec: float | None = None,
        accent_at_sec: float | None = None,
        anticipation_sec: float | None = None,
        release_sec: float | None = None,
        entry_intensity: float | None = None,
        entry_velocity: float = 0.0,
        exit_velocity: float = 0.0,
        duration_frames: int,
        transition_frames: int,
        translation: float,
        scale_delta: float,
        rotation_deg: float,
        blur_strength: float,
        eye_glow: bool = False,
        retime: dict | None = None,
    ):
        """Build one composited Fusion graph for all motion operations."""
        if duration_frames < 2:
            raise ValueError("MotionPhrase clip 至少需要 2 帧")
        for existing in item.GetFusionCompNameList() or []:
            if not item.DeleteFusionCompByName(existing):
                raise ResolveOperationError(f"无法删除旧 Fusion comp: {existing}")
        comp = item.AddFusionComp()
        if not comp:
            raise ResolveOperationError("MotionPhrase AddFusionComp 失败")
        tools = comp.GetToolList(False) or {}
        media_in = next(
            (
                tool for tool in tools.values()
                if (tool.GetAttrs() or {}).get("TOOLS_RegID") == "MediaIn"
            ),
            None,
        )
        media_out = next(
            (
                tool for tool in tools.values()
                if (tool.GetAttrs() or {}).get("TOOLS_RegID") == "MediaOut"
            ),
            None,
        )
        if media_in is None or media_out is None:
            raise ResolveOperationError("MotionPhrase comp 缺 MediaIn/MediaOut")
        # The production MotionPhrase graph is intentionally compact.  Its
        # impact language is authored here (not patched onto a rendered comp):
        # sharpen -> pulse -> liquid displacement -> one-frame cut blur -> glow.
        self._configure_three_frame_liquid_impact(
            comp,
            media_in=media_in,
            media_out=media_out,
            stage=stage,
            direction=direction,
            zoom_direction=zoom_direction,
            intensity=intensity,
            duration_sec=duration_sec,
            anticipation_sec=anticipation_sec,
            release_sec=release_sec,
            duration_frames=duration_frames,
            eye_glow=eye_glow,
            retime=retime,
        )
        names = item.GetFusionCompNameList() or []
        current_name = names[-1] if names else None
        if current_name and current_name != comp_name:
            if not item.RenameFusionCompByName(current_name, comp_name):
                raise ResolveOperationError(
                    f"MotionPhrase comp 重命名失败: {comp_name}"
                )
        return comp

    def _configure_three_frame_liquid_impact(
        self,
        comp,
        *,
        media_in,
        media_out,
        stage: str,
        direction: str,
        zoom_direction: str,
        intensity: float,
        duration_sec: float | None,
        anticipation_sec: float | None,
        release_sec: float | None,
        duration_frames: int,
        eye_glow: bool,
        retime: dict | None,
    ) -> None:
        """Author the exact sharp TikTok-AMV impact chain and keyframes."""
        last = duration_frames - 1
        seconds = (
            anticipation_sec
            if stage != "settle"
            else release_sec
        )
        window_frames = (
            round(seconds / duration_sec * last)
            if seconds is not None and duration_sec and duration_sec > 0
            else min(8, last)
        )
        curves = self._beat_pull_curves(
            duration_frames=duration_frames,
            stage=stage,
            intensity=intensity,
            window_frames=window_frames,
        )
        speed = comp.AddTool("TimeStretcher")
        if speed is None:
            raise ResolveOperationError("MotionPhrase BeatTimeRemap 创建失败")
        speed.SetAttrs({"TOOLS_Name": "BeatTimeRemap"})
        if not speed.ConnectInput("Input", media_in):
            raise ResolveOperationError("MotionPhrase BeatTimeRemap 连接失败")
        self._connect_scalar_spline(
            comp, speed.SourceTime, "BeatSourceTime", curves["source_time"]
        )
        previous = speed

        sharpen = comp.AddTool("UnsharpMask")
        transform = comp.AddTool("Transform")
        noise = comp.AddTool("FastNoise")
        displace = comp.AddTool("Displace")
        blur = comp.AddTool("DirectionalBlur")
        glow = comp.AddTool("Glow")
        if any(
            tool is None
            for tool in (sharpen, transform, noise, displace, blur, glow)
        ):
            raise ResolveOperationError("MotionPhrase 液体节点创建失败")

        sharpen.SetAttrs({"TOOLS_Name": "AnimeSharpen"})
        sharpen.SetInput("XSize", 0.008)
        sharpen.SetInput("YSize", 0.008)
        sharpen.SetInput("Gain", 1.0)
        sharpen.SetInput("Blend", 1.0)
        if not sharpen.ConnectInput("Input", previous):
            raise ResolveOperationError("MotionPhrase Sharpen 连接失败")

        transform.SetAttrs({"TOOLS_Name": "MotionTransform"})
        transform.SetInput("MotionBlur", 0.0)
        if not transform.ConnectInput("Input", sharpen):
            raise ResolveOperationError("MotionPhrase Transform 连接失败")

        if stage == "settle":
            liquid_curve = {
                0.0: 0.05,
                float(min(1, last)): 0.018,
                float(min(2, last)): 0.0,
            }
            blur_curve = {0.0: 0.05, float(min(1, last)): 0.0}
        else:
            liquid_curve = {
                0.0: 0.0,
                float(max(0, last - 1)): 0.0,
                float(last): 0.05,
            }
            blur_curve = {
                0.0: 0.0,
                float(max(0, last - 1)): 0.0,
                float(last): 0.05,
            }
        self._connect_scalar_spline(
            comp, transform.Size, "BeatPullSize", curves["size"]
        )

        noise.SetAttrs({"TOOLS_Name": "LiquidFastNoise"})
        noise.SetInput("Detail", 3.0)
        noise.SetInput("Contrast", 5.0)
        noise.SetInput("SeetheRate", 0.1)
        noise.Seethe.SetExpression("time*0.1/60")

        displace.SetAttrs({"TOOLS_Name": "LiquidDisplace"})
        displace.SetInput("CorrectEdges", 1.0)
        if not displace.ConnectInput("Input", transform):
            raise ResolveOperationError("MotionPhrase Displace 主画面连接失败")
        if not displace.ConnectInput("Foreground", noise):
            raise ResolveOperationError("MotionPhrase FastNoise 位移场连接失败")
        self._connect_scalar_spline(
            comp, displace.RefractionStrength, "LiquidStrength", liquid_curve
        )

        blur.SetAttrs({"TOOLS_Name": "CutDirectionalBlur"})
        blur.SetInput(
            "Angle",
            -45.0 if direction in {"left", "up_left", "down_left"} else 45.0,
        )
        if not blur.ConnectInput("Input", displace):
            raise ResolveOperationError("MotionPhrase DirectionalBlur 连接失败")
        self._connect_scalar_spline(
            comp, blur.Length, "CutBlurLength", blur_curve
        )

        glow.SetAttrs({"TOOLS_Name": "HighlightGlow"})
        glow.SetInput("Low", 0.72)
        glow.SetInput("Glow", 0.25)
        glow.SetInput("XGlowSize", 12.0)
        glow.SetInput("YGlowSize", 12.0)
        glow.SetInput("Blend", 0.08)
        if not glow.ConnectInput("Input", blur):
            raise ResolveOperationError("MotionPhrase Glow 连接失败")
        output_tool = glow
        if eye_glow:
            eye_mask = comp.AddTool("EllipseMask")
            eye_glow_tool = comp.AddTool("Glow")
            if eye_mask is None or eye_glow_tool is None:
                raise ResolveOperationError("MotionPhrase EyeGlow 节点创建失败")
            eye_mask.SetAttrs({"TOOLS_Name": "EyeBandMask"})
            eye_mask.SetInput("Center", {1: 0.42, 2: 0.72})
            eye_mask.SetInput("Width", 0.74)
            eye_mask.SetInput("Height", 0.25)
            eye_mask.SetInput("SoftEdge", 0.035)
            eye_glow_tool.SetAttrs({"TOOLS_Name": "MaskedEyeGlow"})
            eye_glow_tool.SetInput("Low", 0.65)
            eye_glow_tool.SetInput("Glow", 0.48)
            eye_glow_tool.SetInput("XGlowSize", 18.0)
            eye_glow_tool.SetInput("YGlowSize", 18.0)
            eye_glow_tool.SetInput("Blend", 0.24)
            eye_glow_tool.SetInput("ColorScale", 1.0)
            eye_glow_tool.SetInput("RedScale", 1.15)
            eye_glow_tool.SetInput("GreenScale", 0.55)
            eye_glow_tool.SetInput("BlueScale", 1.25)
            if not eye_glow_tool.ConnectInput("Input", glow):
                raise ResolveOperationError("MotionPhrase EyeGlow 主画面连接失败")
            if not eye_glow_tool.ConnectInput("EffectMask", eye_mask):
                raise ResolveOperationError("MotionPhrase EyeGlow 遮罩连接失败")
            output_tool = eye_glow_tool
        if not media_out.ConnectInput("Input", output_tool):
            raise ResolveOperationError("MotionPhrase MediaOut 连接失败")

    @staticmethod
    def _beat_pull_curves(
        *,
        duration_frames: int,
        stage: str,
        intensity: float,
        window_frames: int,
    ) -> dict[str, dict[float, float]]:
        """Return one reusable fixed-curvature time/size pull template.

        SourceTime is built by integrating a positive velocity field and then
        normalizing the integral to ``0..last``. This preserves every clip's
        exact frame count while creating a genuine acceleration into the cut.
        Size uses the same cubic curvature and relays its endpoint into the
        following settle clip.
        """
        if duration_frames < 2:
            raise ValueError("Beat pull 至少需要 2 帧")
        last = duration_frames - 1
        width = max(2, min(int(window_frames), last))
        settle = stage == "settle"
        weights: list[float] = []
        for frame in range(1, duration_frames):
            if settle and frame <= width:
                phase = (frame - 1) / max(1, width - 1)
                ease = 1.0 - (1.0 - phase) ** 3
                weight = 2.00 - 1.50 * ease
            elif settle:
                weight = 0.50
            elif frame > last - width:
                phase = (frame - (last - width)) / width
                weight = 0.50 + 1.50 * phase**3
            else:
                weight = 0.50
            weights.append(weight)
        total = sum(weights)
        source_time = {0.0: 0.0}
        accumulated = 0.0
        for frame, weight in enumerate(weights, start=1):
            accumulated += weight
            source_time[float(frame)] = last * accumulated / total

        size: dict[float, float] = {}
        for frame in range(duration_frames):
            if settle:
                if frame == 0:
                    value = 1.08
                elif frame == 1:
                    value = 1.02
                else:
                    value = 1.0
            elif frame < last:
                value = 1.0
            else:
                value = 1.08
            size[float(frame)] = value
        return {"source_time": source_time, "size": size}

    @staticmethod
    def transition_pair_curves(
        *,
        duration_frames: int,
        incoming_direction: str | None = None,
        outgoing_direction: str | None = None,
        frame_width: int = 1080,
    ) -> dict[str, dict[float, float]]:
        """Build one clip's half-pairs around adjacent edit points.

        Position values are authored in delivery pixels, then converted to
        Fusion's normalized Center coordinate.  Transform.Center moves the
        image opposite to the requested screen-space motion, hence the minus.
        """
        if duration_frames < 7:
            raise ValueError("Transition Pair clip 至少需要 7 帧")
        if frame_width <= 0:
            raise ValueError("frame_width 必须为正数")
        valid = {None, "left", "right"}
        if incoming_direction not in valid or outgoing_direction not in valid:
            raise ValueError("Transition Pair 方向只能是 left、right 或 None")

        last = duration_frames - 1
        position_px: dict[float, float] = {0.0: 0.0, float(last): 0.0}
        zoom: dict[float, float] = {0.0: 1.020, float(last): 1.020}
        blur: dict[float, float] = {0.0: 0.0, float(last): 0.0}

        if incoming_direction is not None:
            sign = 1.0 if incoming_direction == "left" else -1.0
            for frame, x, size, amount in (
                (0, 110, 1.080, 0.85),
                (1, 65, 1.060, 0.55),
                (2, 28, 1.040, 0.25),
                (3, 5, 1.025, 0.08),
                (4, -5, 1.018, 0.0),
                (6, 0, 1.020, 0.0),
            ):
                position_px[float(frame)] = sign * x
                zoom[float(frame)] = size
                blur[float(frame)] = amount

        if outgoing_direction is not None:
            if last < 5:
                raise ValueError("Transition Pair outgoing clip 至少需要 6 帧")
            sign = 1.0 if outgoing_direction == "left" else -1.0
            for relative, x, size, amount in (
                (-5, 0, 1.000, 0.0),
                (-3, -12, 1.010, 0.10),
                (-2, -35, 1.025, 0.35),
                (-1, -85, 1.050, 0.75),
            ):
                frame = float(last + relative + 1)
                position_px[frame] = sign * x
                zoom[frame] = size
                blur[frame] = amount

        return {
            "position_px": dict(sorted(position_px.items())),
            "center_x": {
                frame: 0.5 - value / frame_width
                for frame, value in sorted(position_px.items())
            },
            "zoom": dict(sorted(zoom.items())),
            "blur": dict(sorted(blur.items())),
        }

    def build_transition_pair_comp(
        self,
        item,
        *,
        comp_name: str,
        duration_frames: int,
        incoming_direction: str | None = None,
        outgoing_direction: str | None = None,
        frame_width: int = 1080,
    ):
        """Replace per-shot punches with two-sided directional cut motion."""
        curves = self.transition_pair_curves(
            duration_frames=duration_frames,
            incoming_direction=incoming_direction,
            outgoing_direction=outgoing_direction,
            frame_width=frame_width,
        )
        comp, media_in, media_out = self._fresh_transform_comp(item)
        base = comp.AddTool("Transform")
        pair = comp.AddTool("Transform")
        blur = comp.AddTool("DirectionalBlur")
        color = comp.AddTool("BrightnessContrast")
        if any(tool is None for tool in (base, pair, blur, color)):
            raise ResolveOperationError("Transition Pair Fusion 节点创建失败")

        base.SetAttrs({"TOOLS_Name": "Transform_Base"})
        pair.SetAttrs({"TOOLS_Name": "Transform_TransitionPair"})
        blur.SetAttrs({"TOOLS_Name": "DirectionalBlur"})
        color.SetAttrs({"TOOLS_Name": "ColorCorrector"})
        if not base.ConnectInput("Input", media_in):
            raise ResolveOperationError("Transition Pair Base 连接失败")
        if not pair.ConnectInput("Input", base):
            raise ResolveOperationError("Transition Pair Transform 连接失败")
        if not blur.ConnectInput("Input", pair):
            raise ResolveOperationError("Transition Pair Blur 连接失败")
        if not color.ConnectInput("Input", blur):
            raise ResolveOperationError("Transition Pair Color 连接失败")
        if not media_out.ConnectInput("Input", color):
            raise ResolveOperationError("Transition Pair MediaOut 连接失败")

        self._create_scalar_spline(comp, "PairCenterX", curves["center_x"])
        pair.Center.SetExpression("Point(PairCenterX.Value,0.5)")
        if pair.Center.GetExpression() != "Point(PairCenterX.Value,0.5)":
            raise ResolveOperationError("Transition Pair Center 表达式回读不一致")
        self._connect_scalar_spline(comp, pair.Size, "PairZoom", curves["zoom"])
        self._connect_scalar_spline(comp, blur.Length, "PairDirectionalBlur", curves["blur"])
        blur.SetInput(
            "Angle",
            0.0 if (outgoing_direction or incoming_direction) == "left" else 180.0,
        )
        color.SetInput("Blend", 0.0)

        current = item.GetFusionCompNameList() or []
        if current and current[-1] != comp_name:
            if not item.RenameFusionCompByName(current[-1], comp_name):
                raise ResolveOperationError(
                    f"Transition Pair comp 重命名失败: {comp_name}"
                )
        return comp

    def _legacy_build_motion_phrase_comp(
        self,
        item,
        *,
        comp_name: str,
        stage: str,
        direction: str,
        zoom_direction: str = "in",
        intensity: float,
        duration_sec: float | None = None,
        accent_at_sec: float | None = None,
        anticipation_sec: float | None = None,
        release_sec: float | None = None,
        entry_intensity: float | None = None,
        entry_velocity: float = 0.0,
        exit_velocity: float = 0.0,
        duration_frames: int,
        transition_frames: int,
        translation: float,
        scale_delta: float,
        rotation_deg: float,
        blur_strength: float,
        retime: dict | None = None,
    ):
        """Previous compositor retained temporarily for regression comparison."""
        tools = comp.GetToolList(False) or {}
        previous = media_in
        last = duration_frames - 1
        # Short clips used to switch to a symmetric shake spline. That creates
        # movement but cancels its net direction inside the shot, so a musical
        # drag becomes vibration. MotionPhrase now owns the whole velocity
        # envelope consistently at every duration.
        beat_locked_curve = False
        beat_curves = None
        if beat_locked_curve:
            sign = 1.0 if stage == "carry" else -1.0
            beat_curves = self._velocity_smooth_shake_curves(
                duration_frames=duration_frames,
                sign=sign,
                translation=translation,
                scale_delta=scale_delta,
                rotation_deg=rotation_deg,
                blur_strength=blur_strength,
                intensity=intensity,
            )
        elif retime is not None:
            speed = comp.AddTool("TimeStretcher")
            speed.SetAttrs({"TOOLS_Name": "SpeedRamp"})
            if not speed.ConnectInput("Input", previous):
                raise ResolveOperationError("MotionPhrase TimeStretcher 连接失败")
            self.configure_speed_ramp(
                comp,
                duration_frames=duration_frames,
                entry_speed=float(retime["entry_speed"]),
                impact_speed=float(retime["impact_speed"]),
                exit_speed=float(retime["exit_speed"]),
                impact_frame=int(retime["impact_frame"]),
            )
            previous = speed
        else:
            edge_clamp = comp.AddTool("TimeStretcher")
            edge_clamp.SetAttrs({"TOOLS_Name": "EdgeClamp"})
            if not edge_clamp.ConnectInput("Input", previous):
                raise ResolveOperationError(
                    "MotionPhrase EdgeClamp TimeStretcher 连接失败"
                )
            expression = f"min(time,{max(0, last - 1)})"
            edge_clamp.SourceTime.SetExpression(expression)
            if edge_clamp.SourceTime.GetExpression() != expression:
                raise ResolveOperationError(
                    "MotionPhrase EdgeClamp SourceTime 表达式回读不一致"
                )
            previous = edge_clamp

        transform = comp.AddTool("Transform")
        transform.SetAttrs({"TOOLS_Name": "MotionTransform"})
        if not transform.ConnectInput("Input", previous):
            raise ResolveOperationError("MotionPhrase Transform 连接失败")
        if beat_locked_curve and beat_curves is not None:
            center_x = self._create_scalar_spline(
                comp, "BeatShakeCenterX", beat_curves["center_x"]
            )
            center_y = self._create_scalar_spline(
                comp, "BeatShakeCenterY", beat_curves["center_y"]
            )
            center_expression = "Point(BeatShakeCenterX.Value, BeatShakeCenterY.Value)"
            transform.Center.SetExpression(center_expression)
            if transform.Center.GetExpression() != center_expression:
                raise ResolveOperationError("BeatShake Center 表达式回读不一致")
            self._connect_scalar_spline(
                comp, transform.Size, "BeatShakeSize", beat_curves["size"]
            )
            self._connect_scalar_spline(
                comp, transform.Angle, "BeatShakeAngle", beat_curves["angle"]
            )
            output_tool = transform
            if "blur" in beat_curves:
                blur = comp.AddTool("DirectionalBlur")
                blur.SetAttrs({"TOOLS_Name": "MotionBlur"})
                blur.SetInput(
                    "Angle",
                    0.0 if direction in {"left", "right"} else 90.0,
                )
                if not blur.ConnectInput("Input", transform):
                    raise ResolveOperationError(
                        "BeatShake DirectionalBlur 连接失败"
                    )
                self._connect_scalar_spline(
                    comp, blur.Length, "BeatShakeBlur", beat_curves["blur"]
                )
                output_tool = blur
            if not media_out.ConnectInput("Input", output_tool):
                raise ResolveOperationError("BeatShake MediaOut 连接失败")
            names = item.GetFusionCompNameList() or []
            current_name = names[-1] if names else None
            if current_name and current_name != comp_name:
                if not item.RenameFusionCompByName(current_name, comp_name):
                    raise ResolveOperationError(
                        f"MotionPhrase comp 重命名失败: {comp_name}"
                    )
            return comp

        # Fusion Transform.Center moves the image content in the opposite
        # visual direction. Keep both components for diagonal reference motion.
        diagonal = "-" in direction
        component_distance = translation * intensity * (0.7071 if diagonal else 1.0)
        x_sign = (
            1.0 if "left" in direction
            else -1.0 if "right" in direction
            else 0.0
        )
        y_sign = (
            1.0 if "up" in direction
            else -1.0 if "down" in direction
            else 0.0
        )
        distance = component_distance
        scale = scale_delta * intensity
        t = f"(time/{last})"
        if (
            duration_sec is not None
            and duration_sec > 0
            and accent_at_sec is not None
            and anticipation_sec is not None
            and release_sec is not None
        ):
            accent_frame = min(
                float(last),
                max(0.0, accent_at_sec / duration_sec * last),
            )
            anticipation_frames = max(
                1.0, anticipation_sec / duration_sec * last
            )
            release_frames = max(1.0, release_sec / duration_sec * last)
            start_frame = max(0.0, accent_frame - anticipation_frames)
            end_frame = min(float(last), accent_frame + release_frames)
            # Music owns the velocity envelope: calm before anticipation,
            # accelerate into the accent, then settle during release. The
            # integral remains monotonic, so this cannot create a visual
            # bounce or expose canvas edges.
            attack = (
                f"min(1,max(0,(time-({start_frame:.6f}))/"
                f"({max(1.0, accent_frame - start_frame):.6f})))"
            )
            decay = (
                f"min(1,max(0,(time-({accent_frame:.6f}))/"
                f"({max(1.0, end_frame - accent_frame):.6f})))"
            )
            before_weight = max(0.18, min(0.72, anticipation_sec / (
                anticipation_sec + release_sec
            )))
            decay_at_end = min(
                1.0,
                max(
                    0.0,
                    (float(last) - accent_frame)
                    / max(1.0, end_frame - accent_frame),
                ),
            )
            normalizer = before_weight + (
                (1.0 - before_weight)
                * decay_at_end * decay_at_end * (3.0 - 2.0 * decay_at_end)
            )
            musical_envelope = (
                f"((({before_weight:.9f})*"
                f"(({attack})^2*(3-2*({attack})))"
                f"+({1.0 - before_weight:.9f})*"
                f"(({decay})^2*(3-2*({decay}))))"
                f"/({max(normalizer, 1e-6):.9f}))"
            )
            accent_ratio = accent_frame / max(1.0, float(last))
            if accent_ratio >= 0.65:
                # Beat-to-beat velocity: confine the fast carry to the first
                # and last ~3–5 frames, with a positive calmer middle. Segment
                # distances always sum to 1, so position is continuous and
                # frame duration cannot change.
                incoming_strength = (
                    entry_intensity
                    if entry_intensity is not None else intensity
                )
                impact_distance = min(
                    0.43, 0.33 + 0.12 * incoming_strength
                )
                outgoing_distance = min(
                    0.37, 0.27 + 0.12 * intensity
                )
                entry = f"min(1,max(0,({t})/0.080000000))"
                impact = (
                    f"min(1,max(0,(({t})-0.080000000)/0.140000000))"
                )
                middle = (
                    f"min(1,max(0,(({t})-0.220000000)/0.660000000))"
                )
                outgoing = (
                    f"min(1,max(0,(({t})-0.880000000)/0.120000000))"
                )
                progress = (
                    f"(0.050000000*({entry})"
                    f"+({impact_distance:.9f})*({impact})"
                    f"+({0.95 - impact_distance - outgoing_distance:.9f})"
                    f"*({middle})"
                    f"+({outgoing_distance:.9f})*({outgoing}))"
                )
            else:
                # Interior accents retain a continuous baseline while their
                # localized envelope bends velocity around the measured hit.
                progress = (
                    f"(0.700000000*({t})"
                    f"+0.300000000*({musical_envelope}))"
                )
        elif duration_sec is not None and duration_sec > 0 and distance > 1e-9:
            entry_tangent = entry_velocity * duration_sec / distance
            exit_tangent = exit_velocity * duration_sec / distance
            # Cubic Hermite position 0→1 with physical entry/exit velocities.
            # Multiplying velocity by duration converts units/sec to the
            # derivative of normalized clip time.
            progress = (
                f"((2*({t})^3-3*({t})^2+1)*0"
                f"+(({t})^3-2*({t})^2+({t}))*({entry_tangent:.9f})"
                f"+(-2*({t})^3+3*({t})^2)"
                f"+(({t})^3-({t})^2)*({exit_tangent:.9f}))"
            )
        else:
            progress = t
        if zoom_direction not in {"in", "out"}:
            raise ValueError("MotionPhrase zoom_direction 必须是 in 或 out")
        # Demo cuts retain identifiable edges while showing radial velocity.
        # Linear DirectionalBlur at the full planner strength merely smears the
        # outgoing frame; a shorter Zoom blur reads as optical acceleration.
        peak = blur_strength * intensity * 0.06
        liquid_curves = self._liquid_motion_curves(
            duration_frames=duration_frames,
            x_sign=x_sign,
            y_sign=y_sign,
            distance=distance,
            scale=scale,
            zoom_direction=zoom_direction,
            rotation=rotation_deg * intensity,
            blur_peak=peak,
            reverse=stage == "reverse",
            peak_phase=(
                min(
                    0.92,
                    max(0.08, (accent_at_sec + 0.10) / duration_sec),
                )
                if duration_sec and accent_at_sec is not None
                else 0.65
            ),
            anticipation_ratio=(
                min(0.45, anticipation_sec / duration_sec)
                if duration_sec and anticipation_sec is not None
                else 0.20
            ),
            release_ratio=(
                min(0.50, release_sec / duration_sec)
                if duration_sec and release_sec is not None
                else 0.22
            ),
            inherited_velocity=min(
                1.0,
                entry_velocity / max(0.08, exit_velocity, entry_velocity)
            ) if entry_velocity > 0 else 0.0,
            settle=stage == "settle",
            shake=0.014 * intensity if intensity >= 0.68 else 0.0,
        )
        self._create_scalar_spline(
            comp, "LiquidCenterX", liquid_curves["center_x"]
        )
        self._create_scalar_spline(
            comp, "LiquidCenterY", liquid_curves["center_y"]
        )
        center_expression = "Point(LiquidCenterX.Value, LiquidCenterY.Value)"
        transform.Center.SetExpression(center_expression)
        if transform.Center.GetExpression() != center_expression:
            raise ResolveOperationError(
                "MotionPhrase liquid Center 表达式回读不一致"
            )
        self._connect_scalar_spline(
            comp, transform.Size, "LiquidSize", liquid_curves["size"]
        )
        self._connect_scalar_spline(
            comp, transform.Angle, "LiquidAngle", liquid_curves["angle"]
        )
        if peak > 0:
            transform.SetInput("MotionBlur", 1.0)
            transform.SetInput("Quality", 8.0)
            transform.SetInput("CenterBias", 0.0)
            transform.SetInput("SampleSpread", 1.0)
            max_blur = max(liquid_curves["blur"].values()) or 1.0
            # Main picture stays fully sharp. Motion blur exists only inside the
            # cut window: outgoing last two frames and incoming first frame.
            shutter_curve = {
                frame: (
                    66.0
                    if frame >= duration_frames - 3
                    or (stage == "settle" and frame <= 1)
                    else 0.0
                )
                for frame in liquid_curves["blur"]
            }
            shutter = getattr(transform, "ShutterAngle", None)
            if shutter is None:
                raise ResolveOperationError(
                    "MotionPhrase Transform.ShutterAngle 不可访问"
                )
            self._connect_scalar_spline(
                comp, shutter, "LiquidShutterAngle", shutter_curve
            )
            if transform.GetInput("MotionBlur") != 1.0:
                raise ResolveOperationError(
                    "MotionPhrase Transform MotionBlur 回读不一致"
                )
        if not media_out.ConnectInput("Input", transform):
            raise ResolveOperationError("MotionPhrase MediaOut 连接失败")

        names = item.GetFusionCompNameList() or []
        current_name = names[-1] if names else None
        if current_name and current_name != comp_name:
            if not item.RenameFusionCompByName(current_name, comp_name):
                raise ResolveOperationError(f"MotionPhrase comp 重命名失败: {comp_name}")
        return comp

    def configure_liquid_flow_probe(
        self,
        comp,
        *,
        strength: float = 0.075,
        detail: float = 3.2,
        scale_x: float = 0.42,
        scale_y: float = 1.25,
        seethe_rate: float = 0.18,
    ):
        """Add an unverified velocity-driven liquid refraction to a motion comp.

        This is deliberately a probe API, not an EditSpec compiler path.  It
        authors the missing *image deformation* layer for owner review without
        promoting it to a verified Recipe before a rendered A/B acceptance.
        """
        tools = comp.GetToolList(False) or {}
        transform = next(
            (
                tool for tool in tools.values()
                if (tool.GetAttrs() or {}).get("TOOLS_Name")
                == "MotionTransform"
            ),
            None,
        )
        media_out = next(
            (
                tool for tool in tools.values()
                if (tool.GetAttrs() or {}).get("TOOLS_RegID") == "MediaOut"
            ),
            None,
        )
        if transform is None or media_out is None:
            raise ResolveOperationError(
                "LiquidFlow probe 需要 MotionTransform 与 MediaOut"
            )
        noise = comp.AddTool("FastNoise")
        displace = comp.AddTool("Displace")
        if noise is None or displace is None:
            raise ResolveOperationError(
                "LiquidFlow probe 无法创建 FastNoise/Displace"
            )
        noise.SetAttrs({"TOOLS_Name": "LiquidVectorField"})
        displace.SetAttrs({"TOOLS_Name": "LiquidFlow"})
        for key, value in (
            ("Detail", detail),
            ("Contrast", 2.2),
            ("Brightness", 0.0),
            ("XScale", scale_x),
            ("YScale", scale_y),
            ("SeetheRate", seethe_rate),
        ):
            noise.SetInput(key, float(value))
        # Slow field evolution prevents random shaking.  The field itself
        # drifts continuously while musical velocity controls only amplitude.
        noise.Seethe.SetExpression(f"time*({seethe_rate:.9f})/30")
        whip = comp.AddTool("DirectionalBlur")
        if whip is None:
            raise ResolveOperationError(
                "LiquidFlow probe 无法创建 DirectionalBlur"
            )
        whip.SetAttrs({"TOOLS_Name": "BeatWhip"})
        envelope = "max(0,min(1,LiquidShutterAngle.Value/66))"
        whip.Length.SetExpression(f"0.028000000*({envelope})")
        whip.SetInput("Angle", 0.0)
        if not whip.ConnectInput("Input", transform):
            raise ResolveOperationError(
                "LiquidFlow probe 无法连接 BeatWhip"
            )
        if not displace.ConnectInput("Input", whip):
            raise ResolveOperationError(
                "LiquidFlow probe 无法连接 MotionTransform"
            )
        if not displace.ConnectInput("Foreground", noise):
            raise ResolveOperationError(
                "LiquidFlow probe 无法连接位移场"
            )
        refraction = f"({max(0.0, min(0.14, strength)):.9f})*({envelope})"
        displace.RefractionStrength.SetExpression(refraction)
        displace.SetInput("CorrectEdges", 1.0)
        displace.SetInput("MotionBlur", 1.0)
        displace.SetInput("Quality", 6.0)
        displace.SetInput("ShutterAngle", 180.0)
        flash = f"max(0,min(1,(({envelope})-0.94)/0.06))"
        channels = []
        for name, delta, mapping in (
            ("Red", -0.010, (0, 4, 4)),
            ("Green", 0.0, (4, 1, 4)),
            ("Blue", 0.010, (4, 4, 2)),
        ):
            offset = comp.AddTool("Transform")
            channel = comp.AddTool("ChannelBoolean")
            if offset is None or channel is None:
                raise ResolveOperationError(
                    "LiquidFlow probe 无法创建 RGB Split 分支"
                )
            offset.SetAttrs({"TOOLS_Name": f"Impact{name}Offset"})
            channel.SetAttrs({"TOOLS_Name": f"Impact{name}Only"})
            offset.Center.SetExpression(
                f"Point(0.5+({delta:.9f})*({flash}),0.5)"
            )
            if not offset.ConnectInput("Input", displace):
                raise ResolveOperationError("RGB Split Transform 连接失败")
            channel.SetInput("ToRed", mapping[0])
            channel.SetInput("ToGreen", mapping[1])
            channel.SetInput("ToBlue", mapping[2])
            channel.ConnectInput("Background", offset)
            channel.ConnectInput("Foreground", offset)
            channels.append(channel)
        rgb_output = channels[0]
        for index, channel in enumerate(channels[1:], start=1):
            merge = comp.AddTool("Merge")
            if merge is None:
                raise ResolveOperationError("RGB Split Merge 创建失败")
            merge.SetAttrs({"TOOLS_Name": f"ImpactAddChannel{index}"})
            merge.SetInput("ApplyMode", "Add")
            merge.ConnectInput("Background", rgb_output)
            merge.ConnectInput("Foreground", channel)
            rgb_output = merge
        grade = comp.AddTool("BrightnessContrast")
        glow = comp.AddTool("Glow")
        sharpen = comp.AddTool("UnsharpMask")
        if grade is None or glow is None or sharpen is None:
            raise ResolveOperationError(
                "LiquidFlow probe 无法创建 Impact finishing 节点"
            )
        grade.SetAttrs({"TOOLS_Name": "ImpactGradeAndFlash"})
        glow.SetAttrs({"TOOLS_Name": "HighlightGlow"})
        sharpen.SetAttrs({"TOOLS_Name": "AnimeLineSharpen"})
        grade.Gain.SetExpression(f"1+0.350000000*({flash})")
        grade.SetInput("Contrast", 0.06)
        grade.SetInput("Saturation", 1.10)
        if not grade.ConnectInput("Input", rgb_output):
            raise ResolveOperationError("Impact Grade 连接失败")
        glow.SetInput("Blend", 0.07)
        glow.SetInput("XGlowSize", 9.0)
        glow.SetInput("YGlowSize", 9.0)
        glow.SetInput("Low", 0.90)
        glow.SetInput("Glow", 0.28)
        if not glow.ConnectInput("Input", grade):
            raise ResolveOperationError("Highlight Glow 连接失败")
        sharpen.SetInput("Blend", 0.18)
        sharpen.SetInput("XSize", 0.8)
        sharpen.SetInput("YSize", 0.8)
        sharpen.SetInput("Gain", 1.0)
        if not sharpen.ConnectInput("Input", glow):
            raise ResolveOperationError("Anime sharpen 连接失败")
        if not media_out.ConnectInput("Input", sharpen):
            raise ResolveOperationError("LiquidFlow probe 无法连接 MediaOut")
        if displace.RefractionStrength.GetExpression() != refraction:
            raise ResolveOperationError(
                "LiquidFlow probe 折射强度表达式回读不一致"
            )
        return displace

    def build_zoom_defocus_probe_comp(
        self,
        item,
        *,
        comp_name: str,
        duration_frames: int,
        settle_frames: int,
        start_scale: float,
        start_blur: float,
    ):
        """Author an unverified reference-effect probe on one TimelineItem."""
        if duration_frames < 2:
            raise ValueError("Zoom Defocus probe clip 至少需要 2 帧")
        width = max(1, min(settle_frames, duration_frames - 1))
        progress = f"min(1,max(0,time/({width})))"
        decay = f"(1-({progress}))*(1-({progress}))*(1-({progress}))"
        comp, media_in, media_out = self._fresh_transform_comp(item)
        transform = comp.AddTool("Transform")
        transform.SetAttrs({"TOOLS_Name": "ZoomDefocusTransform"})
        if not transform.ConnectInput("Input", media_in):
            raise ResolveOperationError("Zoom Defocus Transform 连接失败")
        size_expression = f"1 + ({start_scale - 1:.9f})*({decay})"
        transform.Size.SetExpression(size_expression)
        if transform.Size.GetExpression() != size_expression:
            raise ResolveOperationError("Zoom Defocus Size 表达式回读不一致")
        blur = comp.AddTool("Blur")
        blur.SetAttrs({"TOOLS_Name": "ZoomDefocusBlur"})
        if not blur.ConnectInput("Input", transform):
            raise ResolveOperationError("Zoom Defocus Blur 连接失败")
        blur_expression = f"({start_blur:.9f})*({decay})"
        for name in ("XBlurSize", "YBlurSize"):
            input_object = getattr(blur, name, None)
            if input_object is None:
                raise ResolveOperationError(f"Zoom Defocus Blur 缺少 {name}")
            input_object.SetExpression(blur_expression)
            if input_object.GetExpression() != blur_expression:
                raise ResolveOperationError(
                    f"Zoom Defocus {name} 表达式回读不一致"
                )
        if not media_out.ConnectInput("Input", blur):
            raise ResolveOperationError("Zoom Defocus MediaOut 连接失败")
        names = item.GetFusionCompNameList() or []
        current_name = names[-1] if names else None
        if current_name and current_name != comp_name:
            if not item.RenameFusionCompByName(current_name, comp_name):
                raise ResolveOperationError(
                    f"Zoom Defocus comp 重命名失败: {comp_name}"
                )
        return comp

    def build_amv_velocity_probe_comp(
        self,
        item,
        *,
        comp_name: str,
        duration_frames: int,
        direction: str,
        entry_frames: int,
        exit_frames: int,
        entry_scale: float,
        exit_scale: float,
        entry_blur: float,
        exit_blur: float,
        translation: float,
    ):
        """Author one unverified, unified AMV velocity phrase clip."""
        if duration_frames < 4:
            raise ValueError("AMV velocity probe clip 至少需要 4 帧")
        last = duration_frames - 1
        entry_width = max(1, min(entry_frames, last))
        exit_width = max(1, min(exit_frames, last))
        entry_progress = f"min(1,max(0,time/({entry_width})))"
        exit_progress = (
            f"min(1,max(0,(time-({last - exit_width}))/({exit_width})))"
        )
        entry_decay = (
            f"(1-({entry_progress}))*(1-({entry_progress}))"
            f"*(1-({entry_progress}))"
        )
        exit_rise = (
            f"({exit_progress})*({exit_progress})*({exit_progress})"
        )
        sign = 1.0 if direction in {"left", "up"} else -1.0
        axis_x = direction in {"left", "right"}

        comp, media_in, media_out = self._fresh_transform_comp(item)
        transform = comp.AddTool("Transform")
        transform.SetAttrs({"TOOLS_Name": "AMVVelocityTransform"})
        if not transform.ConnectInput("Input", media_in):
            raise ResolveOperationError("AMV Velocity Transform 连接失败")
        offset = (
            f"({sign * translation:.9f})"
            f"*(({entry_decay})+({exit_rise}))"
        )
        center_expression = (
            f"Point(0.5 + ({offset}), 0.5)"
            if axis_x
            else f"Point(0.5, 0.5 + ({offset}))"
        )
        size_expression = (
            f"1 + ({entry_scale - 1:.9f})*({entry_decay})"
            f" + ({exit_scale - 1:.9f})*({exit_rise})"
        )
        transform.Center.SetExpression(center_expression)
        transform.Size.SetExpression(size_expression)
        for input_object, expected, label in (
            (transform.Center, center_expression, "Center"),
            (transform.Size, size_expression, "Size"),
        ):
            if input_object.GetExpression() != expected:
                raise ResolveOperationError(
                    f"AMV Velocity {label} 表达式回读不一致"
                )

        defocus = comp.AddTool("Blur")
        defocus.SetAttrs({"TOOLS_Name": "AMVEntryDefocus"})
        if not defocus.ConnectInput("Input", transform):
            raise ResolveOperationError("AMV Entry Defocus 连接失败")
        defocus_expression = f"({entry_blur:.9f})*({entry_decay})"
        for name in ("XBlurSize", "YBlurSize"):
            input_object = getattr(defocus, name, None)
            if input_object is None:
                raise ResolveOperationError(f"AMV Entry Defocus 缺少 {name}")
            input_object.SetExpression(defocus_expression)
            if input_object.GetExpression() != defocus_expression:
                raise ResolveOperationError(
                    f"AMV Entry Defocus {name} 表达式回读不一致"
                )

        whip = comp.AddTool("DirectionalBlur")
        whip.SetAttrs({"TOOLS_Name": "AMVExitWhip"})
        whip.SetInput("Angle", 0.0 if axis_x else 90.0)
        if not whip.ConnectInput("Input", defocus):
            raise ResolveOperationError("AMV Exit Whip 连接失败")
        whip_expression = f"({exit_blur:.9f})*({exit_rise})"
        whip.Length.SetExpression(whip_expression)
        if whip.Length.GetExpression() != whip_expression:
            raise ResolveOperationError("AMV Exit Whip 表达式回读不一致")
        if not media_out.ConnectInput("Input", whip):
            raise ResolveOperationError("AMV Velocity MediaOut 连接失败")
        names = item.GetFusionCompNameList() or []
        current_name = names[-1] if names else None
        if current_name and current_name != comp_name:
            if not item.RenameFusionCompByName(current_name, comp_name):
                raise ResolveOperationError(
                    f"AMV Velocity comp 重命名失败: {comp_name}"
                )
        return comp

    def build_fusion_comp(
        self,
        item,
        artifact: Path,
        *,
        comp_name: str,
        nodes: list[dict],
    ):
        """Build/export a linear Fusion graph used to author Recipe artifacts."""
        for existing in item.GetFusionCompNameList() or []:
            if existing == comp_name and not item.DeleteFusionCompByName(existing):
                raise ResolveOperationError(f"无法删除旧 Recipe comp: {comp_name}")
        comp = item.AddFusionComp()
        if not comp:
            raise ResolveOperationError("AddFusionComp 失败")
        tools = comp.GetToolList(False) or {}
        media_in = next(
            (
                tool for tool in tools.values()
                if (tool.GetAttrs() or {}).get("TOOLS_RegID") == "MediaIn"
            ),
            None,
        )
        media_out = next(
            (
                tool for tool in tools.values()
                if (tool.GetAttrs() or {}).get("TOOLS_RegID") == "MediaOut"
            ),
            None,
        )
        if media_in is None or media_out is None:
            raise ResolveOperationError("Fusion comp 缺 MediaIn/MediaOut")
        previous = media_in
        for node in nodes:
            tool = comp.AddTool(node["tool"])
            if not tool:
                raise ResolveOperationError(f"Fusion AddTool 失败: {node['tool']}")
            if node.get("name"):
                tool.SetAttrs({"TOOLS_Name": node["name"]})
            for key, value in node.get("inputs", {}).items():
                tool.SetInput(key, value)
                if tool.GetInput(key) != value:
                    raise ResolveOperationError(f"Fusion 输入回读不一致: {key}")
            for key, expression in node.get("expressions", {}).items():
                input_object = getattr(tool, key, None)
                if input_object is None:
                    raise ResolveOperationError(
                        f"Fusion 表达式写入失败: {node['tool']}.{key}"
                    )
                input_object.SetExpression(expression)
                if input_object.GetExpression() != expression:
                    raise ResolveOperationError(
                        f"Fusion 表达式回读不一致: {node['tool']}.{key}"
                    )
            if not tool.ConnectInput(node.get("input", "Input"), previous):
                raise ResolveOperationError(f"Fusion 节点连接失败: {node['tool']}")
            previous = tool
        if not media_out.ConnectInput("Input", previous):
            raise ResolveOperationError("Fusion MediaOut 连接失败")
        names = item.GetFusionCompNameList() or []
        current_name = names[-1] if names else None
        if current_name and current_name != comp_name:
            if not item.RenameFusionCompByName(current_name, comp_name):
                raise ResolveOperationError(f"Fusion comp 重命名失败: {comp_name}")
            names = item.GetFusionCompNameList() or []
        try:
            index = names.index(comp_name) + 1
        except ValueError as exc:
            raise ResolveOperationError(f"找不到新建 Fusion comp: {comp_name}") from exc
        artifact.parent.mkdir(parents=True, exist_ok=True)
        if not item.ExportFusionComp(str(artifact), index):
            raise ResolveOperationError(f"ExportFusionComp 失败: {artifact}")
        if not artifact.is_file() or artifact.stat().st_size == 0:
            raise ResolveOperationError(f"Fusion artifact 未落盘: {artifact}")
        return comp

    def build_rgb_split_comp(
        self,
        item,
        artifact: Path,
        *,
        comp_name: str,
        offset: float = 0.008,
    ):
        """Author a real three-branch RGB spatial split and export it."""
        for existing in item.GetFusionCompNameList() or []:
            item.DeleteFusionCompByName(existing)
        comp = item.AddFusionComp()
        tools = comp.GetToolList(False) or {}
        media_in = next(
            tool for tool in tools.values()
            if (tool.GetAttrs() or {}).get("TOOLS_RegID") == "MediaIn"
        )
        media_out = next(
            tool for tool in tools.values()
            if (tool.GetAttrs() or {}).get("TOOLS_RegID") == "MediaOut"
        )
        channels = []
        for name, delta, mapping in (
            ("Red", -offset, (0, 4, 4)),
            ("Green", 0.0, (4, 1, 4)),
            ("Blue", offset, (4, 4, 2)),
        ):
            transform = comp.AddTool("Transform")
            transform.SetAttrs({"TOOLS_Name": f"{name}Offset"})
            transform.Center.SetExpression(
                f"Point(0.5 + ({delta}) * max(0, 1 - abs(time - 10) / 10), 0.5)"
            )
            transform.ConnectInput("Input", media_in)
            channel = comp.AddTool("ChannelBoolean")
            channel.SetAttrs({"TOOLS_Name": f"{name}Only"})
            channel.SetInput("ToRed", mapping[0])
            channel.SetInput("ToGreen", mapping[1])
            channel.SetInput("ToBlue", mapping[2])
            channel.ConnectInput("Background", transform)
            channel.ConnectInput("Foreground", transform)
            channels.append(channel)
        merged = channels[0]
        for index, channel in enumerate(channels[1:], start=1):
            merge = comp.AddTool("Merge")
            merge.SetAttrs({"TOOLS_Name": f"AddChannel{index}"})
            merge.SetInput("ApplyMode", "Add")
            merge.ConnectInput("Background", merged)
            merge.ConnectInput("Foreground", channel)
            merged = merge
        media_out.ConnectInput("Input", merged)
        names = item.GetFusionCompNameList() or []
        current_name = names[-1]
        item.RenameFusionCompByName(current_name, comp_name)
        names = item.GetFusionCompNameList() or []
        artifact.parent.mkdir(parents=True, exist_ok=True)
        if not item.ExportFusionComp(str(artifact), names.index(comp_name) + 1):
            raise ResolveOperationError(f"RGB split ExportFusionComp 失败: {artifact}")
        return comp

    def apply_group_lut(
        self,
        items: list,
        *,
        group_name: str,
        lut_path: Path,
        registered_path: str | None = None,
    ):
        """Apply one ColorGroup post-clip LUT with read/write failures surfaced."""
        if not lut_path.is_file():
            raise ResolveOperationError(f"Color Recipe LUT 不存在: {lut_path}")
        group = self.project.AddColorGroup(group_name)
        if not group:
            raise ResolveOperationError(f"无法创建/获取 ColorGroup: {group_name}")
        for item in items:
            if not item.AssignToColorGroup(group):
                raise ResolveOperationError(f"片段加入 ColorGroup 失败: {group_name}")
        graph = group.GetPostClipNodeGraph()
        lut_id = registered_path or str(lut_path)
        if not graph or not graph.SetLUT(1, lut_id):
            raise ResolveOperationError(f"ColorGroup SetLUT 失败: {lut_id}")
        if not graph.GetLUT(1):
            raise ResolveOperationError(f"ColorGroup LUT 回读为空: {lut_id}")
        return group

    def apply_group_grade(
        self,
        items: list,
        *,
        group_name: str,
        drx_path: Path,
    ):
        """Apply an accepted DRX to one ColorGroup post-clip graph."""
        if not drx_path.is_file():
            raise ResolveOperationError(f"Color Recipe DRX 不存在: {drx_path}")
        group = self.project.AddColorGroup(group_name)
        if not group:
            raise ResolveOperationError(f"无法创建/获取 ColorGroup: {group_name}")
        for item in items:
            if not item.AssignToColorGroup(group):
                raise ResolveOperationError(f"片段加入 ColorGroup 失败: {group_name}")
        graph = group.GetPostClipNodeGraph()
        if not graph or not graph.ApplyGradeFromDRX(str(drx_path), 0):
            raise ResolveOperationError(f"ColorGroup ApplyGradeFromDRX 失败: {drx_path}")
        return group

    def refresh_lut_list(self) -> None:
        if not self.project.RefreshLUTList():
            raise ResolveOperationError("Resolve RefreshLUTList 失败")

    def clear_color_groups(self) -> None:
        for group in self.project.GetColorGroupsList() or []:
            if not self.project.DeleteColorGroup(group):
                raise ResolveOperationError(f"ColorGroup 删除失败: {group.GetName()}")

    def export_current_grade_drx(self, item, output: Path, *, label: str) -> Path:
        """Grab the current graded clip and export a portable DRX evidence artifact."""
        self._resolve.OpenPage("color")
        if self.timeline.GetCurrentVideoItem() is None:
            raise ResolveOperationError("Color 页当前没有可抓取的 video item")
        still = self.timeline.GrabStill()
        if not still:
            raise ResolveOperationError("Color 页 GrabStill 失败")
        gallery = self.project.GetGallery()
        album = gallery.GetCurrentStillAlbum()
        if not album.SetLabel(still, label):
            raise ResolveOperationError("Gallery still 标签写入失败")
        output.parent.mkdir(parents=True, exist_ok=True)
        if not album.ExportStills([still], str(output.parent), output.stem, "drx"):
            raise ResolveOperationError(f"DRX 导出失败: {output}")
        candidates = sorted(
            output.parent.glob(f"{output.stem}*.drx"),
            key=lambda path: path.stat().st_mtime_ns,
            reverse=True,
        )
        if not candidates:
            raise ResolveOperationError(f"DRX 导出后未找到产物: {output}")
        if candidates[0] != output:
            candidates[0].replace(output)
        return output

    # ---------- 渲染 ----------

    def render(
        self,
        *,
        output_dir: Path,
        name: str,
        preset: str,
        mark_in: int | None = None,
        mark_out: int | None = None,
        timeout_sec: float = 1800,
    ) -> RenderResult:
        output_dir = output_dir.expanduser().resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        if not self.project.LoadRenderPreset(preset):
            raise ResolveOperationError(f"Resolve render preset 加载失败: {preset}")
        settings: dict[str, object] = {
            "TargetDir": str(output_dir),
            "CustomName": name,
            "SelectAllFrames": mark_in is None,
        }
        if (mark_in is None) != (mark_out is None):
            raise ValueError("mark_in 与 mark_out 必须同时提供")
        if mark_in is not None and mark_out is not None:
            if mark_out < mark_in:
                raise ValueError("mark_out 必须 >= mark_in")
            settings.update({"MarkIn": mark_in, "MarkOut": mark_out})
        if not self.project.SetRenderSettings(settings):
            raise ResolveOperationError(f"渲染设置被 Resolve 拒绝: {settings}")
        self.project.DeleteAllRenderJobs()
        job_id = self.project.AddRenderJob()
        if not job_id:
            raise ResolveOperationError("AddRenderJob 失败")
        if not self.project.StartRendering(job_id):
            raise ResolveOperationError(f"StartRendering 失败: {job_id}")
        started = time.monotonic()
        while self.project.IsRenderingInProgress():
            if time.monotonic() - started > timeout_sec:
                self.project.StopRendering()
                raise ResolveOperationError(f"渲染超时 ({timeout_sec}s): {job_id}")
            time.sleep(0.25)
        status = self.project.GetRenderJobStatus(job_id) or {}
        if float(status.get("CompletionPercentage") or 0) < 100:
            raise ResolveOperationError(f"渲染未完成: {status}")
        outputs = sorted(
            output_dir.glob(f"{name}.*"),
            key=lambda path: path.stat().st_mtime_ns,
            reverse=True,
        )
        if not outputs:
            raise ResolveOperationError(
                f"Resolve 报告完成但找不到输出: {output_dir / (name + '.*')}"
            )
        return RenderResult(str(job_id), outputs[0], status)


__all__ = [
    "MediaInfo",
    "RenderResult",
    "ResolveAdapter",
    "ResolveOperationError",
    "ResolveUnavailable",
]
