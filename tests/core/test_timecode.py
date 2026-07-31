"""timecode 内核测试 —— 重点在非整数帧率与拼接无缝。

这些边界正是 v1 系统踩过的坑（AGENTS.md P4）。
"""
from __future__ import annotations

from fractions import Fraction

import pytest

from studio.core.timecode import (
    NTSC_24,
    NTSC_30,
    Timebase,
    Timecode,
    TimecodeError,
)


class TestTimebaseConstruction:
    def test_integer_fps(self):
        tb = Timebase.from_fps(24)
        assert (tb.num, tb.den) == (24, 1)
        assert tb.fps_float == 24.0

    @pytest.mark.parametrize(
        "fps,expected",
        [
            (23.976, (24000, 1001)),
            (23.98, (24000, 1001)),
            (29.97, (30000, 1001)),
            (59.94, (60000, 1001)),
        ],
    )
    def test_ntsc_rates_are_exact_rationals(self, fps, expected):
        """23.976 必须是 24000/1001，不能是 float —— 这是漂移的根源。"""
        assert (Timebase.from_fps(fps).num, Timebase.from_fps(fps).den) == expected

    def test_float_that_is_integral(self):
        assert Timebase.from_fps(60.0) == Timebase(60, 1)

    def test_rejects_nonpositive(self):
        with pytest.raises(TimecodeError):
            Timebase(0, 1)
        with pytest.raises(TimecodeError):
            Timebase(24, 0)

    def test_drop_frame_only_for_ntsc_df_rates(self):
        Timebase(30000, 1001, drop_frame=True)          # ok
        Timebase(60000, 1001, drop_frame=True)          # ok
        with pytest.raises(TimecodeError):
            Timebase(24, 1, drop_frame=True)
        with pytest.raises(TimecodeError):
            Timebase(24000, 1001, drop_frame=True)


class TestConversion:
    def test_roundtrip_exact_at_2397(self):
        """23.976 下往返换算必须精确，不得累积误差。"""
        tb = NTSC_24
        for frame in (0, 1, 24, 1000, 86_399):
            assert tb.to_frames(tb.to_seconds(frame)) == frame

    def test_no_drift_over_long_duration(self):
        """v1 的核心 bug：逐处 round(sec*fps) 在长片上漂移。"""
        tb = NTSC_24
        # 1 小时 = 86400 帧（名义），23.976 下为 86313.6...
        one_hour_frames = tb.to_frames(3600)
        assert one_hour_frames == round(3600 * 24000 / 1001)
        # 往返不丢帧
        assert tb.to_frames(tb.to_seconds(one_hour_frames)) == one_hour_frames

    def test_modes(self):
        tb = Timebase(24, 1)
        # 1.5 帧处
        sec = Fraction(1, 16)  # 0.0625s -> 1.5 frames @24fps
        assert tb.to_frames(sec, mode="floor") == 1
        assert tb.to_frames(sec, mode="ceil") == 2
        assert tb.to_frames(sec, mode="round") == 2

    def test_rejects_negative_seconds(self):
        with pytest.raises(TimecodeError):
            Timebase(24, 1).to_frames(-0.1)

    def test_unknown_mode(self):
        with pytest.raises(TimecodeError):
            Timebase(24, 1).to_frames(1.0, mode="nope")


class TestSeamlessConcatenation:
    """相邻片段必须首尾相接 —— 无 1 帧空隙、无 1 帧重叠。"""

    @pytest.mark.parametrize("tb", [Timebase(24, 1), NTSC_24, NTSC_30, Timebase(60, 1)])
    def test_adjacent_clips_have_no_gap(self, tb):
        durations = [1.51, 0.42, 2.33, 0.07, 1.0] * 8
        cursor_sec = 0.0
        cursor_frame = 0
        for d in durations:
            n = tb.duration_frames(cursor_sec, d)
            start_f = tb.to_frames(cursor_sec)
            assert start_f == cursor_frame, "片段起点与上一段终点不接"
            cursor_frame = start_f + n
            cursor_sec += d

    def test_duration_frames_never_zero(self):
        """极短片段也至少占 1 帧，否则 Resolve 会拒绝。"""
        tb = NTSC_24
        assert tb.duration_frames(0.0, 0.001) == 1
        assert tb.duration_frames(10.0, 0.0) == 1

    def test_rebased_duration_preserves_target_frame_count(self):
        source = Timebase(30000, 1001)
        timeline = Timebase(24000, 1001)
        assert source.frames_for_rebased_duration(48, timeline) == 60
        assert timeline.frames_for_rebased_duration(48, timeline) == 48

    def test_rebased_duration_ceil_prevents_fractional_conform_gap(self):
        source = Timebase(24000, 1001)
        timeline = Timebase(30, 1)
        assert source.frames_for_rebased_duration(43, timeline) == 35
        assert source.frames_for_rebased_duration(8, timeline) == 7


class TestTimecodeString:
    def test_non_drop_frame(self):
        tb = Timebase(24, 1)
        assert tb.to_timecode(0) == "00:00:00:00"
        assert tb.to_timecode(23) == "00:00:00:23"
        assert tb.to_timecode(24) == "00:00:01:00"
        assert tb.to_timecode(24 * 60) == "00:01:00:00"
        assert tb.to_timecode(24 * 3600) == "01:00:00:00"

    def test_drop_frame_uses_semicolon(self):
        tb = Timebase(30000, 1001, drop_frame=True)
        assert ";" in tb.to_timecode(100)

    def test_drop_frame_skips_at_minute_boundary(self):
        """DF 在每分钟跳 2 帧，第 10 分钟不跳。"""
        tb = Timebase(30000, 1001, drop_frame=True)
        # 第 1 分钟结束处应跳号
        assert tb.to_timecode(30 * 60 - 1) == "00:00:59;29"
        assert tb.to_timecode(30 * 60) == "00:01:00;02"

    def test_rejects_negative_frames(self):
        with pytest.raises(TimecodeError):
            Timebase(24, 1).to_timecode(-1)


class TestTimecodeObject:
    def test_carries_its_timebase(self):
        tc = Timecode.from_seconds(1.0, NTSC_24)
        assert tc.timebase is NTSC_24
        assert tc.frames == 24  # 23.976 下 1s ≈ 24 帧

    def test_rebase_preserves_time_not_frames(self):
        """源 23.976 → 时间线 60fps：帧号变，时间语义不变。

        注意误差是**量化误差**而非漂移：2.0s 在 23.976 下落到第 48 帧，
        而第 48 帧的精确时间是 48/(24000/1001) = 2.002s。
        这 2ms 是源时基本身的栅格，不可消除，也不会累积。
        容差取半帧。
        """
        src = Timecode.from_seconds(2.0, NTSC_24)
        dst = src.rebase(Timebase(60, 1))
        assert dst.frames == 120
        half_frame = 0.5 / NTSC_24.fps_float
        assert abs(dst.seconds - src.seconds) < half_frame

    def test_quantization_error_does_not_accumulate(self):
        """关键性质：反复 rebase 不应让误差滚雪球。

        这正是 v1 逐处 round(sec*fps) 会失败的地方。
        """
        tb_src, tb_dst = NTSC_24, Timebase(60, 1)
        tc = Timecode.from_seconds(2.0, tb_src)
        original = tc.seconds
        for _ in range(50):
            tc = tc.rebase(tb_dst).rebase(tb_src)
        assert abs(tc.seconds - original) < 1e-9, "往返 50 次后出现漂移"

    def test_str_is_timecode(self):
        assert str(Timecode(24, Timebase(24, 1))) == "00:00:01:00"
