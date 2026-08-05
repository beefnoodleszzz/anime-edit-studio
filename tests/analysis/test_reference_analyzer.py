from __future__ import annotations

import cv2
import numpy as np

from studio.analysis.reference_analyzer import _classify_effect, _classify_relation, analyze_reference
from studio.spec.reference_blueprint import Estimate


def _panning_scene(rng, width=320, height=240, n=48, direction=1):
    base = rng.integers(0, 200, size=(height + 40, width + 80, 3), dtype=np.uint8)
    cv2.rectangle(base, (60, 60), (200, 180), (10, 200, 10), -1)
    frames = []
    for i in range(n):
        offset = int(direction * i * 1.2)
        frame = np.roll(base, offset, axis=1)[20:20 + height, 40:40 + width]
        frames.append(frame.copy())
    return frames


def _write(path, frames, fps=24):
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (320, 240))
    for frame in frames:
        writer.write(frame)
    writer.release()


def test_reference_blueprint_from_two_panning_shots_with_a_hard_cut(tmp_path):
    rng = np.random.default_rng(5)
    shot_a = _panning_scene(rng, n=48, direction=1)
    shot_b = _panning_scene(np.random.default_rng(9), n=48, direction=1)
    # Make the second shot visually distinct so the cut is detectable.
    for frame in shot_b:
        cv2.circle(frame, (250, 60), 30, (200, 30, 30), -1)

    video = tmp_path / "demo.mp4"
    _write(video, shot_a + shot_b)

    blueprint = analyze_reference(video)

    assert blueprint.technical.width == 320
    assert blueprint.technical.height == 240
    assert len(blueprint.shots) >= 1
    assert len(blueprint.motion_curve) > 10
    for cut in blueprint.cuts:
        assert cut.outgoing_motion is not None
        assert cut.incoming_motion is not None
        assert 0.0 <= cut.sec <= blueprint.technical.duration_sec


def test_transition_pairs_carry_nonempty_motion_envelopes(tmp_path):
    rng = np.random.default_rng(5)
    shot_a = _panning_scene(rng, n=48, direction=1)
    shot_b = _panning_scene(np.random.default_rng(9), n=48, direction=1)
    for frame in shot_b:
        cv2.circle(frame, (250, 60), 30, (200, 30, 30), -1)

    video = tmp_path / "demo.mp4"
    _write(video, shot_a + shot_b)

    blueprint = analyze_reference(video)

    assert blueprint.transition_pairs
    for pair in blueprint.transition_pairs:
        # These used to be declared on the schema but never populated by the
        # analyzer, so a downstream consumer (motion_planner) had nothing
        # real to shape a transition curve from.
        assert pair.outgoing_envelope
        assert pair.incoming_envelope
        assert pair.blur_envelope


def test_classify_effect_flags_a_sharp_sustained_sharpness_collapse():
    assert _classify_effect([0.1, 0.2, 0.6, 0.3]) == "flash"
    assert _classify_effect([0.1, 0.2, 0.3]) == "none"
    assert _classify_effect([]) == "none"


def test_classify_relation_uses_direction_not_just_speed_ratio():
    """Regression for the bug the codex review caught: the old
    ``_classify_relation`` only compared magnitudes, so two windows moving
    at the same speed in *opposite* directions (ratio == 1.0) were called
    "carry" — direction must actually be considered."""
    outgoing = Estimate(value=5.0, confidence=0.9)
    incoming = Estimate(value=5.0, confidence=0.9)
    assert _classify_relation(outgoing, incoming, (5.0, 0.0), (5.0, 0.0)) == "carry"
    assert _classify_relation(outgoing, incoming, (5.0, 0.0), (-5.0, 0.0)) == "reverse"
    # No reliable vector on either side (e.g. rotation/scale-dominated
    # motion): fall back to the old ratio heuristic rather than asserting a
    # direction relationship with nothing behind it.
    assert _classify_relation(outgoing, incoming, (0.0, 0.0), (0.0, 0.0)) == "carry"


def test_reference_blueprint_round_trips_through_json(tmp_path):
    rng = np.random.default_rng(11)
    frames = _panning_scene(rng, n=36)
    video = tmp_path / "single_shot.mp4"
    _write(video, frames)

    blueprint = analyze_reference(video)
    from studio.spec.reference_blueprint import ReferenceBlueprint

    restored = ReferenceBlueprint.model_validate_json(blueprint.model_dump_json())
    assert restored == blueprint
