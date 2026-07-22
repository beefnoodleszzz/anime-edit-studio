"""SQLite data layer for the decision-loop workstation."""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterable

from . import config


def _migration_001_base(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version     INTEGER PRIMARY KEY,
            applied_at  TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS assets (
            id          TEXT PRIMARY KEY,
            path        TEXT NOT NULL,
            sha256      TEXT NOT NULL,
            width       INTEGER,
            height      INTEGER,
            fps         REAL,
            duration    REAL,
            codec       TEXT,
            proxy_path  TEXT,
            created_at  TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS shots (
            id              TEXT PRIMARY KEY,
            asset_id        TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
            idx             INTEGER NOT NULL,
            start_sec       REAL NOT NULL,
            end_sec         REAL NOT NULL,
            keyframe        TEXT,
            brightness      REAL,
            sharpness       REAL,
            motion_dir      TEXT,
            motion_mag      REAL,
            character       TEXT,
            action          TEXT,
            emotion         TEXT,
            camera          TEXT,
            dialogue        TEXT,
            tags            TEXT,
            slot            TEXT,
            picked          INTEGER DEFAULT 0,
            embedding       BLOB,
            reframe_x       REAL DEFAULT 0,
            min_brightness  REAL,
            fill_mode       TEXT DEFAULT 'crop',
            UNIQUE(asset_id, idx)
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS shots_fts USING fts5(
            shot_id UNINDEXED,
            character,
            action,
            emotion,
            dialogue,
            tags,
            content=''
        );
        """
    )


def _migration_002_decision_loop(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS review_decisions (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id          TEXT NOT NULL,
            shot_id             TEXT NOT NULL REFERENCES shots(id) ON DELETE CASCADE,
            decision            TEXT NOT NULL CHECK(decision IN ('use','alternate','reject')),
            reasons             TEXT NOT NULL DEFAULT '[]',
            rating              INTEGER CHECK(rating BETWEEN 1 AND 5),
            trim_start_sec      REAL,
            trim_end_sec        REAL,
            preferred_role      TEXT CHECK(preferred_role IN ('hook','build','climax','release','ending')),
            created_at          TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at          TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(project_id, shot_id)
        );

        CREATE TABLE IF NOT EXISTS creative_briefs (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id          TEXT NOT NULL UNIQUE,
            character_query     TEXT,
            theme               TEXT,
            target_emotions     TEXT NOT NULL DEFAULT '[]',
            duration_sec        REAL,
            aspect_ratio        TEXT,
            target_platform     TEXT,
            structure_json      TEXT NOT NULL DEFAULT '{}',
            reference_video_path TEXT,
            created_at          TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at          TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS shot_scores (
            shot_id               TEXT PRIMARY KEY REFERENCES shots(id) ON DELETE CASCADE,
            technical_quality     REAL NOT NULL DEFAULT 0,
            composition_quality   REAL NOT NULL DEFAULT 0,
            character_salience    REAL NOT NULL DEFAULT 0,
            emotion_intensity     REAL NOT NULL DEFAULT 0,
            action_intensity      REAL NOT NULL DEFAULT 0,
            hook_potential        REAL NOT NULL DEFAULT 0,
            climax_potential      REAL NOT NULL DEFAULT 0,
            ending_potential      REAL NOT NULL DEFAULT 0,
            vertical_crop_score   REAL NOT NULL DEFAULT 0,
            subtitle_risk         REAL NOT NULL DEFAULT 0,
            watermark_risk        REAL NOT NULL DEFAULT 0,
            diversity_score       REAL NOT NULL DEFAULT 0,
            preference_score      REAL NOT NULL DEFAULT 0,
            final_score           REAL NOT NULL DEFAULT 0,
            score_version         TEXT NOT NULL,
            explanation_json      TEXT NOT NULL DEFAULT '{}',
            updated_at            TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS source_records (
            asset_id               TEXT PRIMARY KEY REFERENCES assets(id) ON DELETE CASCADE,
            source_type            TEXT,
            source_url             TEXT,
            creator                TEXT,
            title                  TEXT,
            license                TEXT,
            license_url            TEXT,
            commercial_allowed     INTEGER,
            modification_allowed   INTEGER,
            attribution_required   INTEGER,
            attribution_text       TEXT,
            permission_proof_path  TEXT,
            acquired_at            TEXT,
            license_checked_at     TEXT,
            expires_at             TEXT,
            status                 TEXT NOT NULL DEFAULT 'review' CHECK(status IN ('approved','review','blocked')),
            notes                  TEXT
        );

        CREATE TABLE IF NOT EXISTS cut_variants (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id        TEXT NOT NULL,
            brief_id          INTEGER REFERENCES creative_briefs(id) ON DELETE SET NULL,
            variant_type      TEXT NOT NULL,
            editspec_path     TEXT NOT NULL,
            preview_path      TEXT,
            final_editspec_path TEXT,
            score             REAL NOT NULL DEFAULT 0,
            explanation_json  TEXT NOT NULL DEFAULT '{}',
            selected          INTEGER NOT NULL DEFAULT 0,
            created_at        TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS preference_models (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            scope             TEXT NOT NULL UNIQUE,
            model_type        TEXT NOT NULL,
            version           TEXT NOT NULL,
            features_json     TEXT NOT NULL DEFAULT '[]',
            model_json        TEXT NOT NULL DEFAULT '{}',
            trained_on        INTEGER NOT NULL DEFAULT 0,
            created_at        TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at        TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_shots_asset_id ON shots(asset_id, idx);
        CREATE INDEX IF NOT EXISTS idx_review_project_shot ON review_decisions(project_id, shot_id);
        CREATE INDEX IF NOT EXISTS idx_cut_variants_project ON cut_variants(project_id, selected);
        """
    )


def _migration_003_project_scope(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS project_assets (
            project_id   TEXT NOT NULL,
            asset_id     TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
            created_at   TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY(project_id, asset_id)
        );

        CREATE INDEX IF NOT EXISTS idx_project_assets_project ON project_assets(project_id, asset_id);
        """
    )
    cols = [row["name"] for row in conn.execute("PRAGMA table_info(cut_variants)").fetchall()]
    if "final_editspec_path" not in cols:
        conn.execute("ALTER TABLE cut_variants ADD COLUMN final_editspec_path TEXT")
    _import_legacy_rights(conn)


def _import_legacy_rights(conn: sqlite3.Connection) -> None:
    legacy = config.LIBRARY / "rights.json"
    if not legacy.exists():
        return
    try:
        payload = json.loads(legacy.read_text())
    except json.JSONDecodeError:
        return
    for asset_id, row in payload.items():
        if not isinstance(row, dict):
            continue
        conn.execute(
            """
            INSERT INTO source_records (asset_id, source_url, license, notes, commercial_allowed, status)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(asset_id) DO UPDATE SET
                source_url=COALESCE(source_records.source_url, excluded.source_url),
                license=COALESCE(source_records.license, excluded.license),
                notes=COALESCE(source_records.notes, excluded.notes),
                commercial_allowed=COALESCE(source_records.commercial_allowed, excluded.commercial_allowed),
                status=CASE
                    WHEN source_records.status IS NULL OR source_records.status=''
                    THEN excluded.status
                    ELSE source_records.status
                END
            """,
            (
                asset_id,
                row.get("source"),
                row.get("license"),
                row.get("notes"),
                1 if row.get("commercial_cleared") else 0 if row.get("commercial_cleared") is not None else None,
                "approved" if row.get("commercial_cleared") else "review",
            ),
        )


MIGRATIONS: list[tuple[int, Callable[[sqlite3.Connection], None]]] = [
    (1, _migration_001_base),
    (2, _migration_002_decision_loop),
    (3, _migration_003_project_scope),
]


def connect() -> sqlite3.Connection:
    config.ensure_dirs()
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT (datetime('now')))"
    )
    applied = {
        int(row["version"])
        for row in conn.execute("SELECT version FROM schema_migrations").fetchall()
    }
    for version, fn in MIGRATIONS:
        if version not in applied:
            fn(conn)
            conn.execute("INSERT INTO schema_migrations(version) VALUES (?)", (version,))
    conn.commit()
    return conn


def upsert_asset(conn: sqlite3.Connection, asset: dict) -> None:
    conn.execute(
        """
        INSERT INTO assets (id, path, sha256, width, height, fps, duration, codec, proxy_path)
        VALUES (:id, :path, :sha256, :width, :height, :fps, :duration, :codec, :proxy_path)
        ON CONFLICT(id) DO UPDATE SET
          path=excluded.path,
          sha256=excluded.sha256,
          width=excluded.width,
          height=excluded.height,
          fps=excluded.fps,
          duration=excluded.duration,
          codec=excluded.codec,
          proxy_path=excluded.proxy_path
        """,
        asset,
    )
    conn.commit()


def insert_shot(conn: sqlite3.Connection, shot: dict) -> None:
    conn.execute(
        """
        INSERT INTO shots (id, asset_id, idx, start_sec, end_sec, keyframe)
        VALUES (:id, :asset_id, :idx, :start_sec, :end_sec, :keyframe)
        ON CONFLICT(id) DO UPDATE SET
          start_sec=excluded.start_sec,
          end_sec=excluded.end_sec,
          keyframe=excluded.keyframe
        """,
        shot,
    )


def update_shot_analysis(conn: sqlite3.Connection, shot_id: str, fields: dict) -> None:
    cols = ", ".join(f"{k}=:{k}" for k in fields)
    conn.execute(f"UPDATE shots SET {cols} WHERE id=:id", {**fields, "id": shot_id})
    reindex_shot(conn, shot_id)
    conn.commit()


def reindex_shot(conn: sqlite3.Connection, shot_id: str) -> None:
    conn.execute("DELETE FROM shots_fts WHERE shot_id=?", (shot_id,))
    row = conn.execute(
        "SELECT character, action, emotion, dialogue, tags FROM shots WHERE id=?",
        (shot_id,),
    ).fetchone()
    if row:
        conn.execute(
            "INSERT INTO shots_fts (shot_id, character, action, emotion, dialogue, tags) VALUES (?,?,?,?,?,?)",
            (
                shot_id,
                row["character"] or "",
                row["action"] or "",
                row["emotion"] or "",
                row["dialogue"] or "",
                row["tags"] or "",
            ),
        )


def reindex_all(conn: sqlite3.Connection) -> None:
    for row in conn.execute("SELECT id FROM shots").fetchall():
        reindex_shot(conn, row["id"])
    conn.commit()


def asset_by_id(conn: sqlite3.Connection, asset_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM assets WHERE id=? OR id LIKE ?",
        (asset_id, asset_id + "%"),
    ).fetchone()


def rows_to_dicts(rows: Iterable[sqlite3.Row]) -> list[dict]:
    return [dict(row) for row in rows]


def json_dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False)


def attach_project_assets(conn: sqlite3.Connection, project_id: str, asset_ids: Iterable[str]) -> int:
    count = 0
    for asset_id in asset_ids:
        conn.execute(
            "INSERT OR IGNORE INTO project_assets(project_id, asset_id) VALUES (?, ?)",
            (project_id, asset_id),
        )
        count += 1
    conn.commit()
    return count


def project_asset_ids(conn: sqlite3.Connection, project_id: str) -> list[str]:
    rows = conn.execute(
        "SELECT asset_id FROM project_assets WHERE project_id=? ORDER BY asset_id",
        (project_id,),
    ).fetchall()
    return [row["asset_id"] for row in rows]
