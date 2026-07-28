from studio.asset_intelligence.motion.action_peak import ActionPeak
from studio.editing.music import MusicMap
from studio.editing.timing import apply_action_sync
from studio.editspec.schema import (
    Canvas,
    Clip,
    EditSpec,
    SourceRange,
    Timebase,
    TimelinePlacement,
)


def _music(**kw) -> MusicMap:
    base = dict(
        duration_sec=6.0,
        bpm=120.0,
        beats=[],
        bars=[],
        downbeats=[],
        onsets=[],
        beat_energy=[],
        sections=[],
        impact_points=[],
        risers=[],
        breaks=[],
        silences=[],
        spectral_change_points=[],
    )
    base.update(kw)
    return MusicMap(**base)


def _spec(clips: list[Clip]) -> EditSpec:
    return EditSpec(
        id="t",
        timebase=Timebase(num=24, den=1),
        canvas=Canvas(width=3072, height=3072, aspect="1:1"),
        clips=clips,
    )


def _clip(cid: str, t_in: float, dur: float, src_in: float) -> Clip:
    return Clip(
        id=cid,
        asset_id="a",
        shot_id="shot1",
        source=SourceRange(in_sec=src_in, out_sec=src_in + dur),
        timeline=TimelinePlacement(in_sec=t_in, duration_sec=dur),
    )


def test_achievable_peak_gets_retimed():
    # Clip source window [10,12]; shot starts at 8, peak at shot-relative 2.9s
    # => absolute source 10.9s => 0.9s into the window.  An impact sits 0.7s
    # into the clip: a small, reachable shift, so the clip is retimed.
    spec = _spec([_clip("c1", 0.0, 2.0, 10.0)])
    music = _music(impact_points=[0.7], downbeats=[0.0, 2.0])
    peaks = {"shot1": [ActionPeak(sec=2.9, magnitude=5.0, confidence=0.9)]}
    starts = {"shot1": 8.0}
    out, report = apply_action_sync(
        spec, music=music, peaks_by_shot=peaks, shot_starts=starts
    )
    assert report.measured_clips == 1
    row = report.rows[0]
    assert row.measured_peak
    assert row.target_kind == "impact"
    assert out.clips[0].retime.type == "speed_ramp"
    assert row.retimed
    assert row.action_peak_error_frames <= 2


def test_unreachable_peak_keeps_hard_cut():
    # A large shift on a short clip cannot seat the peak; keep the hard cut
    # rather than distort motion off the beat.
    spec = _spec([_clip("c1", 0.0, 2.0, 10.0)])
    music = _music(impact_points=[0.5])
    peaks = {"shot1": [ActionPeak(sec=3.8, magnitude=5.0, confidence=0.9)]}
    out, report = apply_action_sync(
        spec, music=music, peaks_by_shot=peaks, shot_starts={"shot1": 8.0}
    )
    assert report.measured_clips == 1
    assert report.retimed_clips == 0
    assert out.clips[0].retime.type == "constant"
    assert "kept hard cut" in report.rows[0].note


def test_no_measured_peak_stays_hard_cut():
    spec = _spec([_clip("c1", 0.0, 2.0, 10.0)])
    music = _music(impact_points=[0.5])
    out, report = apply_action_sync(
        spec, music=music, peaks_by_shot={}, shot_starts={"shot1": 8.0}
    )
    assert report.measured_clips == 0
    assert out.clips[0].retime.type == "constant"
    assert "no measured peak" in report.rows[0].note


def test_peak_outside_source_window_ignored():
    # Peak at shot-relative 0.1s => absolute 8.1s, before the window [10,12].
    spec = _spec([_clip("c1", 0.0, 2.0, 10.0)])
    music = _music(impact_points=[0.5])
    peaks = {"shot1": [ActionPeak(sec=0.1, magnitude=5.0, confidence=0.9)]}
    out, report = apply_action_sync(
        spec, music=music, peaks_by_shot=peaks, shot_starts={"shot1": 8.0}
    )
    assert report.measured_clips == 0
    assert out.clips[0].retime.type == "constant"


def test_no_in_window_target_is_reported_not_retimed():
    spec = _spec([_clip("c1", 0.0, 2.0, 10.0)])
    # Only a target on the cut itself (0.0), which is excluded by MIN offset.
    music = _music(downbeats=[0.0])
    peaks = {"shot1": [ActionPeak(sec=3.0, magnitude=5.0, confidence=0.9)]}
    out, report = apply_action_sync(
        spec, music=music, peaks_by_shot=peaks, shot_starts={"shot1": 8.0}
    )
    assert report.measured_clips == 1
    assert report.retimed_clips == 0
    assert "no in-window musical target" in report.rows[0].note


def test_report_counts_on_beat_clips():
    spec = _spec([_clip("c1", 0.0, 2.0, 10.0)])
    music = _music(impact_points=[1.0])
    # Peak already near 1.0s into the window renders on the beat cheaply.
    peaks = {"shot1": [ActionPeak(sec=3.0, magnitude=5.0, confidence=0.9)]}
    _, report = apply_action_sync(
        spec, music=music, peaks_by_shot=peaks, shot_starts={"shot1": 8.0}
    )
    assert report.on_beat_clips >= 1
    assert report.max_action_peak_error_frames <= 1
