from __future__ import annotations

import cv2
import numpy as np

from studio.core.database import connect
from studio.planning.candidates import _trim_to_duration, candidates_for_slot
from studio.planning.slots import TimelineSlot

_SIZE = (160, 120)


def _write_video(path, *, duration_sec, fps=12.0):
    rng = np.random.default_rng(1)
    frame_count = int(duration_sec * fps) + 2
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, _SIZE)
    for i in range(frame_count):
        frame = np.clip(rng.normal(120, 15, (_SIZE[1], _SIZE[0], 3)), 0, 255).astype(np.uint8)
        cv2.circle(frame, (20 + (i * 4) % 100, 60), 15, (0, 200, 0), -1)
        writer.write(frame)
    writer.release()


def test_trim_to_duration_center_crops_a_longer_shot():
    start, end = _trim_to_duration(0.0, 10.0, 2.0)
    assert end - start == 2.0
    assert start == 4.0


def test_trim_to_duration_rejects_a_shot_shorter_than_the_target():
    # A shot shorter than the slot cannot back a full-duration placement —
    # the renderer trusts timeline_duration_sec, not the source's own
    # (shorter) [in,out], so it would silently read past the shot's end.
    assert _trim_to_duration(0.0, 1.0, 2.0) is None


def test_candidates_for_slot_excludes_shots_shorter_than_the_slot(tmp_path):
    conn = connect(tmp_path / "engine.v2.sqlite")
    video = tmp_path / "a0.mp4"
    _write_video(video, duration_sec=6.0)
    conn.execute(
        "INSERT INTO assets(id,path,sha256,width,height,fps_num,fps_den,duration_sec) "
        "VALUES ('a0',?,'sha0',160,120,12,1,6.0)",
        (str(video),),
    )
    conn.execute(
        "INSERT INTO shots(id,asset_id,start_sec,end_sec,character,series) "
        "VALUES ('good','a0',0.0,2.0,'hero','showA')"
    )
    conn.execute(
        "INSERT INTO shots(id,asset_id,start_sec,end_sec,character,series) "
        "VALUES ('short','a0',3.0,3.2,'hero','showA')"
    )
    conn.commit()

    slot = TimelineSlot(index=0, start_sec=0.0, duration_sec=2.0, target_energy=0.5)
    scored = candidates_for_slot(conn, slot, ["good", "short"])

    assert [item.window.shot_id for item in scored] == ["good"]
    assert scored[0].components["duration_fit"] == 1.0
    assert scored[0].window.subject.identity_cluster == "hero"
    assert scored[0].window.subject.series_scope == "showA"
    conn.close()
