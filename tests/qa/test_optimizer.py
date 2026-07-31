from __future__ import annotations

from studio.qa.optimizer import (
    DEFAULT_PARAMS,
    optimize_test_interval,
    pick_representative_interval,
)
from studio.qa.rendered_qa import MetricComparison, RenderedQAReport
from studio.spec.amv import (
    AMVSpec, Canvas, Clip, InputHashes, Motion, MusicRef, RenderSettings,
    SourceRange, Timebase, TimelinePlacement, TransformKeyframe, TransitionPair,
)
from studio.spec.music_timeline import Accent, MusicTimeline

CANVAS = Canvas(width=1080, height=1350, aspect="4:5")
TIMEBASE = Timebase(num=24000, den=1000)


def _clip(clip_id: str, start: float, duration: float) -> Clip:
    return Clip(
        id=clip_id, asset_id="a1",
        source=SourceRange(in_sec=0.0, out_sec=duration + 0.5),
        timeline=TimelinePlacement(in_sec=start, duration_sec=duration),
        motion=Motion(
            transform_keyframes=[
                TransformKeyframe(sec=start, center_x=0.5, center_y=0.5, scale=1.0),
                TransformKeyframe(sec=start + duration, center_x=0.55, center_y=0.5, scale=1.1),
            ]
        ),
    )


def _spec() -> AMVSpec:
    clips = [_clip("c0", 0.0, 2.0), _clip("c1", 2.0, 2.0), _clip("c2", 4.0, 2.0)]
    pairs = [
        TransitionPair(
            id="t0", cut_sec=2.0, outgoing_clip_id="c0", incoming_clip_id="c1",
            direction="left", safe_scale=1.2, overshoot=0.05, confidence=0.7,
        ),
        TransitionPair(
            id="t1", cut_sec=4.0, outgoing_clip_id="c1", incoming_clip_id="c2",
            direction="left", safe_scale=1.2, overshoot=0.0, confidence=0.6,
        ),
    ]
    return AMVSpec(
        id="test-amv",
        input_hashes=InputHashes(demo="d", materials_index="m"),
        timebase=TIMEBASE, canvas=CANVAS, duration_sec=6.0,
        music=MusicRef(path="music.wav", timeline_hash="h"),
        clips=clips, transition_pairs=pairs,
        render=RenderSettings(output_path="out.mov"),
    )


def _music() -> MusicTimeline:
    return MusicTimeline(
        source_hash="h", duration_sec=6.0, selected_tempo=128.0, tempo_confidence=0.9,
        accents=[Accent(sec=3.0, kind="downbeat", strength=0.9, confidence=0.9)],
    )


def test_pick_representative_interval_finds_a_window_with_two_cuts_and_an_accent():
    interval = pick_representative_interval(_spec(), _music())
    assert interval is not None
    start, end = interval
    assert 2.0 <= start <= end <= 6.0
    assert end - start >= 3.0


def test_pick_representative_interval_returns_none_with_fewer_than_two_cuts():
    spec = _spec()
    spec = spec.model_copy(update={"transition_pairs": spec.transition_pairs[:1]})
    assert pick_representative_interval(spec, _music()) is None


def _report(passed: bool, motion_coverage_actual: float = 0.6) -> RenderedQAReport:
    from studio.critic.technical.qa import TechnicalQAResult

    metric = MetricComparison(
        reference=0.6, actual=motion_coverage_actual,
        difference=abs(0.6 - motion_coverage_actual), tolerance=0.25,
        passed=abs(0.6 - motion_coverage_actual) <= 0.25,
    )
    return RenderedQAReport(
        technical=TechnicalQAResult(file="x.mov", passed=True, checks=[]),
        fusion_graph_consistent=True,
        metrics={"motion_coverage": metric},
        passed=passed and metric.passed,
    )


def test_optimize_test_interval_stops_immediately_when_first_round_passes():
    calls = []

    def render_fn(spec, params, interval):
        calls.append(dict(params))
        return "rendered.mov"

    def qa_fn(path):
        return _report(True)

    rounds = optimize_test_interval(_spec(), (2.0, 6.0), render_fn=render_fn, qa_fn=qa_fn)

    assert len(rounds) == 1
    assert rounds[0].params == DEFAULT_PARAMS
    assert rounds[0].report.passed


def test_optimize_test_interval_adjusts_the_mapped_param_on_failure_and_stops_at_max_rounds():
    def render_fn(spec, params, interval):
        return "rendered.mov"

    def qa_fn(path):
        return _report(False, motion_coverage_actual=0.05)

    rounds = optimize_test_interval(_spec(), (2.0, 6.0), render_fn=render_fn, qa_fn=qa_fn, max_rounds=4)

    assert len(rounds) == 4
    assert not rounds[-1].report.passed
    # translation_gain is the mapped param for motion_coverage and should
    # have moved away from its neutral default every round.
    gains = [r.params["translation_gain"] for r in rounds]
    assert gains == sorted(gains)
    assert gains[-1] > gains[0]
