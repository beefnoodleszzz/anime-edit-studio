import sqlite3

import pytest

from studio.creative.director.plan import DirectorPlan, DirectorSection, ImpactBudget
from studio.editing.camera import (
    CAMERA_ASSIGNMENT_VERSION,
    assign_camera_moves,
)
from studio.editing.music import MusicMap
from studio.editspec.schema import (
    Canvas,
    Clip,
    CutRelation,
    EditSpec,
    MotionBeat,
    MotionPhrase,
    RecipeRef,
    SourceRange,
    Timebase,
    TimelinePlacement,
    Track,
)


def _music(**kw) -> MusicMap:
    base = dict(
        duration_sec=8.0, bpm=120.0, beats=[], bars=[], downbeats=[],
        onsets=[], beat_energy=[], sections=[], impact_points=[],
        risers=[], breaks=[], silences=[], spectral_change_points=[],
    )
    base.update(kw)
    return MusicMap(**base)


def _conn(rows: list[tuple[str, str | None, float]]) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE shots (id TEXT PRIMARY KEY, motion_dir TEXT, motion_mag REAL)"
    )
    conn.executemany("INSERT INTO shots VALUES (?,?,?)", rows)
    return conn


def _clip(cid: str, t_in: float, *, kind: str | None = None, **kw) -> Clip:
    return Clip(
        id=cid, asset_id="a", shot_id=cid,
        source=SourceRange(in_sec=10.0, out_sec=10.5),
        timeline=TimelinePlacement(in_sec=t_in, duration_sec=0.5),
        role="impact",
        incoming_cut=(
            None if kind is None
            else CutRelation(kind=kind, motivation="test", confidence=0.8)
        ),
        **kw,
    )


def _spec(clips: list[Clip]) -> EditSpec:
    return EditSpec(
        id="t", timebase=Timebase(num=30, den=1),
        canvas=Canvas(width=1080, height=1350),
        tracks=[Track(id="V1", kind="video")],
        clips=clips,
    )


def test_rides_measured_footage_direction():
    """A shot that already moves left is panned left, not against itself."""
    spec = _spec([_clip("s0", 0.0), _clip("s1", 0.5), _clip("s2", 1.0)])
    conn = _conn([
        ("s0", "left", 3.0), ("s1", "up-right", 2.5), ("s2", "down", 4.0),
    ])
    updated, report = assign_camera_moves(spec, conn=conn, music=_music())
    assert [clip.camera.move for clip in updated.clips] == [
        "pan_left", "pan_right", "pan_down",
    ]
    assert report.version == CAMERA_ASSIGNMENT_VERSION
    assert report.assigned == 3
    assert all(item.basis == "footage_flow" for item in report.decisions)


def test_weak_motion_is_not_ridden():
    """Below the rideable threshold the direction is noise, so push instead."""
    spec = _spec([_clip("s0", 0.0)])
    conn = _conn([("s0", "left", 0.4)])
    updated, report = assign_camera_moves(spec, conn=conn, music=_music())
    assert updated.clips[0].camera.move == "push_in"
    assert report.decisions[0].basis == "role_push"


def test_static_shot_breaks_or_carries_by_cut_kind():
    """Only where footage offers no direction does the join decide."""
    spec = _spec([
        _clip("s0", 0.0),
        _clip("s1", 0.5, kind="contrast"),
        _clip("s2", 1.0, kind="match_action"),
    ])
    conn = _conn([("s0", "right", 3.0), ("s1", "static", 0.0), ("s2", "static", 0.0)])
    updated, report = assign_camera_moves(spec, conn=conn, music=_music())
    moves = [clip.camera.move for clip in updated.clips]
    assert moves == ["pan_right", "pan_left", "pan_left"]
    assert [item.basis for item in report.decisions] == [
        "footage_flow", "break_previous", "carry_previous",
    ]
    assert report.broken_joins == 1
    assert report.carried_joins == 1


def test_measured_direction_is_never_overridden_by_the_join():
    """The reference reverses across 45% of cuts; forcing carry kills variety."""
    spec = _spec([_clip("s0", 0.0), _clip("s1", 0.5, kind="match_action")])
    conn = _conn([("s0", "right", 3.0), ("s1", "left", 3.0)])
    updated, _ = assign_camera_moves(spec, conn=conn, music=_music())
    assert [clip.camera.move for clip in updated.clips] == ["pan_right", "pan_left"]


def test_occupied_fusion_slot_is_skipped_not_silently_overwritten():
    """Effects/ramps/transitions own the single comp slot; writing camera lies."""
    spec = _spec([
        _clip("s0", 0.0, effects=[RecipeRef(recipe="white_flash_v1")]),
        _clip("s1", 0.5),
    ])
    conn = _conn([("s0", "left", 3.0), ("s1", "right", 3.0)])
    updated, report = assign_camera_moves(spec, conn=conn, music=_music())
    assert updated.clips[0].camera.move == "none"
    assert updated.clips[1].camera.move == "pan_right"
    assert report.skipped_occupied == 1
    assert report.assigned == 1


def test_motion_phrase_clip_is_skipped_not_silently_overwritten():
    """A MotionPhrase beat also occupies the one Fusion comp slot.

    Regression: _apply_recipes (compiler.py) builds the MotionPhrase comp
    first, then unconditionally deletes *all* comps on the item before
    building a camera curve (_fresh_transform_comp) — so a camera move
    written onto a phrase clip silently overwrote the phrase's own comp at
    render time. Confirmed on a real spec: all 32 clips carrying a
    MotionPhrase beat also had camera.move set, meaning none of that
    project's 8 computed whip/zoom/blur phrases ever actually rendered.
    """
    spec = _spec([_clip("s0", 0.0), _clip("s1", 0.5), _clip("s2", 1.0), _clip("s3", 1.5)])
    spec.motion_phrases.append(
        MotionPhrase(
            id="phrase-0",
            beats=[
                MotionBeat(clip_id="s1", stage="accelerate", intensity=0.6),
                MotionBeat(clip_id="s2", stage="reverse", intensity=0.6),
            ],
            direction="left",
        )
    )
    conn = _conn([
        ("s0", "left", 3.0), ("s1", "left", 3.0),
        ("s2", "left", 3.0), ("s3", "left", 3.0),
    ])
    updated, report = assign_camera_moves(spec, conn=conn, music=_music())
    by_id = {clip.id: clip for clip in updated.clips}
    assert by_id["s1"].camera.move == "none"
    assert by_id["s2"].camera.move == "none"
    assert by_id["s0"].camera.move != "none"
    assert by_id["s3"].camera.move != "none"
    skipped_reasons = {
        item.clip_id: item.reason
        for item in report.decisions if item.basis == "skipped"
    }
    assert "motion_phrase" in skipped_reasons["s1"]
    assert "motion_phrase" in skipped_reasons["s2"]
    assert report.skipped_occupied == 2


def test_impact_points_move_further_than_off_beat_cuts():
    spec = _spec([_clip("s0", 0.0), _clip("s1", 4.0)])
    conn = _conn([("s0", "left", 3.0), ("s1", "left", 3.0)])
    updated, _ = assign_camera_moves(
        spec, conn=conn, music=_music(impact_points=[0.0])
    )
    assert updated.clips[0].camera.to_scale > updated.clips[1].camera.to_scale


def test_section_energy_shapes_a_calm_to_climax_arc():
    """An on-beat cut in a quiet section must move less than one in a loud
    section, even though local beat proximity is identical for both.

    Regression: before this, magnitude only looked at proximity to a
    beat/impact point, so an opening establishing shot snapped to a beat
    moved exactly as much as the climax — the whole cut read as one flat
    shake regardless of where it sat in the DirectorPlan's own energy arc.
    """
    spec = _spec([_clip("s0", 0.0), _clip("s1", 4.0)])
    conn = _conn([("s0", "left", 3.0), ("s1", "left", 3.0)])
    music = _music(impact_points=[0.0, 4.0])
    plan = DirectorPlan(
        project_id="arc", revision=1, duration_sec=8,
        primary_characters=[], tone=[],
        structure=[
            DirectorSection(role="opening", start=0, end=3, energy=0.3, average_shot_length=1),
            DirectorSection(role="impact", start=3, end=8, energy=0.95, average_shot_length=1),
        ],
        visual_rules={"prefer": [], "avoid": []}, sound_strategy="test",
        impact_budget=ImpactBudget(sfx_max=1, flash_max=1, shake_max=1),
        generation={"llm_used": False},
    )
    updated, _ = assign_camera_moves(spec, conn=conn, music=music, plan=plan)
    quiet_scale, loud_scale = (
        updated.clips[0].camera.to_scale, updated.clips[1].camera.to_scale,
    )
    assert loud_scale > quiet_scale


def test_assignment_is_deterministic():
    conn = _conn([("s0", "left", 3.0), ("s1", "down", 2.0)])
    first, _ = assign_camera_moves(
        _spec([_clip("s0", 0.0), _clip("s1", 0.5)]), conn=conn, music=_music()
    )
    second, _ = assign_camera_moves(
        _spec([_clip("s0", 0.0), _clip("s1", 0.5)]), conn=conn, music=_music()
    )
    assert first.model_dump_json() == second.model_dump_json()


def test_rejects_invalid_magnitude_window():
    with pytest.raises(ValueError):
        assign_camera_moves(
            _spec([_clip("s0", 0.0)]), conn=_conn([]), music=_music(),
            min_magnitude=0.3, max_magnitude=0.1,
        )
