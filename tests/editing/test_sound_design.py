import pytest

from studio.creative.director.plan import (
    DirectorPlan,
    ImpactBudget,
)
from studio.critic.creative import evaluate_sound_design
from studio.editing.music import MusicMap
from studio.editing.sound import apply_sound_design
from studio.editspec.schema import (
    Canvas,
    Clip,
    EditSpec,
    SfxCue,
    SourceRange,
    Timebase,
    TimelinePlacement,
)


def _music(**kw) -> MusicMap:
    base = dict(
        duration_sec=6.0, bpm=120.0, beats=[], bars=[], downbeats=[],
        onsets=[], beat_energy=[], sections=[], impact_points=[],
        risers=[], breaks=[], silences=[], spectral_change_points=[],
    )
    base.update(kw)
    return MusicMap(**base)


def _plan(sfx_max: int = 9) -> DirectorPlan:
    return DirectorPlan(
        project_id="p", revision=1, duration_sec=6.0,
        primary_characters=["x"], tone=["aggressive"],
        structure=[], visual_rules={}, sound_strategy="low source -> impact",
        impact_budget=ImpactBudget(sfx_max=sfx_max, flash_max=3, shake_max=3),
        generation={},
    )


def _clip(cid, t_in, dur, tokens=None):
    from studio.editspec.schema import SourceSelection
    ss = None
    if tokens is not None:
        ss = SourceSelection(
            phase="impact", anchor_sec=1.0, confidence=0.8,
            evidence=[f"semantic:{tok}" for tok in tokens],
        )
    return Clip(
        id=cid, asset_id="a", shot_id=cid,
        source=SourceRange(in_sec=10.0, out_sec=10.0 + dur),
        timeline=TimelinePlacement(in_sec=t_in, duration_sec=dur),
        role="impact",
        source_selection=ss,
    )


def _spec(clips):
    return EditSpec(
        id="t", timebase=Timebase(num=24, den=1),
        canvas=Canvas(width=3072, height=3072, aspect="1:1"),
        clips=clips,
    )


def test_impact_lands_on_beat_and_riser_precedes():
    # Cuts at 0, 2, 4; impacts on the 2s and 4s cuts.
    spec = _spec([
        _clip("c1", 0.0, 2.0),
        _clip("c2", 2.0, 2.0),
        _clip("c3", 4.0, 2.0),
    ])
    music = _music(impact_points=[2.0, 4.0])
    out, report = apply_sound_design(spec, music=music, plan=_plan())
    assert report.designed_targets == 2
    # An impact cue lands exactly on each target beat.
    abs_impacts = sorted(
        clip.timeline.in_sec + cue.at_sec
        for clip in out.clips for cue in clip.audio.sfx
        if cue.recipe in {"impact_low_v1", "sub_impact_v1"}
    )
    assert abs_impacts == [2.0, 4.0]
    # The riser for the 2s beat sits on the previous clip, before the beat.
    riser_times = sorted(
        clip.timeline.in_sec + cue.at_sec
        for clip in out.clips for cue in clip.audio.sfx
        if cue.recipe == "riser_v1"
    )
    assert riser_times and all(rt < 2.0 or (2.0 < rt < 4.0) for rt in riser_times)


def test_budget_caps_total_cues():
    spec = _spec([
        _clip("c1", 0.0, 1.0), _clip("c2", 1.0, 1.0), _clip("c3", 2.0, 1.0),
        _clip("c4", 3.0, 1.0), _clip("c5", 4.0, 1.0),
    ])
    music = _music(impact_points=[1.0, 2.0, 3.0, 4.0])
    out, report = apply_sound_design(spec, music=music, plan=_plan(sfx_max=3))
    total = sum(len(clip.audio.sfx) for clip in out.clips)
    assert total <= 3
    assert report.cue_count <= 3


def test_impact_skipped_inside_silence():
    spec = _spec([_clip("c1", 0.0, 2.0), _clip("c2", 2.0, 2.0)])
    music = _music(impact_points=[2.0], silences=[])
    music_silent = _music(
        impact_points=[2.0],
        silences=[__import__("studio.editing.music.map", fromlist=["TimeRange"]).TimeRange(start=1.95, end=2.1)],
    )
    _, report = apply_sound_design(spec, music=music_silent, plan=_plan())
    assert report.silence_skipped_impacts == 1
    # No on-beat impact cue was placed at the silenced target.
    _, ref = apply_sound_design(spec, music=music, plan=_plan())
    assert ref.silence_skipped_impacts == 0


def test_existing_managed_cues_are_replaced_not_duplicated():
    clip = _clip("c2", 2.0, 2.0)
    clip.audio = clip.audio.model_copy(
        update={"sfx": [SfxCue(recipe="impact_low_v1", at_sec=0.0)]}
    )
    spec = _spec([_clip("c1", 0.0, 2.0), clip])
    music = _music(impact_points=[2.0])
    out, _ = apply_sound_design(spec, music=music, plan=_plan())
    impacts = [
        cue for c in out.clips for cue in c.audio.sfx
        if cue.recipe in {"impact_low_v1", "sub_impact_v1"}
    ]
    assert len(impacts) == 1


def test_sword_token_adds_whoosh():
    spec = _spec([
        _clip("c1", 0.0, 2.0),
        _clip("c2", 2.0, 2.0, tokens=["sword"]),
    ])
    music = _music(impact_points=[2.0])
    out, _ = apply_sound_design(spec, music=music, plan=_plan())
    whooshes = [
        cue for c in out.clips for cue in c.audio.sfx
        if cue.recipe == "sword_whoosh_v1"
    ]
    assert whooshes


def test_sound_qa_flags_coverage_and_orphans():
    spec = _spec([
        _clip("c1", 0.0, 2.0),
        _clip("c2", 2.0, 2.0),
    ])
    music = _music(impact_points=[2.0])
    out, _ = apply_sound_design(spec, music=music, plan=_plan())
    qa = evaluate_sound_design(out, music)
    assert qa.impact_targets == 1
    assert qa.covered_targets == 1
    assert qa.coverage_ratio == pytest.approx(1.0)
    assert qa.orphan_risers == 0
    assert qa.passed
