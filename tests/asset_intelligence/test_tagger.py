from pathlib import Path

import cv2
import numpy as np

from studio.asset_intelligence.visual.tagger import (
    TagResult,
    hydrate_legacy_tags,
    tag_pending,
)
from studio.core.database import connect


class FakeTagger:
    def tag(self, paths):
        return [
            TagResult(
                {"gojou_satoru": 0.97},
                {"white_hair": 0.88, "sunglasses": 0.81, "fighting": 0.76},
                {"general": 0.99},
            )
            for _ in paths
        ]


def test_tag_pending_persists_confident_tags_and_fts(tmp_path: Path):
    frame = tmp_path / "frame.jpg"
    cv2.imwrite(str(frame), np.zeros((16, 16, 3), np.uint8))
    conn = connect(tmp_path / "v2.sqlite")
    with conn:
        conn.execute(
            "INSERT INTO assets(id,path,sha256,fps_num,fps_den,duration_sec) "
            "VALUES ('a',?,'hash',24,1,1)",
            (str(frame),),
        )
        conn.execute(
            "INSERT INTO shots(id,asset_id,idx,start_sec,end_sec,keyframe) "
            "VALUES ('s','a',0,0,1,?)",
            (str(frame),),
        )

    assert tag_pending(
        conn, cache_root=tmp_path / "cache", backend=FakeTagger()
    ) == 1
    row = conn.execute(
        "SELECT character,character_confidence,action,action_confidence,tags "
        "FROM shots WHERE id='s'"
    ).fetchone()
    assert row["character"] == "gojou_satoru"
    assert row["character_confidence"] == 0.97
    assert row["action"] == "fighting"
    assert row["action_confidence"] == 0.76
    assert "sunglasses" in row["tags"]
    assert conn.execute(
        "SELECT count(*) FROM shots_fts WHERE shots_fts MATCH 'sunglasses'"
    ).fetchone()[0] == 1


def test_hydrate_legacy_tags_uses_threshold_as_lower_bound(tmp_path: Path):
    conn = connect(tmp_path / "v2.sqlite")
    with conn:
        conn.execute(
            "INSERT INTO assets(id,path,sha256,fps_num,fps_den,duration_sec) "
            "VALUES ('a','x','hash',24,1,1)"
        )
        conn.execute(
            "INSERT INTO shots(id,asset_id,idx,start_sec,end_sec,tags) "
            "VALUES ('s','a',0,0,1,'solo,fighting,angry')"
        )
    assert hydrate_legacy_tags(conn) == 1
    row = conn.execute(
        "SELECT character,action,action_confidence,emotion,emotion_confidence "
        "FROM shots WHERE id='s'"
    ).fetchone()
    assert row["character"] == ""
    assert row["action"] == "fighting"
    assert row["action_confidence"] == 0.35
    assert row["emotion"] == "angry"
