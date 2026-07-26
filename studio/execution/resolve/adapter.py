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

    def audio_items(self, track_index: int = 1) -> list:
        return self.timeline.GetItemListInTrack("audio", track_index) or []

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
    def fusion_tool(comp, name: str):
        """Resolve one named tool from an imported Recipe composition."""
        tools = comp.GetToolList(False) or {}
        tool = tools.get(name)
        if tool is None:
            tool = next(
                (
                    candidate
                    for candidate in tools.values()
                    if (candidate.GetAttrs() or {}).get("TOOLS_Name") == name
                ),
                None,
            )
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
        return expression

    def build_motion_phrase_comp(
        self,
        item,
        *,
        comp_name: str,
        stage: str,
        direction: str,
        intensity: float,
        duration_frames: int,
        transition_frames: int,
        translation: float,
        scale_delta: float,
        rotation_deg: float,
        blur_strength: float,
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
        previous = media_in
        if retime is not None:
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

        transform = comp.AddTool("Transform")
        transform.SetAttrs({"TOOLS_Name": "MotionTransform"})
        if not transform.ConnectInput("Input", previous):
            raise ResolveOperationError("MotionPhrase Transform 连接失败")
        last = duration_frames - 1
        # Fusion Transform.Center moves the image content in the opposite
        # visual direction, so semantic left/up require a positive center.
        sign = 1.0 if direction in {"left", "up"} else -1.0
        axis_x = direction in {"left", "right"}
        distance = translation * intensity
        scale = scale_delta * intensity
        t = f"(time/{last})"
        ease_in = f"({t})*({t})*({t})"
        ease_out = f"(1-(1-{t})*(1-{t})*(1-{t}))"
        if stage in {"accelerate", "whip"}:
            offset = f"({sign * distance:.9f})*({ease_in})"
            size = f"1 + ({scale:.9f})*({ease_in})"
        elif stage == "carry":
            offset = f"({sign * distance:.9f})*(2*({ease_out})-1)"
            size = (
                f"1 + ({scale:.9f})*"
                f"(1-abs(2*({ease_out})-1))*0.65"
            )
        elif stage == "settle":
            offset = f"({-sign * distance:.9f})*(1-({ease_out}))"
            size = f"1 + ({scale * 0.55:.9f})*(1-({ease_out}))"
        elif stage == "reverse":
            offset = f"({-sign * distance:.9f})*({ease_in})"
            size = f"1 + ({scale:.9f})*({ease_in})"
        else:
            offset = "0"
            size = "1"
        center = (
            f"Point(0.5 + ({offset}), 0.5)"
            if axis_x
            else f"Point(0.5, 0.5 + ({offset}))"
        )
        transform.Center.SetExpression(center)
        transform.Size.SetExpression(size)
        transform.Angle.SetExpression(
            f"({rotation_deg * intensity:.9f})*({ease_in})"
        )
        for input_object, expected, label in (
            (transform.Center, center, "Center"),
            (transform.Size, size, "Size"),
        ):
            if input_object.GetExpression() != expected:
                raise ResolveOperationError(
                    f"MotionPhrase Transform.{label} 表达式回读不一致"
                )

        blur = comp.AddTool("DirectionalBlur")
        blur.SetAttrs({"TOOLS_Name": "MotionBlur"})
        blur.SetInput("Angle", 0.0 if axis_x else 90.0)
        if not blur.ConnectInput("Input", transform):
            raise ResolveOperationError("MotionPhrase DirectionalBlur 连接失败")
        width = max(1, transition_frames - 1)
        peak = blur_strength * intensity
        entry_envelope = f"max(0, 1-abs(time-0)/({width}))"
        exit_envelope = f"max(0, 1-abs(time-({last}))/({width}))"
        if stage in {"accelerate", "whip"}:
            envelope = exit_envelope
        elif stage == "carry":
            envelope = f"max({entry_envelope}, {exit_envelope})"
        elif stage in {"settle", "reverse"}:
            envelope = entry_envelope
        else:
            envelope = "0"
        blur_expression = f"({peak:.9f})*({envelope})"
        blur.Length.SetExpression(blur_expression)
        if blur.Length.GetExpression() != blur_expression:
            raise ResolveOperationError("MotionPhrase Blur 表达式回读不一致")
        if not media_out.ConnectInput("Input", blur):
            raise ResolveOperationError("MotionPhrase MediaOut 连接失败")

        names = item.GetFusionCompNameList() or []
        current_name = names[-1] if names else None
        if current_name and current_name != comp_name:
            if not item.RenameFusionCompByName(current_name, comp_name):
                raise ResolveOperationError(f"MotionPhrase comp 重命名失败: {comp_name}")
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
