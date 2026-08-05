from __future__ import annotations

import pytest

from studio.planning.motion_planner import (
    build_clip_motion,
    build_transition_pair,
    direction_vector_for,
    required_safe_scale,
)
from studio.planning.slots import TimelineSlot
from studio.planning.transition_profile import TransitionProfile
from studio.spec.amv import Canvas

CANVAS = Canvas(width=1080, height=1350, aspect="4:5")


def test_required_safe_scale_grows_with_translation_and_stays_above_one():
    small = required_safe_scale(0.02, 0.0, CANVAS)
    large = required_safe_scale(0.2, 0.0, CANVAS)
    assert small >= 1.0
    assert large > small


def test_required_safe_scale_accounts_for_rotation():
    no_rotation = required_safe_scale(0.05, 0.0, CANVAS)
    rotated = required_safe_scale(0.05, 0.0, CANVAS, rotation_deg=15.0)
    assert rotated >= no_rotation


def test_hold_slot_produces_static_low_amplitude_motion():
    slot = TimelineSlot(index=0, start_sec=0, duration_sec=1.0, target_energy=0.2, hold=True)
    motion = build_clip_motion(slot, CANVAS)
    scales = {kf.scale for kf in motion.transform_keyframes}
    assert len(scales) == 1
    assert not motion.native_motion_blur_keyframes


def test_moving_slot_ends_at_a_safe_scale_covering_the_translation():
    slot = TimelineSlot(
        index=1, start_sec=0, duration_sec=1.2, target_energy=0.8, hold=False, entry_motion="carry",
    )
    motion = build_clip_motion(slot, CANVAS, direction=direction_vector_for("left"))
    end_scale = motion.transform_keyframes[-1].scale
    assert end_scale > 1.0
    assert motion.native_motion_blur_keyframes


def test_short_clip_gets_a_damped_push_not_the_full_designed_magnitude():
    # Regression: a fixed-magnitude push covered in a much shorter clip than
    # it was designed for reads as unnaturally fast whip-speed motion blur —
    # found running a real fast-cut AMV where almost every clip was shorter
    # than the reference duration. A short clip's end keyframe should sit
    # closer to center than a clip at/above the reference duration.
    short_slot = TimelineSlot(index=0, start_sec=0, duration_sec=0.15, target_energy=0.8, entry_motion="carry")
    long_slot = TimelineSlot(index=1, start_sec=0, duration_sec=1.2, target_energy=0.8, entry_motion="carry")
    short_motion = build_clip_motion(short_slot, CANVAS, direction=direction_vector_for("left"))
    long_motion = build_clip_motion(long_slot, CANVAS, direction=direction_vector_for("left"))
    short_dx = abs(short_motion.transform_keyframes[-1].center_x - 0.5)
    long_dx = abs(long_motion.transform_keyframes[-1].center_x - 0.5)
    assert short_dx < long_dx


def test_clip_motion_blur_is_constant_not_an_artificial_ramp():
    # Regression: shutter angle used to escalate 0.3x -> 1x across a clip's
    # own duration even though the transform curve is one constant-velocity
    # linear move — found via dense per-frame sharpness measurement on a
    # real render, where a clip's readability decayed ~60x from its
    # sharpest to its final frame despite nothing speeding up. Blur must be
    # driven by the transform's actual velocity, not an independent ramp.
    slot = TimelineSlot(
        index=0, start_sec=0, duration_sec=2.9, target_energy=0.8, hold=False, entry_motion="carry",
    )
    motion = build_clip_motion(slot, CANVAS, direction=direction_vector_for("left"))
    shutter_values = {kf.shutter_angle for kf in motion.native_motion_blur_keyframes}
    assert len(shutter_values) == 1


def test_faster_effective_pan_gets_more_shutter_angle_than_a_slower_one():
    fast_slot = TimelineSlot(index=0, start_sec=0, duration_sec=0.15, target_energy=0.8, entry_motion="carry")
    slow_slot = TimelineSlot(index=1, start_sec=0, duration_sec=2.9, target_energy=0.8, entry_motion="carry")
    fast_motion = build_clip_motion(fast_slot, CANVAS, direction=direction_vector_for("left"))
    slow_motion = build_clip_motion(slow_slot, CANVAS, direction=direction_vector_for("left"))
    assert fast_motion.native_motion_blur_keyframes[0].shutter_angle > slow_motion.native_motion_blur_keyframes[0].shutter_angle


def test_direction_vector_for_rejects_a_relation_label_not_a_direction():
    # "carry"/"reverse"/"reset" are relation labels, not screen directions —
    # feeding one in here must fail loudly, not silently resolve to (0, 0).
    with pytest.raises(KeyError):
        direction_vector_for("carry")


@pytest.mark.parametrize("relation", ["carry", "reverse", "reset"])
def test_transition_pair_links_outgoing_and_incoming_with_a_shared_safe_scale(relation):
    pair = build_transition_pair(
        pair_id="t0", cut_sec=1.5, outgoing_clip_id="c0", incoming_clip_id="c1",
        relation=relation, direction="left", canvas=CANVAS, confidence=0.7,
    )
    assert pair.outgoing_clip_id == "c0"
    assert pair.incoming_clip_id == "c1"
    assert pair.safe_scale >= 1.0
    assert pair.outgoing_keyframes[-1].scale == pytest.approx(pair.safe_scale)
    if relation == "carry":
        assert pair.overshoot > 0
    else:
        assert pair.overshoot == 0


def test_carry_transition_uses_the_measured_direction_not_a_flat_zero():
    # This is the bug the codex review caught: relation labels used to be
    # looked up directly as directions and always resolved to (0, 0),
    # collapsing every transition into a centered zoom regardless of
    # direction. A real direction must actually move the geometry.
    pair = build_transition_pair(
        pair_id="t0", cut_sec=1.5, outgoing_clip_id="c0", incoming_clip_id="c1",
        relation="carry", direction="left", canvas=CANVAS, confidence=0.7,
    )
    assert pair.direction == "left"
    assert pair.outgoing_keyframes[-1].center_x != pytest.approx(0.5)


def test_reset_transition_has_no_direction():
    pair = build_transition_pair(
        pair_id="t0", cut_sec=1.0, outgoing_clip_id="c0", incoming_clip_id="c1",
        relation="reset", direction="left", canvas=CANVAS, confidence=0.5,
    )
    assert pair.direction == "none"
    assert pair.outgoing_keyframes[-1].center_x == pytest.approx(0.5)


def test_usable_profile_adds_an_attack_keyframe_at_its_measured_position():
    profile = TransitionProfile(
        anticipation_sec=0.5, release_sec=0.5, translation_unit=0.2,
        overshoot=0.05, confidence=0.9, attack_fraction=0.8,
    )
    pair = build_transition_pair(
        pair_id="t0", cut_sec=2.0, outgoing_clip_id="c0", incoming_clip_id="c1",
        relation="carry", direction="left", canvas=CANVAS, confidence=0.7, profile=profile,
    )
    # Fixed constants (2 keyframes/side) used to be the only option; a
    # usable profile inserts a measured attack point on each side.
    assert len(pair.outgoing_keyframes) == 3
    assert len(pair.incoming_keyframes) == 3
    expected_attack_sec = (2.0 - profile.anticipation_sec) + profile.attack_fraction * profile.anticipation_sec
    assert pair.outgoing_keyframes[1].sec == pytest.approx(expected_attack_sec)


def test_usable_profile_with_flash_effect_sets_the_transition_pairs_effect_kind():
    profile = TransitionProfile(
        anticipation_sec=0.3, release_sec=0.3, translation_unit=0.1,
        overshoot=0.02, confidence=0.9, attack_fraction=0.5, effect_kind="flash",
    )
    pair = build_transition_pair(
        pair_id="t0", cut_sec=2.0, outgoing_clip_id="c0", incoming_clip_id="c1",
        relation="carry", direction="left", canvas=CANVAS, confidence=0.7, profile=profile,
    )
    assert pair.effect_kind == "flash"


def test_no_profile_means_no_effect_kind():
    pair = build_transition_pair(
        pair_id="t0", cut_sec=2.0, outgoing_clip_id="c0", incoming_clip_id="c1",
        relation="carry", direction="left", canvas=CANVAS, confidence=0.7,
    )
    assert pair.effect_kind == "none"


def test_transition_windows_are_capped_by_short_clip_durations():
    # Regression: a fast-cut sequence (Demo shots averaging well under a
    # second) used to spend almost the whole clip inside the anticipation/
    # release ramp on both sides of every cut — anticipation_sec/
    # release_sec must shrink to fit a short clip's own duration rather
    # than always running at the profile/default's full length.
    profile = TransitionProfile(
        anticipation_sec=0.5, release_sec=0.5, translation_unit=0.2,
        overshoot=0.05, confidence=0.9, attack_fraction=0.5,
    )
    pair = build_transition_pair(
        pair_id="t0", cut_sec=2.0, outgoing_clip_id="c0", incoming_clip_id="c1",
        relation="carry", direction="left", canvas=CANVAS, confidence=0.7, profile=profile,
        outgoing_duration_sec=0.2, incoming_duration_sec=0.2,
    )
    anticipation_sec = 2.0 - pair.outgoing_keyframes[0].sec
    release_sec = pair.incoming_keyframes[-1].sec - 2.0
    assert anticipation_sec <= 0.2 * 0.35 + 1e-6
    assert release_sec <= 0.2 * 0.35 + 1e-6


def test_unusable_profile_falls_back_to_a_flat_hard_cut():
    low_confidence = TransitionProfile(
        anticipation_sec=0.5, release_sec=0.5, translation_unit=0.2,
        overshoot=0.05, confidence=0.1, attack_fraction=0.5,
    )
    pair = build_transition_pair(
        pair_id="t0", cut_sec=2.0, outgoing_clip_id="c0", incoming_clip_id="c1",
        relation="carry", direction="left", canvas=CANVAS, confidence=0.7, profile=low_confidence,
    )
    assert pair.direction == "none"
    assert pair.overshoot == 0
    assert pair.outgoing_keyframes[-1].center_x == pytest.approx(0.5)
