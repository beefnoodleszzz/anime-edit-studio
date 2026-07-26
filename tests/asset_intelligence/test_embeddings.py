from pathlib import Path

import cv2
import numpy as np

from studio.asset_intelligence.embeddings.encoder import encode_pending
from studio.core.database import connect


class FakeBackend:
    def aesthetic_scores(self, paths):
        return np.arange(len(paths), dtype=np.float32)

    def image_embeddings(self, paths):
        result = np.ones((len(paths), 512), dtype=np.float32)
        return result / np.linalg.norm(result, axis=1, keepdims=True)


def test_encode_pending_selects_best_candidate_and_caches(tmp_path: Path):
    database = tmp_path / "v2.sqlite"
    conn = connect(database)
    root = tmp_path / "frames"
    root.mkdir()
    paths = []
    for index in range(3):
        path = root / f"shot_0000_c{index}.jpg"
        cv2.imwrite(str(path), np.full((24, 32, 3), index * 60, np.uint8))
        paths.append(path)
    with conn:
        conn.execute(
            "INSERT INTO assets(id,path,sha256,fps_num,fps_den,duration_sec) "
            "VALUES ('a',?,'hash',24000,1001,1)",
            (str(paths[0]),),
        )
        conn.execute(
            "INSERT INTO shots(id,asset_id,idx,start_sec,end_sec,keyframe) "
            "VALUES ('s','a',0,0,1,?)",
            (str(paths[0]),),
        )

    assert encode_pending(conn, cache_root=tmp_path / "cache", backend=FakeBackend()) == 1
    row = conn.execute(
        "SELECT keyframe,aesthetic,embedding FROM shots WHERE id='s'"
    ).fetchone()
    assert row["keyframe"] == str(paths[-1])
    assert row["aesthetic"] == 2
    vector = np.frombuffer(row["embedding"], dtype=np.float32)
    assert vector.shape == (512,)
    assert np.isclose(np.linalg.norm(vector), 1)
    assert encode_pending(conn, cache_root=tmp_path / "cache", backend=FakeBackend()) == 0
