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

    # ---------- Fusion 曲线基元 ----------

    @staticmethod
    def _create_scalar_spline(comp, name: str, values: dict[float, float]):
        """实测坑（两层）：

        1. BezierSpline 的关键帧**位置**（不是数值）在 Resolve 内部只保留 4 位
           小数——提交 17.167347 回读得到的是 17.1673，不是原值。调用方若用
           6 位小数算帧号（本仓库多处用 ``round(sec * fps, 6)``），必须先按
           Resolve 实际精度取整，而不是要求每个调用方各自记得。
        2. 就算提交前已经按 4 位小数取整，回读值仍可能带浮点噪声（提交
           0.3001，回读 0.30010000000000003）——大概率是 Resolve 内部按
           frame/fps 之类的比例重新算了一遍。按 ``set`` 做精确浮点比较必然
           偶发不一致，所以回读值也要按同样精度取整再比较。

        数值本身（KeyFrame 的 ``{1: value}``）不受影响，只有位置需要取整。
        """
        rounded = {round(float(frame), 4): float(value) for frame, value in values.items()}
        spline = comp.BezierSpline()
        if not spline:
            raise ResolveOperationError(f"{name}: BezierSpline 创建失败")
        spline.SetAttrs({"TOOLS_Name": name})
        spline.SetKeyFrames(
            {frame: {1: value} for frame, value in rounded.items()}
        )
        actual = spline.GetKeyFrames() or {}
        actual_frames = {round(float(frame), 4) for frame in actual}
        if actual_frames != set(rounded):
            raise ResolveOperationError(f"{name}: BezierSpline 关键帧回读不一致")
        return spline

    @classmethod
    def _connect_scalar_spline(
        cls, comp, input_object, name: str, values: dict[float, float]
    ):
        spline = cls._create_scalar_spline(comp, name, values)
        output = (spline.GetOutputList() or {}).get(1)
        if output is None or not input_object.ConnectTo(output):
            raise ResolveOperationError(f"{name}: BezierSpline 连接失败")
        return spline

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
