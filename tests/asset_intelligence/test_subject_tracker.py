import cv2
import numpy as np
import pytest

from studio.asset_intelligence.character.tracker import SubjectTracker


def test_tracker_follows_moving_subject_and_bounds_reframe(tmp_path):
    path = tmp_path / "moving.mp4"
    writer = cv2.VideoWriter(
        str(path), cv2.VideoWriter_fourcc(*"mp4v"), 12, (320, 180)
    )
    for index in range(24):
        frame = np.zeros((180, 320, 3), dtype=np.uint8)
        x = 20 + index * 8
        cv2.rectangle(frame, (x, 35), (x + 55, 165), (40, 220, 250), -1)
        writer.write(frame)
    writer.release()
    points = SubjectTracker(sample_fps=6).track(
        path, start_sec=0, end_sec=2, target_aspect=4 / 5
    )
    assert len(points) >= 10
    assert all(-1 <= point.pan_x <= 1 for point in points)
    assert all(-1 <= point.pan_y <= 1 for point in points)
    assert all(point.zoom >= 1 for point in points)
    first_center = points[0].box.x + points[0].box.width / 2
    last_center = points[-1].box.x + points[-1].box.width / 2
    assert last_center > first_center


def test_tracker_defaults_to_square_delivery(tmp_path):
    path = tmp_path / "center.mp4"
    writer = cv2.VideoWriter(
        str(path), cv2.VideoWriter_fourcc(*"mp4v"), 12, (320, 180)
    )
    for _ in range(12):
        frame = np.zeros((180, 320, 3), dtype=np.uint8)
        cv2.rectangle(frame, (130, 25), (190, 165), (40, 220, 250), -1)
        writer.write(frame)
    writer.release()
    points = SubjectTracker(sample_fps=4).track(path, start_sec=0, end_sec=1)
    assert points
    # 16:9 source cover-cropped to 1:1.
    assert all(point.zoom == pytest.approx(16 / 9, rel=0.02) for point in points)
