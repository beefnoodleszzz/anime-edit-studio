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


def test_schema_version_is_15():
    assert SCHEMA_VERSION == 15


def test_amv_project_and_run_round_trip(tmp_path):
    conn = connect(tmp_path / "engine.v2.sqlite")
    with conn:
        conn.execute(
            "INSERT INTO amv_projects(id,demo_path,materials_dir,output_dir) VALUES (?,?,?,?)",
            ("proj-1", "/tmp/demo.mp4", "/tmp/materials", "projects/proj-1"),
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


def test_amv_run_rejects_unknown_stage(tmp_path):
    conn = connect(tmp_path / "engine.v2.sqlite")
    conn.execute(
        "INSERT INTO amv_projects(id,demo_path,materials_dir,output_dir) VALUES (?,?,?,?)",
        ("proj-1", "/tmp/demo.mp4", "/tmp/materials", "projects/proj-1"),
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
