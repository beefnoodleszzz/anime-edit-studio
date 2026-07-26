import json

from studio.asset_intelligence.indexing import SearchQuery, search_shots
from studio.core.database import connect


def seed(conn):
    conn.execute(
        """
        INSERT INTO assets(id,path,sha256,fps_num,fps_den,duration_sec)
        VALUES ('a','/a','hash',24,1,10)
        """
    )
    for idx, values in enumerate(
        [
            ("hero", "running", "right", 8.0, 0.9, 0.8, False),
            ("villain", "standing", "static", 0.1, 0.4, 0.3, True),
        ]
    ):
        character, action, direction, motion, face, energy, subtitle = values
        conn.execute(
            """
            INSERT INTO shots(
              id,asset_id,idx,start_sec,end_sec,character,action,tags,
              motion_dir,motion_mag,face_visibility,visual_energy,subtitle_region
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                f"s{idx}", "a", idx, idx, idx + 1, character, action,
                f"{character},{action}", direction, motion, face, energy,
                json.dumps({"present": subtitle}),
            ),
        )
        conn.execute(
            "INSERT INTO shots_fts(rowid,shot_id,character,action,tags) "
            "SELECT rowid,id,character,action,tags FROM shots WHERE id=?",
            (f"s{idx}",),
        )
    conn.commit()


def test_combined_filters(tmp_path):
    conn = connect(tmp_path / "db.sqlite")
    seed(conn)
    rows = search_shots(
        conn,
        SearchQuery(
            character="hero",
            action="run",
            motion_direction="right",
            min_motion=1,
            subtitle=False,
            min_face_visibility=0.7,
            min_visual_energy=0.7,
        ),
    )
    assert [row["id"] for row in rows] == ["s0"]


def test_fts_and_limit(tmp_path):
    conn = connect(tmp_path / "db.sqlite")
    seed(conn)
    rows = search_shots(conn, SearchQuery(text="villain", limit=1))
    assert [row["id"] for row in rows] == ["s1"]
