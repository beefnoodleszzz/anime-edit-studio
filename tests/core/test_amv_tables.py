"""New Demo-driven AMV chain tables (migration 15) are additive to the v2 db."""
from __future__ import annotations

import json
import sqlite3

from studio.core.database import SCHEMA_VERSION, connect


def test_migration_creates_amv_tables(tmp_path):
    conn = connect(tmp_path / "engine.v2.sqlite")
    tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    for name in ("reference_blueprints", "music_timelines", "amv_projects", "amv_runs"):
        assert name in tables
    conn.close()


def test_schema_version_is_19():
    assert SCHEMA_VERSION == 19


def test_amv_project_and_run_round_trip(tmp_path):
    conn = connect(tmp_path / "engine.v2.sqlite")
    with conn:
        conn.execute(
            "INSERT INTO amv_projects(id,demo_path,shot_ids_json,output_dir) VALUES (?,?,?,?)",
            ("proj-1", "/tmp/demo.mp4", '["s1","s2"]', "projects/proj-1"),
        )
        conn.execute(
            "INSERT INTO amv_runs(project_id,stage,status,details_json) VALUES (?,?,?,?)",
            ("proj-1", "analyze_reference", "complete", json.dumps({"shots": 12})),
        )
    row = conn.execute(
        "SELECT stage,status FROM amv_runs WHERE project_id=?", ("proj-1",)
    ).fetchone()
    assert row["stage"] == "analyze_reference"
    assert row["status"] == "complete"
    conn.close()


def test_migration_18_drops_the_cv_selection_stage_tables(tmp_path):
    """Footage now arrives pre-selected by ID from anime-shot-library, so
    the old candidate-generation caches (ShotWindow scoring, embeddings,
    tracking) have nothing left to write into."""
    conn = connect(tmp_path / "engine.v2.sqlite")
    tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    for name in (
        "review_decisions", "shot_windows", "shot_window_embeddings", "shot_window_tracks",
        "shot_tracks", "shot_scores", "candidate_scores", "shot_temporal_quality",
        "subject_layers", "characters", "source_records", "shots_fts", "music_tracks",
    ):
        assert name not in tables
    conn.close()


def test_shots_table_has_the_lean_pre_selected_shot_shape(tmp_path):
    conn = connect(tmp_path / "engine.v2.sqlite")
    columns = {row[1] for row in conn.execute("PRAGMA table_info(shots)")}
    assert columns == {
        "id", "asset_id", "source_shot_id", "start_sec", "end_sec",
        "character", "series", "tags_json", "created_at",
    }
    conn.close()


def test_amv_run_rejects_unknown_stage(tmp_path):
    conn = connect(tmp_path / "engine.v2.sqlite")
    conn.execute(
        "INSERT INTO amv_projects(id,demo_path,shot_ids_json,output_dir) VALUES (?,?,?,?)",
        ("proj-1", "/tmp/demo.mp4", '["s1"]', "projects/proj-1"),
    )
    try:
        conn.execute(
            "INSERT INTO amv_runs(project_id,stage,status) VALUES (?,?,?)",
            ("proj-1", "not_a_real_stage", "complete"),
        )
        raised = False
    except sqlite3.IntegrityError:
        raised = True
    assert raised
    conn.close()
