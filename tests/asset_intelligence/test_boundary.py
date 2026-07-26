from pathlib import Path

import cv2
import numpy as np

from studio.asset_intelligence.visual.boundary import analyze_cutability
from studio.core.database import connect


def test_cutability_uses_adjacent_boundary_frames(tmp_path: Path):
    frames = tmp_path / "frames"
    frames.mkdir()
    keyframes = []
    for shot, level in enumerate((20, 230)):
        for candidate in range(5):
            path = frames / f"shot_{shot:04d}_c{candidate}.jpg"
            cv2.imwrite(str(path), np.full((30, 40, 3), level, np.uint8))
            if candidate == 2:
                keyframes.append(path)
    conn = connect(tmp_path / "v2.sqlite")
    with conn:
        conn.execute(
            "INSERT INTO assets(id,path,sha256,fps_num,fps_den,duration_sec) "
            "VALUES ('a',?,'hash',24,1,2)",
            (str(keyframes[0]),),
        )
        conn.executemany(
            "INSERT INTO shots(id,asset_id,idx,start_sec,end_sec,keyframe) "
            "VALUES (?,?,?,?,?,?)",
            [
                ("s0", "a", 0, 0, 1, str(keyframes[0])),
                ("s1", "a", 1, 1, 2, str(keyframes[1])),
            ],
        )
    report = analyze_cutability(conn, cache_root=tmp_path / "cache")
    assert report["analyzed"] == 2
    first = conn.execute(
        "SELECT cutability,cutability_confidence FROM shots WHERE id='s0'"
    ).fetchone()
    assert first["cutability"] > 0.5
    assert first["cutability_confidence"] == 0.82
