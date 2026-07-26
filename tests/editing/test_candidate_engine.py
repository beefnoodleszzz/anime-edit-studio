import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from studio.core.database import connect
from studio.editing.ranking import CandidateContext, rank_candidates
from studio.editing.retrieval import RetrievalQuery, retrieve
from studio.editing.candidates import (
    choose_candidate,
    create_group,
    precision_metrics,
    generate_review_assets,
)
from studio.editing.candidates import CandidateGroup


def _seed(conn):
    with conn:
        conn.execute(
            "INSERT INTO assets(id,path,sha256,fps_num,fps_den,duration_sec) "
            "VALUES ('a','x','hash',24,1,10)"
        )
        for index in range(3):
            vector = np.zeros(512, np.float32)
            vector[index] = 1
            conn.execute(
                """
                INSERT INTO shots(
                  id,asset_id,idx,start_sec,end_sec,keyframe,
                  character,character_confidence,action,action_confidence,tags,
                  motion_dir,motion_mag,shot_scale,subject_motion,
                  pose_quality,face_visibility,visual_energy,compression_score,
                  subtitle_region,cutability,composition,composition_confidence,
                  image_quality,aesthetic,embedding
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    f"s{index}", "a", index, index, index + 1, "x",
                    "gojou_satoru", 0.95, "fighting", 0.8, "sword,fighting",
                    "right", 0.4 + index, 0.4, 0.4 + index * 0.2,
                    0.7, 0.8, 0.3 + index * 0.3, 0.1,
                    json.dumps({"present": index == 0}), 0.8,
                    "rule_of_thirds", 0.8, 0.7 + index * 0.1, 7.0,
                    vector.tobytes(),
                ),
            )


def test_retrieval_and_contextual_ranking_are_separate(tmp_path):
    conn = connect(tmp_path / "v2.sqlite")
    _seed(conn)
    ids = retrieve(
        conn,
        RetrievalQuery(
            character="gojou", action="fighting", subtitle_allowed=False, limit=100
        ),
    )
    assert ids == ["s2", "s1"]
    ranked = rank_candidates(
        conn,
        ids,
        CandidateContext(
            project_id="p",
            role="impact",
            target_energy=0.9,
            character="gojou",
            action="fighting",
        ),
        limit=2,
    )
    assert ranked[0].shot_id == "s2"
    assert "image_quality" in ranked[0].intrinsic_components
    assert "sequence_fit" in ranked[0].contextual_components
    assert conn.execute("SELECT count(*) FROM shot_scores").fetchone()[0] == 2
    assert conn.execute("SELECT count(*) FROM candidate_scores").fetchone()[0] == 2
    assert retrieve(
        conn, RetrievalQuery(min_duration_sec=1.01, limit=100)
    ) == []


def test_production_retrieval_rejects_text_and_contaminated_tags(tmp_path):
    conn = connect(tmp_path / "v2.sqlite")
    _seed(conn)
    with conn:
        conn.execute(
            "UPDATE shots SET tags='gojou_satoru,english_text,fake_screenshot' "
            "WHERE id='s2'"
        )
    ids = retrieve(
        conn,
        RetrievalQuery(
            character="gojou",
            subtitle_allowed=False,
            min_face=0.7,
            min_pose=0.6,
            required_any_tags=["solo", "sword"],
            excluded_tags=["fake_screenshot", "parody"],
            limit=100,
        ),
    )
    assert ids == ["s1"]


def test_contaminated_candidate_gets_intrinsic_penalty(tmp_path):
    conn = connect(tmp_path / "v2.sqlite")
    _seed(conn)
    with conn:
        conn.execute("UPDATE shots SET tags=tags||',parody' WHERE id='s2'")
    ranked = rank_candidates(
        conn,
        ["s1", "s2"],
        CandidateContext(project_id="p", role="impact", target_energy=0.9),
        limit=2,
    )
    by_id = {item.shot_id: item for item in ranked}
    assert by_id["s2"].intrinsic_components["production_clean"] == 0
    assert by_id["s1"].intrinsic_components["production_clean"] == 1


def test_project_rejections_are_removed_from_retrieval(tmp_path):
    conn = connect(tmp_path / "v2.sqlite")
    _seed(conn)
    with conn:
        conn.execute(
            "INSERT INTO review_decisions(project_id,shot_id,decision,reasons) "
            "VALUES ('p','s2','reject','[\"visual_boundary_miss\"]')"
        )
    assert "s2" not in retrieve(
        conn, RetrievalQuery(project_id="p", limit=100)
    )
    assert "s2" in retrieve(
        conn, RetrievalQuery(project_id="other", limit=100)
    )


def test_abc_selection_writes_pairwise_preferences(tmp_path):
    conn = connect(tmp_path / "v2.sqlite")
    _seed(conn)
    ranked = rank_candidates(
        conn,
        ["s0", "s1", "s2"],
        CandidateContext(project_id="p", role="impact", target_energy=0.8),
        limit=3,
    )
    group = create_group(conn, project_id="p", role="impact", ranked=ranked)
    chosen = choose_candidate(
        conn,
        group_id=group.id,
        shot_id=group.shot_ids[1],
        context={"target_energy": 0.8},
        project_style="high_energy",
    )
    assert chosen.selected_shot_id == group.shot_ids[1]
    assert conn.execute("SELECT count(*) FROM preference_pairs").fetchone()[0] == 2
    assert precision_metrics(conn, "p")["candidate_precision"] == 1.0


def test_replan_exposes_only_current_groups_and_preserves_compatible_choice(tmp_path):
    conn = connect(tmp_path / "v2.sqlite")
    _seed(conn)
    first = rank_candidates(
        conn,
        ["s0", "s1", "s2"],
        CandidateContext(project_id="p", role="impact", target_energy=0.8),
        limit=3,
    )
    old = create_group(
        conn, project_id="p", role="impact", ranked=first, plan_revision=1
    )
    chosen_id = old.shot_ids[1]
    choose_candidate(
        conn, group_id=old.id, shot_id=chosen_id, context={"source": "human"}
    )

    # A changed ranking produces a new generation. The compatible human choice
    # survives, while metrics and review queries see only the active generation.
    second = list(reversed(first))
    current = create_group(
        conn, project_id="p", role="impact", ranked=second, plan_revision=2
    )
    assert current.id != old.id
    assert current.selected_shot_id == chosen_id
    assert conn.execute(
        "SELECT count(*) FROM candidate_groups WHERE project_id='p' AND active=1"
    ).fetchone()[0] == 1
    assert conn.execute(
        "SELECT active FROM candidate_groups WHERE id=?", (old.id,)
    ).fetchone()[0] == 0
    assert precision_metrics(conn, "p") == {
        "project_id": "p",
        "groups": 1,
        "accepted": 1,
        "human_accepted": 1,
        "ai_delegated": 0,
        "selection_completion": 1.0,
        "candidate_precision": 1.0,
    }
    with pytest.raises(ValueError, match="不存在"):
        choose_candidate(
            conn, group_id=old.id, shot_id=old.shot_ids[0], context={}
        )


def test_same_shot_cannot_be_selected_for_two_active_roles(tmp_path):
    conn = connect(tmp_path / "v2.sqlite")
    _seed(conn)
    ranked = rank_candidates(
        conn,
        ["s0", "s1", "s2"],
        CandidateContext(project_id="p", role="opening", target_energy=0.5),
        limit=3,
    )
    opening = create_group(
        conn, project_id="p", role="opening", ranked=ranked
    )
    ending = create_group(
        conn, project_id="p", role="ending", ranked=ranked
    )
    choose_candidate(
        conn, group_id=opening.id, shot_id="s0", context={"source": "human"}
    )
    with pytest.raises(ValueError, match="opening"):
        choose_candidate(
            conn, group_id=ending.id, shot_id="s0", context={"source": "human"}
        )


def test_review_assets_include_three_previews_and_contact_sheet(tmp_path):
    video = tmp_path / "source.mp4"
    writer = cv2.VideoWriter(
        str(video), cv2.VideoWriter_fourcc(*"mp4v"), 10, (64, 48)
    )
    for index in range(30):
        writer.write(np.full((48, 64, 3), index * 8, np.uint8))
    writer.release()
    conn = connect(tmp_path / "v2.sqlite")
    frame_paths = []
    for index in range(3):
        path = tmp_path / f"s{index}.jpg"
        cv2.imwrite(str(path), np.full((48, 64, 3), index * 80, np.uint8))
        frame_paths.append(path)
    with conn:
        conn.execute(
            "INSERT INTO assets(id,path,sha256,fps_num,fps_den,duration_sec) "
            "VALUES ('a',?,'hash',10,1,3)",
            (str(video),),
        )
        conn.executemany(
            "INSERT INTO shots(id,asset_id,idx,start_sec,end_sec,keyframe) "
            "VALUES (?,?,?,?,?,?)",
            [
                (f"s{i}", "a", i, i, i + 0.8, str(frame_paths[i]))
                for i in range(3)
            ],
        )
    group = CandidateGroup(
        id="g", project_id="p", role="impact", shot_ids=["s0", "s1", "s2"]
    )
    manifest = generate_review_assets(conn, group, output_dir=tmp_path / "review")
    assert Path(manifest["contact_sheet"]).is_file()
    assert all(Path(path).is_file() for path in manifest["previews"].values())
