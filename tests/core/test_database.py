from __future__ import annotations

import json
import sqlite3

import pytest

from studio.core.database import SCHEMA_VERSION, connect, migrate_v1


def legacy_db(path):
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE assets(
          id TEXT PRIMARY KEY,path TEXT,sha256 TEXT,width INTEGER,height INTEGER,
          fps REAL,duration REAL,codec TEXT,proxy_path TEXT,created_at TEXT);
        CREATE TABLE shots(
          id TEXT PRIMARY KEY,asset_id TEXT,idx INTEGER,start_sec REAL,end_sec REAL,
          keyframe TEXT,brightness REAL,sharpness REAL,motion_dir TEXT,motion_mag REAL,
          character TEXT,action TEXT,emotion TEXT,camera TEXT,dialogue TEXT,tags TEXT,
          slot TEXT,picked INTEGER,embedding BLOB,reframe_x REAL,min_brightness REAL,
          fill_mode TEXT,aesthetic REAL,growth_score REAL);
        """
    )
    # Remaining preserved tables can be empty but must exist with compatible columns.
    conn.executescript(
        """
        CREATE TABLE review_decisions(id INTEGER,project_id TEXT,shot_id TEXT,decision TEXT,reasons TEXT,rating INTEGER,trim_start_sec REAL,trim_end_sec REAL,preferred_role TEXT,created_at TEXT,updated_at TEXT);
        CREATE TABLE creative_briefs(id INTEGER,project_id TEXT,character_query TEXT,theme TEXT,target_emotions TEXT,duration_sec REAL,aspect_ratio TEXT,target_platform TEXT,structure_json TEXT,reference_video_path TEXT,created_at TEXT,updated_at TEXT,creative_contract_json TEXT);
        CREATE TABLE preference_models(id INTEGER,scope TEXT,model_type TEXT,version TEXT,features_json TEXT,model_json TEXT,trained_on INTEGER,created_at TEXT,updated_at TEXT);
        CREATE TABLE project_assets(project_id TEXT,asset_id TEXT,created_at TEXT);
        CREATE TABLE growth_experiments(id INTEGER,project_id TEXT,name TEXT,base_spec_path TEXT,platform TEXT,status TEXT,created_at TEXT);
        CREATE TABLE growth_variants(id INTEGER,experiment_id INTEGER,label TEXT,hook_text TEXT,hook_sub TEXT,editspec_path TEXT,views INTEGER,likes INTEGER,comments INTEGER,shares INTEGER,follows INTEGER,retention_2s REAL,retention_3s REAL,completion_rate REAL,avg_watch_sec REAL,published_at TEXT,updated_at TEXT,factors_json TEXT,retention_curve_json TEXT,external_post_id TEXT);
        CREATE TABLE shot_outcomes(variant_id INTEGER,shot_id TEXT,start_sec REAL,end_sec REAL,retention_in REAL,retention_out REAL,retention_drop REAL,updated_at TEXT);
        CREATE TABLE source_records(asset_id TEXT,source_type TEXT,source_url TEXT,creator TEXT,title TEXT,license TEXT,license_url TEXT,commercial_allowed INTEGER,modification_allowed INTEGER,attribution_required INTEGER,attribution_text TEXT,permission_proof_path TEXT,acquired_at TEXT,license_checked_at TEXT,expires_at TEXT,status TEXT,notes TEXT);
        """
    )
    conn.execute(
        "INSERT INTO assets VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("a", "/a.mp4", "hash", 1920, 1080, 23.976, 2.0, "h264", "/p.mp4", "now"),
    )
    conn.execute(
        "INSERT INTO shots VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("s", "a", 0, 0, 1, None, 1, 1, None, 0, "hero", "run", "joy", None,
         None, "tag", None, 0, b"embed", 0, None, "crop", 0.5, 0),
    )
    conn.commit()
    conn.close()


def test_schema_is_idempotent(tmp_path):
    path = tmp_path / "v2.sqlite"
    connect(path).close()
    connect(path).close()
    conn = sqlite3.connect(path)
    assert conn.execute("select count(*) from schema_migrations").fetchone()[0] == SCHEMA_VERSION
    assert conn.execute("pragma table_info(shots)").fetchall()


def test_etl_preserves_counts_embeddings_and_exact_timebase(tmp_path):
    source, target = tmp_path / "v1.sqlite", tmp_path / "v2.sqlite"
    legacy_db(source)
    aliases = tmp_path / "aliases.json"
    aliases.write_text(json.dumps({"角色": "hero white hair"}))
    report = migrate_v1(source, target, aliases_path=aliases)
    assert report.ok
    assert report.counts["assets"] == (1, 1)
    assert report.counts["shots"] == (1, 1)
    assert report.embeddings == (1, 1)
    assert report.characters == 1
    conn = sqlite3.connect(target)
    assert conn.execute("select fps_num,fps_den from assets").fetchone() == (24000, 1001)
    assert conn.execute("select count(*) from shots_fts").fetchone()[0] == 1


def test_etl_refuses_overwrite(tmp_path):
    source, target = tmp_path / "v1.sqlite", tmp_path / "v2.sqlite"
    legacy_db(source)
    target.touch()
    with pytest.raises(FileExistsError):
        migrate_v1(source, target)
