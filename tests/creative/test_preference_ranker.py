import json

from studio.core.database import connect
from studio.creative.preference import preference_signal, train_pairwise


def test_pairwise_ranker_learns_without_becoming_a_hard_rule(tmp_path):
    conn = connect(tmp_path / "v2.sqlite")
    with conn:
        conn.execute(
            "INSERT INTO assets(id,path,sha256,fps_num,fps_den,duration_sec) "
            "VALUES ('a','x','hash',24,1,20)"
        )
        for index, quality in enumerate((0.1, 0.3, 0.6, 0.8, 0.95, 0.99)):
            conn.execute(
                """
                INSERT INTO shots(
                  id,asset_id,idx,start_sec,end_sec,image_quality,pose_quality,
                  face_visibility,visual_energy,shot_scale,subject_motion,
                  cutability,subtitle_region,aesthetic
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    f"s{index}", "a", index, index, index + 1, quality,
                    quality, quality, quality, 0.5, quality, quality,
                    json.dumps({"present": False}), quality * 10,
                ),
            )
        conn.executemany(
            "INSERT INTO preference_pairs(winner_shot_id,loser_shot_id,project_id) "
            "VALUES (?,?,?)",
            [(f"s{i}", "s0", "p") for i in range(1, 6)],
        )
    model = train_pairwise(conn, project_id="p", scope="project:p")
    assert model.fitted
    assert model.trained_on == 5
    low = conn.execute("SELECT * FROM shots WHERE id='s0'").fetchone()
    high = conn.execute("SELECT * FROM shots WHERE id='s5'").fetchone()
    assert preference_signal(model, high) > preference_signal(model, low)


def test_preference_model_is_explicitly_unfitted_without_data(tmp_path):
    conn = connect(tmp_path / "v2.sqlite")
    model = train_pairwise(conn)
    assert not model.fitted
    assert model.trained_on == 0
