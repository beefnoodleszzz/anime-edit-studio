from studio.core.timecode import Timebase
from studio.editing.timing.action_sync import (
    _source_position,
    solve_action_sync,
)

TB = Timebase.from_fps(24)


def _landed_frame(sol, duration_sec):
    """Timeline frame where the peak source frame actually renders."""
    frames = TB.to_frames(duration_sec)
    best, best_gap = 0, float("inf")
    for time in range(frames):
        source = _source_position(
            float(time),
            duration_frames=frames,
            impact_frame=sol.impact_frame,
            entry_speed=sol.retime.entry_speed,
            impact_speed=sol.retime.impact_speed,
            exit_speed=sol.retime.exit_speed,
        )
        gap = abs(source - sol.peak_source_frame)
        if gap < best_gap:
            best, best_gap = time, gap
    return best


def test_peak_already_on_marker_stays_constant():
    sol = solve_action_sync(
        timebase=TB,
        clip_duration_sec=1.5,
        marker_offset_sec=0.75,
        peak_source_offset_sec=0.75,
    )
    assert sol.retime.type == "constant"
    assert sol.residual_frames == 0


def test_peak_later_than_marker_lands_on_beat_when_unclamped():
    sol = solve_action_sync(
        timebase=TB,
        clip_duration_sec=2.0,
        marker_offset_sec=1.2,
        peak_source_offset_sec=1.5,
    )
    assert sol.retime.type == "speed_ramp"
    if not sol.clamped:
        # The measured peak renders within one delivery frame of the beat.
        assert abs(_landed_frame(sol, 2.0) - sol.impact_frame) <= 1
        assert abs(sol.residual_frames) <= 1


def test_peak_earlier_than_marker_lands_on_beat_when_unclamped():
    sol = solve_action_sync(
        timebase=TB,
        clip_duration_sec=2.0,
        marker_offset_sec=1.0,
        peak_source_offset_sec=0.7,
    )
    assert sol.retime.type == "speed_ramp"
    if not sol.clamped:
        assert abs(_landed_frame(sol, 2.0) - sol.impact_frame) <= 1


def test_impact_at_sec_matches_marker():
    sol = solve_action_sync(
        timebase=TB,
        clip_duration_sec=1.6,
        marker_offset_sec=0.6,
        peak_source_offset_sec=1.1,
    )
    assert sol.retime.impact_at_sec is not None
    assert abs(sol.retime.impact_at_sec - TB.to_seconds(sol.impact_frame)) < 1e-6


def test_extreme_shift_clamps_and_reports_residual():
    # A peak crammed against the clip end cannot be pulled to an early marker;
    # the solver must clamp and report a non-zero, honest residual.
    sol = solve_action_sync(
        timebase=TB,
        clip_duration_sec=1.0,
        marker_offset_sec=0.1,
        peak_source_offset_sec=0.95,
    )
    assert sol.clamped
    assert abs(sol.residual_frames) >= 1
    assert 0.1 <= sol.retime.entry_speed <= 4.0


def test_too_short_clip_is_constant():
    sol = solve_action_sync(
        timebase=TB,
        clip_duration_sec=0.05,
        marker_offset_sec=0.02,
        peak_source_offset_sec=0.04,
    )
    assert sol.retime.type == "constant"
