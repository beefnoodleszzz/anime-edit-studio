"""时间码内核 —— 全仓**唯一**的帧↔秒↔timecode 换算实现。

AGENTS.md R8：EditSpec 以秒为权威，帧数由此模块换算。
禁止在其他模块重复实现任何 round(sec * fps) 逻辑。

为什么必须集中在一处
--------------------
实测素材帧率为 23.976（= 24000/1001），交付时间线可能是 24 / 30 / 60。
非整数帧率下，任何"就地 round(sec*fps)"都会累积亚帧漂移：
v1 系统硬编 60fps、逐处换算，正是它需要 fps.py / interpolate.py
一堆补丁的根因。

核心约定
--------
1. **秒是权威**，帧是派生。所有对外接口收发秒。
2. 帧率用有理数 (num, den) 精确表示，不用 float。23.976 → 24000/1001。
3. 换算一律走 Fraction，最后一步才取整，杜绝浮点累积误差。
4. 源帧与时间线帧是**两个不同的时基**，不可混用（见 SOURCE vs TIMELINE 注释）。
"""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from math import floor

__all__ = ["Timebase", "NTSC_24", "NTSC_30", "NTSC_60", "Timecode", "TimecodeError"]


class TimecodeError(ValueError):
    """无效的时基或时间码。"""


# 常见广播帧率的精确有理数表示
_WELL_KNOWN: dict[float, tuple[int, int]] = {
    23.976: (24000, 1001),
    23.98: (24000, 1001),
    29.97: (30000, 1001),
    59.94: (60000, 1001),
    47.952: (48000, 1001),
    119.88: (120000, 1001),
}


@dataclass(frozen=True)
class Timebase:
    """精确帧率。

    ``num/den`` 为每秒帧数。23.976 fps 表示为 24000/1001，而非 float。

    ``drop_frame`` 只影响 timecode 字符串的呈现，**不影响**帧数与秒的换算。
    仅 29.97 / 59.94 允许 drop_frame。
    """

    num: int
    den: int = 1
    drop_frame: bool = False

    def __post_init__(self) -> None:
        if self.num <= 0 or self.den <= 0:
            raise TimecodeError(f"帧率必须为正: {self.num}/{self.den}")
        if self.drop_frame and not self._is_ntsc_df_rate():
            raise TimecodeError(
                f"drop_frame 仅适用于 29.97/59.94，当前 {self.fps_float}"
            )

    # ---------- 构造 ----------

    @classmethod
    def from_fps(cls, fps: float | int | str, *, drop_frame: bool = False) -> "Timebase":
        """从常见写法构造。优先匹配广播标准帧率，避免 float 噪声。"""
        if isinstance(fps, str):
            fps = float(fps)
        if isinstance(fps, int) or (isinstance(fps, float) and fps.is_integer()):
            return cls(int(fps), 1, drop_frame=drop_frame)
        key = round(float(fps), 3)
        if key in _WELL_KNOWN:
            num, den = _WELL_KNOWN[key]
            return cls(num, den, drop_frame=drop_frame)
        # 未知非整数帧率：用有限分母逼近，避免 float 直接入库
        frac = Fraction(float(fps)).limit_denominator(100000)
        return cls(frac.numerator, frac.denominator, drop_frame=drop_frame)

    # ---------- 基本属性 ----------

    @property
    def rate(self) -> Fraction:
        return Fraction(self.num, self.den)

    @property
    def fps_float(self) -> float:
        return self.num / self.den

    @property
    def is_ntsc(self) -> bool:
        return self.den == 1001

    def _is_ntsc_df_rate(self) -> bool:
        return (self.num, self.den) in {(30000, 1001), (60000, 1001)}

    @property
    def nominal_fps(self) -> int:
        """名义帧率：23.976→24, 29.97→30, 59.94→60。用于 timecode 呈现。"""
        return round(self.fps_float) if self.is_ntsc else self.num // self.den

    # ---------- 换算 ----------

    def to_frames(self, seconds: float | Fraction, *, mode: str = "round") -> int:
        """秒 → 帧。

        mode:
            round —— 最近帧（默认，用于时间线位置）
            floor —— 向下取整（用于源入点，避免越过素材头）
            ceil  —— 向上取整（用于源出点，避免丢掉最后一帧）
        """
        if seconds < 0:
            raise TimecodeError(f"时间不能为负: {seconds}")
        exact = Fraction(seconds).limit_denominator(1_000_000_000) * self.rate
        if mode == "round":
            # 银行家舍入会让 x.5 不稳定，这里用「四舍五入到最近整数」
            return int(exact + Fraction(1, 2)) if exact >= 0 else -int(-exact + Fraction(1, 2))
        if mode == "floor":
            return floor(exact)
        if mode == "ceil":
            return -floor(-exact)
        raise TimecodeError(f"未知 mode: {mode!r}")

    def to_seconds(self, frames: int) -> Fraction:
        """帧 → 秒（精确有理数，调用方需要 float 时自行转换）。"""
        return Fraction(frames) / self.rate

    def duration_frames(self, start_sec: float, duration_sec: float) -> int:
        """时长 → 帧数。

        用「结束帧 - 起始帧」而非「时长 × fps」，
        保证相邻片段首尾相接、不产生 1 帧空隙或重叠。
        这是拼接时间线时最容易出错的地方。
        """
        start_f = self.to_frames(start_sec)
        end_f = self.to_frames(Fraction(start_sec).limit_denominator(1_000_000_000)
                               + Fraction(duration_sec).limit_denominator(1_000_000_000))
        return max(1, end_f - start_f)

    def frames_for_rebased_duration(
        self,
        target_frames: int,
        target_timebase: "Timebase",
    ) -> int:
        """Source-frame count representing an exact target-frame duration.

        This keeps cross-timebase rounding in the one authoritative timecode
        module.  It is used when Resolve needs a source-frame half-open range
        whose conformed duration must exactly fill known timeline boundaries.
        """
        if target_frames < 1:
            raise TimecodeError("目标时长必须至少 1 帧")
        exact = Fraction(target_frames) * self.rate / target_timebase.rate
        return max(1, int(exact + Fraction(1, 2)))

    # ---------- timecode 字符串 ----------

    def to_timecode(self, frames: int) -> str:
        """帧 → "HH:MM:SS:FF"（drop-frame 用 ';' 分隔秒与帧）。"""
        if frames < 0:
            raise TimecodeError("帧号不能为负")
        fps = self.nominal_fps
        if self.drop_frame:
            frames = self._apply_drop_frame(frames)
        ff = frames % fps
        total_sec = frames // fps
        ss, mm, hh = total_sec % 60, (total_sec // 60) % 60, total_sec // 3600
        sep = ";" if self.drop_frame else ":"
        return f"{hh:02d}:{mm:02d}:{ss:02d}{sep}{ff:02d}"

    def _apply_drop_frame(self, frames: int) -> int:
        """把真实帧号转成 drop-frame 计数（每分钟跳号，第 10 分钟除外）。"""
        drop = 2 if self.num == 30000 else 4
        frames_per_min = self.nominal_fps * 60 - drop
        frames_per_10min = self.nominal_fps * 600 - drop * 9
        d, m = divmod(frames, frames_per_10min)
        if m < drop:
            m += drop
        return frames + drop * 9 * d + drop * ((m - drop) // frames_per_min)

    def __str__(self) -> str:
        label = f"{self.fps_float:.3f}".rstrip("0").rstrip(".")
        return f"{label}fps{' DF' if self.drop_frame else ''}"


NTSC_24 = Timebase(24000, 1001)
NTSC_30 = Timebase(30000, 1001)
NTSC_60 = Timebase(60000, 1001)


@dataclass(frozen=True)
class Timecode:
    """一个绑定了时基的时间点。避免裸帧号在模块间流动时丢失时基。

    SOURCE vs TIMELINE
    ------------------
    源帧（素材自身时基，如 23.976）与时间线帧（交付时基，如 24）**不可混用**。
    Resolve 的 AppendToTimeline 里 startFrame/endFrame 是**源帧**，
    recordFrame 是**时间线帧**。混淆二者会导致片段错位且难以察觉。
    """

    frames: int
    timebase: Timebase

    @classmethod
    def from_seconds(cls, seconds: float, timebase: Timebase, *, mode: str = "round") -> "Timecode":
        return cls(timebase.to_frames(seconds, mode=mode), timebase)

    @property
    def seconds(self) -> float:
        return float(self.timebase.to_seconds(self.frames))

    def rebase(self, target: Timebase, *, mode: str = "round") -> "Timecode":
        """换算到另一时基。经由秒中转，保持时间语义不变。"""
        return Timecode.from_seconds(self.seconds, target, mode=mode)

    def __str__(self) -> str:
        return self.timebase.to_timecode(self.frames)
