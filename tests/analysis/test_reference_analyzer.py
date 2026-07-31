from __future__ import annotations

import cv2
import numpy as np

from studio.analysis.reference_analyzer import analyze_reference


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


def test_reference_blueprint_round_trips_through_json(tmp_path):
    rng = np.random.default_rng(11)
    frames = _panning_scene(rng, n=36)
    video = tmp_path / "single_shot.mp4"
    _write(video, frames)

    blueprint = analyze_reference(video)
    from studio.spec.reference_blueprint import ReferenceBlueprint

    restored = ReferenceBlueprint.model_validate_json(blueprint.model_dump_json())
    assert restored == blueprint
