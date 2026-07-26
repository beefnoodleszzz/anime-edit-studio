import importlib.util
import json

import pytest

from studio.core.database import connect
from studio.review import create_review_app


@pytest.mark.skipif(importlib.util.find_spec("fastapi") is None, reason="review extra")
def test_review_routes_list_and_select(tmp_path):
    from fastapi.testclient import TestClient

    database = tmp_path / "v2.sqlite"
    conn = connect(database)
    with conn:
        conn.execute(
            "INSERT INTO assets(id,path,sha256,fps_num,fps_den,duration_sec) "
            "VALUES ('a','x','hash',24,1,3)"
        )
        conn.executemany(
            "INSERT INTO shots(id,asset_id,idx,start_sec,end_sec) VALUES (?,?,?,?,?)",
            [(f"s{i}", "a", i, i, i + 1) for i in range(3)],
        )
        conn.execute(
            "INSERT INTO candidate_groups(id,project_id,role,shot_ids_json) "
            "VALUES ('g','p','impact','[\"s0\",\"s1\",\"s2\"]')"
        )
    conn.close()
    client = TestClient(create_review_app(database=database))
    listed = client.get("/projects/p/candidate-groups")
    assert listed.status_code == 200
    assert listed.json()["groups"][0]["shot_ids"] == ["s0", "s1", "s2"]
    selected = client.post(
        "/candidate-groups/g/selection",
        json={"shot_id": "s1", "context": {"role": "impact"}},
    )
    assert selected.status_code == 200
    assert selected.json()["selected_shot_id"] == "s1"
    assert client.get("/projects/p/candidate-metrics").json()["candidate_precision"] == 1


@pytest.mark.skipif(importlib.util.find_spec("fastapi") is None, reason="review extra")
def test_delivery_never_claims_pass_without_complete_master_and_13_checks(tmp_path):
    from fastapi.testclient import TestClient

    database = tmp_path / "v2.sqlite"
    conn = connect(database)
    with conn:
        conn.execute(
            """
            INSERT INTO renders(
              id,project_id,spec_version,backend,preset,status,output_path
            ) VALUES ('r','p',1,'resolve','H.265 Master','complete','master.mp4')
            """
        )
        conn.execute(
            """
            INSERT INTO qa_results(render_id,kind,passed,checks_json)
            VALUES ('r','technical',1,?)
            """,
            ('[{"name":"only-one","passed":true}]',),
        )
    conn.close()
    client = TestClient(create_review_app(database=database))
    incomplete = client.get("/projects/p/delivery").json()
    assert incomplete["passed"] is False
    assert incomplete["output_path"] is None

    conn = connect(database)
    checks = json.dumps(
        [{"name": f"check-{index}", "passed": True} for index in range(13)]
    )
    with conn:
        conn.execute(
            "INSERT INTO qa_results(render_id,kind,passed,checks_json) "
            "VALUES ('r','technical',1,?)",
            (checks,),
        )
    conn.close()
    passed = client.get("/projects/p/delivery").json()
    assert passed["passed"] is True
    assert passed["output_path"] == "master.mp4"


@pytest.mark.skipif(importlib.util.find_spec("fastapi") is None, reason="review extra")
def test_project_creation_and_music_upload_are_real(tmp_path):
    from fastapi.testclient import TestClient

    database = tmp_path / "v2.sqlite"
    projects = tmp_path / "projects"
    client = TestClient(
        create_review_app(database=database, projects_root=projects)
    )
    created = client.post(
        "/projects",
        json={
            "title": "Test",
            "intent": "A focused character edit",
            "duration_sec": 25,
            "platform": "douyin",
            "primary_characters": ["hero"],
            "tone": ["intense"],
        },
    )
    assert created.status_code == 200
    project_id = created.json()["project_id"]
    state = client.get(f"/projects/{project_id}/state")
    assert state.status_code == 200
    assert state.json()["state"] == "CREATED"
    uploaded = client.post(
        f"/projects/{project_id}/uploads/music",
        files={"file": ("music.wav", b"RIFF-not-a-wave", "audio/wav")},
    )
    assert uploaded.status_code == 200
    assert (
        projects / project_id / "uploads" / "music.wav"
    ).read_bytes() == b"RIFF-not-a-wave"
    failed = client.post(f"/projects/{project_id}/prepare")
    assert failed.status_code == 422
    failure_state = client.get(f"/projects/{project_id}/state").json()
    assert failure_state["state"] == "FAILED_CREATED"
    assert failure_state["payload"]["step"] == "prepare"
    assert failure_state["payload"]["error_type"]


@pytest.mark.skipif(importlib.util.find_spec("fastapi") is None, reason="review extra")
def test_ai_selection_uses_persisted_ranking_not_fixed_b(tmp_path):
    from fastapi.testclient import TestClient

    database = tmp_path / "v2.sqlite"
    conn = connect(database)
    with conn:
        conn.execute(
            "INSERT INTO assets(id,path,sha256,fps_num,fps_den,duration_sec) "
            "VALUES ('a','x','hash',24,1,3)"
        )
        conn.executemany(
            "INSERT INTO shots(id,asset_id,idx,start_sec,end_sec) VALUES (?,?,?,?,?)",
            [(f"s{i}", "a", i, i, i + 1) for i in range(3)],
        )
        conn.execute(
            "INSERT INTO candidate_groups(id,project_id,role,shot_ids_json) "
            "VALUES ('g','p','impact','[\"s0\",\"s1\",\"s2\"]')"
        )
        conn.executemany(
            """
            INSERT INTO candidate_scores(
              project_id,role,shot_id,version,components_json,contextual,total
            ) VALUES ('p','impact',?,'v','{}',?,?)
            """,
            [("s0", 0.6, 0.6), ("s1", 0.7, 0.7), ("s2", 0.9, 0.9)],
        )
    conn.close()
    client = TestClient(create_review_app(database=database, projects_root=tmp_path))
    response = client.post("/candidate-groups/g/ai-selection")
    assert response.status_code == 200
    assert response.json()["selected_shot_id"] == "s2"
