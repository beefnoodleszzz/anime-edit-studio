"""SQLite 镜头库:assets / shots + FTS5 全文检索。"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS assets (
    id          TEXT PRIMARY KEY,           -- sha256 前 12 位
    path        TEXT NOT NULL,
    sha256      TEXT NOT NULL,
    width       INTEGER, height INTEGER,
    fps         REAL, duration REAL,
    codec       TEXT,
    proxy_path  TEXT,
    created_at  TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS shots (
    id          TEXT PRIMARY KEY,           -- <asset_id>-<idx>
    asset_id    TEXT NOT NULL REFERENCES assets(id),
    idx         INTEGER NOT NULL,
    start_sec   REAL NOT NULL, end_sec REAL NOT NULL,
    keyframe    TEXT,
    -- 分析字段(analyze 填充)
    brightness  REAL, sharpness REAL,
    motion_dir  TEXT, motion_mag REAL,
    character   TEXT, action TEXT, emotion TEXT, camera TEXT,
    dialogue    TEXT,
    tags        TEXT,                         -- 逗号分隔
    slot        TEXT,                         -- opening|build|climax|ending
    picked      INTEGER DEFAULT 0,            -- 历史被选次数
    UNIQUE(asset_id, idx)
);

CREATE VIRTUAL TABLE IF NOT EXISTS shots_fts USING fts5(
    shot_id UNINDEXED, character, action, emotion, dialogue, tags,
    content=''
);
"""


def connect() -> sqlite3.Connection:
    config.ensure_dirs()
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.executescript(SCHEMA)
    # 迁移:CLIP 语义向量列
    cols = [r[1] for r in conn.execute("PRAGMA table_info(shots)")]
    if "embedding" not in cols:
        conn.execute("ALTER TABLE shots ADD COLUMN embedding BLOB")
    if "reframe_x" not in cols:
        conn.execute("ALTER TABLE shots ADD COLUMN reframe_x REAL DEFAULT 0")
    if "min_brightness" not in cols:
        conn.execute("ALTER TABLE shots ADD COLUMN min_brightness REAL")
    if "fill_mode" not in cols:                    # 跨画幅装帧:crop 满屏 | fit_blur 毛玻璃留全帧
        conn.execute("ALTER TABLE shots ADD COLUMN fill_mode TEXT DEFAULT 'crop'")
    conn.commit()
    return conn


def upsert_asset(conn: sqlite3.Connection, asset: dict) -> None:
    conn.execute(
        """INSERT INTO assets (id, path, sha256, width, height, fps, duration, codec, proxy_path)
           VALUES (:id, :path, :sha256, :width, :height, :fps, :duration, :codec, :proxy_path)
           ON CONFLICT(id) DO UPDATE SET proxy_path=excluded.proxy_path, path=excluded.path""",
        asset,
    )
    conn.commit()


def insert_shot(conn: sqlite3.Connection, shot: dict) -> None:
    conn.execute(
        """INSERT INTO shots (id, asset_id, idx, start_sec, end_sec, keyframe)
           VALUES (:id, :asset_id, :idx, :start_sec, :end_sec, :keyframe)
           ON CONFLICT(id) DO UPDATE SET
             start_sec=excluded.start_sec, end_sec=excluded.end_sec, keyframe=excluded.keyframe""",
        shot,
    )


def update_shot_analysis(conn: sqlite3.Connection, shot_id: str, fields: dict) -> None:
    cols = ", ".join(f"{k}=:{k}" for k in fields)
    conn.execute(f"UPDATE shots SET {cols} WHERE id=:id", {**fields, "id": shot_id})
    # 同步 FTS
    conn.execute("DELETE FROM shots_fts WHERE shot_id=?", (shot_id,))
    row = conn.execute(
        "SELECT character, action, emotion, dialogue, tags FROM shots WHERE id=?", (shot_id,)
    ).fetchone()
    if row:
        conn.execute(
            "INSERT INTO shots_fts (shot_id, character, action, emotion, dialogue, tags) VALUES (?,?,?,?,?,?)",
            (shot_id, row["character"] or "", row["action"] or "", row["emotion"] or "",
             row["dialogue"] or "", row["tags"] or ""),
        )
    conn.commit()


def asset_by_id(conn: sqlite3.Connection, asset_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM assets WHERE id=? OR id LIKE ?",
                        (asset_id, asset_id + "%")).fetchone()
