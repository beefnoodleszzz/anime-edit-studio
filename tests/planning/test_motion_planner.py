from __future__ import annotations

import pytest

from studio.planning.motion_planner import (
    build_clip_motion,
    build_transition_pair,
    direction_vector_for,
    required_safe_scale,
)
from studio.planning.slots import TimelineSlot
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
    motion = build_clip_motion(slot, CANVAS, direction=direction_vector_for("carry"))
    end_scale = motion.transform_keyframes[-1].scale
    assert end_scale > 1.0
    assert motion.native_motion_blur_keyframes


@pytest.mark.parametrize("entry_motion", ["carry", "reverse", "reset"])
def test_transition_pair_links_outgoing_and_incoming_with_a_shared_safe_scale(entry_motion):
    pair = build_transition_pair(
        pair_id="t0", cut_sec=1.5, outgoing_clip_id="c0", incoming_clip_id="c1",
        entry_motion=entry_motion, canvas=CANVAS, confidence=0.7,
    )
    assert pair.outgoing_clip_id == "c0"
    assert pair.incoming_clip_id == "c1"
    assert pair.safe_scale >= 1.0
    assert pair.outgoing_keyframes[-1].scale == pytest.approx(pair.safe_scale)
    if entry_motion == "carry":
        assert pair.overshoot > 0
    else:
        assert pair.overshoot == 0


def test_reset_transition_has_no_direction():
    pair = build_transition_pair(
        pair_id="t0", cut_sec=1.0, outgoing_clip_id="c0", incoming_clip_id="c1",
        entry_motion="reset", canvas=CANVAS, confidence=0.5,
    )
    assert pair.direction == "none"
