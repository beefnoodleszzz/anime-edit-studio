from pathlib import Path

import cv2
import numpy as np

from studio.creative.reference import EditingStyleProfile
from studio.critic.creative import evaluate_motion


def _video(path: Path) -> None:
    writer = cv2.VideoWriter(
        str(path),
        cv2.VideoWriter_fourcc(*"mp4v"),
        24,
        (320, 180),
    )
    base = np.random.default_rng(7).integers(
        0, 90, size=(180, 320, 3), dtype=np.uint8
    )
    cv2.rectangle(base, (90, 50), (210, 140), (255, 160, 40), -1)
    for frame in range(96):
        if frame < 24:
            offset = 0
        elif frame < 48:
            offset = round(((frame - 24) / 24) ** 3 * 45)
        elif frame < 72:
            offset = round(45 - ((frame - 48) / 24) * 45)
        else:
            offset = 0
        writer.write(np.roll(base, offset, axis=1))
    writer.release()


def test_motion_qa_detects_holds_dynamics_and_direction_changes(tmp_path):
    video = tmp_path / "motion.mp4"
    _video(video)
    profile = EditingStyleProfile(
        motion_median_target=0.3,
        motion_p75_target=1.0,
        motion_dynamic_range_target=1.3,
        hold_ratio_target=0.4,
        direction_balance_target=0.4,
        direction_reversal_target=0.2,
    )
    result = evaluate_motion(video, profile, cut_times=[2.0])
    assert result.sample_count > 8
    assert result.dynamic_range >= 1
    assert result.hold_ratio > 0.2
    assert result.direction_reversal_rate > 0
