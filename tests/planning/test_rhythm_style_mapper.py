from __future__ import annotations

import pytest

from studio.planning.rhythm_style_mapper import MIN_SLOT_DURATION_SEC, _merge_short_slots, choose_mode, map_rhythm_to_slots
from studio.planning.slots import TimelineSlot
from studio.spec.music_timeline import Accent, MusicTimeline, Section
from studio.spec.reference_blueprint import (
    CutObservation,
    Estimate,
    ReferenceBlueprint,
    ShotObservation,
    StyleSummary,
    TechnicalProfile,
)


def _blueprint(music_ref: str | None = None) -> ReferenceBlueprint:
    return ReferenceBlueprint(
        source_hash="demo123",
        technical=TechnicalProfile(
            width=1080, height=1350, fps_num=24000, fps_den=1001, duration_sec=4.0, aspect="4:5",
        ),
        music_timeline_ref=music_ref,
        shots=[
            ShotObservation(
                index=0, start_sec=0.0, end_sec=2.0, duration_sec=2.0,
                visual_energy=0.3, brightness=0.5,
                native_motion_estimate=Estimate(value=0.1, confidence=0.6, evidence=[]),
                global_motion_estimate=Estimate(value=0.1, confidence=0.6, evidence=[]),
                motion_confidence=0.6,
            ),
            ShotObservation(
                index=1, start_sec=2.0, end_sec=4.0, duration_sec=2.0,
                visual_energy=0.8, brightness=0.4,
                native_motion_estimate=Estimate(value=0.6, confidence=0.7, evidence=[]),
                global_motion_estimate=Estimate(value=0.6, confidence=0.7, evidence=[]),
                motion_confidence=0.7,
            ),
        ],
        cuts=[CutObservation(sec=2.0, type="hard_cut", confidence=0.9, relation="carry")],
        style_summary=StyleSummary(
            cut_density=0.5, motion_coverage=0.6, hold_ratio=0.3,
            reversal_ratio=0.1, scale_motion_ratio=0.3, blur_usage=0.3,
        ),
    )


def _music(source_hash: str, duration=8.0) -> MusicTimeline:
    beats = [round(i * 0.5, 3) for i in range(int(duration / 0.5))]
    return MusicTimeline(
        source_hash=source_hash, duration_sec=duration,
        selected_tempo=120.0, tempo_confidence=0.8,
        beats=beats, downbeats=beats[::4],
        sections=[Section(start_sec=0.0, end_sec=duration, label="main")],
        accents=[
            Accent(sec=b, kind="downbeat" if b in beats[::4] else "beat", strength=0.6, confidence=0.8)
            for b in beats
        ] + [Accent(sec=0.0, kind="section_boundary", strength=1.0, confidence=0.9),
             Accent(sec=duration, kind="section_boundary", strength=1.0, confidence=0.9)],
    )


def test_exact_replica_mode_is_chosen_when_music_matches_demo_audio():
    blueprint = _blueprint(music_ref="abc")
    music = _music("abc")
    assert choose_mode(blueprint, music) == "exact_replica"

    slots = map_rhythm_to_slots(blueprint, music)
    assert [s.start_sec for s in slots] == [0.0, 2.0]
    assert [s.duration_sec for s in slots] == [2.0, 2.0]
    assert slots[1].entry_motion == "carry"


def test_exact_replica_merges_sub_frame_shots_into_the_previous_slot():
    # Regression: cut-detection artifacts near a real cut can produce a
    # "shot" a fraction of a frame long (observed on a real Demo). No
    # candidate clip can back a slot that short, and Resolve's own Fusion
    # comp placement needs at least a frame of runway, so it must be
    # absorbed rather than emitted as its own unplaceable slot.
    blueprint = _blueprint(music_ref="abc")
    tiny_shot = ShotObservation(
        index=1, start_sec=2.0, end_sec=2.02, duration_sec=0.02,
        visual_energy=0.5, brightness=0.5,
        native_motion_estimate=Estimate(value=0.1, confidence=0.5, evidence=[]),
        global_motion_estimate=Estimate(value=0.1, confidence=0.5, evidence=[]),
        motion_confidence=0.5,
    )
    real_second_shot = blueprint.shots[1].model_copy(update={"index": 2, "start_sec": 2.02})
    blueprint = blueprint.model_copy(
        update={"shots": [blueprint.shots[0], tiny_shot, real_second_shot]}
    )
    music = _music("abc")

    slots = map_rhythm_to_slots(blueprint, music)

    assert len(slots) == 2
    assert slots[0].duration_sec == 2.02  # 2.0 (first shot) + 0.02 (absorbed sliver)
    assert slots[1].start_sec == 2.02


def test_merge_short_slots_absorbs_any_sub_threshold_slot_from_either_mode():
    # style_transfer builds slots from selected music-accent boundaries, and
    # two accents landing almost on top of each other produces the same
    # unplaceable-sliver problem exact_replica has from cut-detection
    # artifacts — _merge_short_slots is the single point both modes route
    # through (map_rhythm_to_slots), so it must not care which mode
    # produced the sliver.
    slots = [
        TimelineSlot(index=0, start_sec=0.0, duration_sec=1.0, target_energy=0.5),
        TimelineSlot(index=1, start_sec=1.0, duration_sec=0.01, target_energy=0.5),
        TimelineSlot(index=2, start_sec=1.01, duration_sec=1.0, target_energy=0.5),
    ]
    merged = _merge_short_slots(slots, min_duration=MIN_SLOT_DURATION_SEC)
    assert len(merged) == 2
    assert merged[0].duration_sec == pytest.approx(1.01)
    assert merged[1].duration_sec == pytest.approx(1.0)


def test_style_transfer_mode_uses_target_music_event_times_not_demo_times():
    blueprint = _blueprint(music_ref="abc")
    different_music = _music("xyz", duration=8.0)
    assert choose_mode(blueprint, different_music) == "style_transfer"

    slots = map_rhythm_to_slots(blueprint, different_music)
    assert slots
    # Not a linear stretch of the 4s Demo onto 8s: boundaries land on actual
    # target-track accent times, not {0, 4, 8} or any fixed multiple.
    demo_shot_boundaries = {0.0, 2.0, 4.0}
    boundary_secs = {round(s.start_sec, 3) for s in slots}
    assert boundary_secs != demo_shot_boundaries
    assert all(0.0 <= s.start_sec < different_music.duration_sec for s in slots)
    assert abs(sum(s.duration_sec for s in slots) - different_music.duration_sec) < 0.01


def test_style_transfer_is_deterministic_given_identical_inputs():
    blueprint = _blueprint(music_ref="abc")
    music = _music("xyz")
    first = map_rhythm_to_slots(blueprint, music)
    second = map_rhythm_to_slots(blueprint, music)
    assert [s.model_dump() for s in first] == [s.model_dump() for s in second]


def test_style_transfer_cut_density_scales_with_target_duration():
    blueprint = _blueprint(music_ref="abc")
    short_music = _music("short", duration=4.0)
    long_music = _music("long", duration=16.0)
    short_slots = map_rhythm_to_slots(blueprint, short_music)
    long_slots = map_rhythm_to_slots(blueprint, long_music)
    assert len(long_slots) >= len(short_slots)
