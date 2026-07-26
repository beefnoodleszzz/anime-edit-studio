from pathlib import Path

import cv2
import numpy as np

from studio.asset_intelligence.motion import analyze_pending_motion
from studio.core.database import connect


def test_motion_analysis_uses_candidate_pair(tmp_path: Path):
    frames = tmp_path / "frames"
    frames.mkdir()
    for index, x in enumerate((4, 8, 12, 16, 20)):
        image = np.zeros((40, 60, 3), np.uint8)
        cv2.rectangle(image, (x, 10), (x + 16, 30), (255, 255, 255), -1)
        cv2.imwrite(str(frames / f"shot_0000_c{index}.jpg"), image)
    keyframe = frames / "shot_0000_c2.jpg"
    conn = connect(tmp_path / "v2.sqlite")
    with conn:
        conn.execute(
            "INSERT INTO assets(id,path,sha256,fps_num,fps_den,duration_sec) "
            "VALUES ('a',?,'hash',24,1,1)",
            (str(keyframe),),
        )
        conn.execute(
            "INSERT INTO shots(id,asset_id,idx,start_sec,end_sec,keyframe) "
            "VALUES ('s','a',0,0,1,?)",
            (str(keyframe),),
        )

    report = analyze_pending_motion(conn, cache_root=tmp_path / "cache")
    assert report["analyzed"] == 1
    row = conn.execute(
        "SELECT brightness,sharpness,motion_dir,motion_mag FROM shots WHERE id='s'"
    ).fetchone()
    assert row["brightness"] > 0
    assert row["sharpness"] > 0
    assert row["motion_mag"] >= 0
    assert row["motion_dir"] in {
        "static", "right", "down-right", "down", "down-left",
        "left", "up-left", "up", "up-right",
    }
