import cv2
import numpy as np

from studio.asset_intelligence.visual.temporal_quality import (
    analyze_temporal_quality,
    gate_candidates,
)
from studio.core.database import connect


def _video(path, *, off_center=False, bad_tail=False):
    writer = cv2.VideoWriter(
        str(path), cv2.VideoWriter_fourcc(*"mp4v"), 10, (320, 180)
    )
    for index in range(20):
        frame = np.full((180, 320, 3), 24, dtype=np.uint8)
        if bad_tail and index >= 12:
            frame[:] = 0
        else:
            x = 5 if off_center else 125
            cv2.rectangle(frame, (x, 20), (x + 70, 165), (230, 180, 40), -1)
            for y in range(25, 160, 12):
                cv2.line(frame, (x, y), (x + 70, y), (20, 20, 20), 2)
        writer.write(frame)
    writer.release()


def test_temporal_quality_rejects_bad_window_and_square_crop(tmp_path):
    good = tmp_path / "good.mp4"
    tail = tmp_path / "tail.mp4"
    side = tmp_path / "side.mp4"
    _video(good)
    _video(tail, bad_tail=True)
    _video(side, off_center=True)
    good_result = analyze_temporal_quality(
        good, shot_id="good", start_sec=0, end_sec=2
    )
    tail_result = analyze_temporal_quality(
        tail, shot_id="tail", start_sec=0, end_sec=2
    )
    side_result = analyze_temporal_quality(
        side, shot_id="side", start_sec=0, end_sec=2
    )
    assert good_result.accepted
    assert tail_result.bad_frame_ratio > good_result.bad_frame_ratio
    assert not tail_result.accepted
    assert side_result.crop_fitness < good_result.crop_fitness
    assert not side_result.accepted


def test_candidate_gate_is_persisted_and_reused(tmp_path):
    video = tmp_path / "good.mp4"
    _video(video)
    conn = connect(tmp_path / "v2.sqlite")
    with conn:
        conn.execute(
            "INSERT INTO assets(id,path,sha256) VALUES ('a',?,'hash')",
            (str(video),),
        )
        conn.execute(
            "INSERT INTO shots(id,asset_id,idx,start_sec,end_sec) "
            "VALUES ('s','a',0,0,2)"
        )
    accepted, first = gate_candidates(conn, ["s"])
    accepted_again, second = gate_candidates(conn, ["s"])
    assert accepted == accepted_again == ["s"]
    assert first == second
    assert conn.execute(
        "SELECT count(*) FROM shot_temporal_quality"
    ).fetchone()[0] == 1
