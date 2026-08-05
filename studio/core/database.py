"""Versioned SQLite schema for the AMV engine's v2 database."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from studio.core.state import ensure_state_schema

SCHEMA_VERSION = 19
REPO = Path(__file__).resolve().parents[2]
DEFAULT_V2_DB = REPO / "library" / "engine.v2.sqlite"


def _migration_001(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
        );

        CREATE TABLE assets (
            id TEXT PRIMARY KEY,
            path TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            width INTEGER, height INTEGER,
            fps_num INTEGER, fps_den INTEGER,
            duration_sec REAL,
            codec TEXT, proxy_path TEXT,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
        );

        CREATE TABLE shots (
            id TEXT PRIMARY KEY,
            asset_id TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
            idx INTEGER NOT NULL,
            start_sec REAL NOT NULL,
            end_sec REAL NOT NULL CHECK(end_sec > start_sec),
            keyframe TEXT,
            brightness REAL, sharpness REAL,
            motion_dir TEXT, motion_mag REAL,
            character TEXT, action TEXT, emotion TEXT, camera TEXT,
            dialogue TEXT, tags TEXT, slot TEXT,
            picked INTEGER NOT NULL DEFAULT 0,
            embedding BLOB,
            aesthetic REAL,
            growth_score REAL NOT NULL DEFAULT 0,
            shot_scale REAL, shot_scale_confidence REAL,
            subject_motion REAL, subject_motion_confidence REAL,
            pose_quality REAL, pose_quality_confidence REAL,
            face_visibility REAL, face_visibility_confidence REAL,
            eye_visibility REAL, eye_visibility_confidence REAL,
            visual_energy REAL, visual_energy_confidence REAL,
            compression_score REAL, compression_score_confidence REAL,
            subtitle_region TEXT, subtitle_region_confidence REAL,
            color_palette TEXT, color_palette_confidence REAL,
            audio_energy REAL, audio_energy_confidence REAL,
            music_presence REAL, music_presence_confidence REAL,
            cutability REAL, cutability_confidence REAL,
            analysis_version TEXT,
            UNIQUE(asset_id, idx)
        );

        CREATE VIRTUAL TABLE shots_fts USING fts5(
            shot_id UNINDEXED, character, action, emotion, dialogue, tags, content=''
        );

        CREATE TABLE review_decisions (
            id INTEGER PRIMARY KEY,
            project_id TEXT NOT NULL,
            shot_id TEXT NOT NULL REFERENCES shots(id) ON DELETE CASCADE,
            decision TEXT NOT NULL CHECK(decision IN ('use','alternate','reject')),
            reasons TEXT NOT NULL DEFAULT '[]',
            rating INTEGER CHECK(rating BETWEEN 1 AND 5),
            trim_start_sec REAL, trim_end_sec REAL,
            preferred_role TEXT,
            created_at TEXT, updated_at TEXT,
            UNIQUE(project_id, shot_id)
        );

        CREATE TABLE creative_briefs (
            id INTEGER PRIMARY KEY,
            project_id TEXT NOT NULL UNIQUE,
            character_query TEXT, theme TEXT,
            target_emotions TEXT NOT NULL DEFAULT '[]',
            duration_sec REAL, aspect_ratio TEXT, target_platform TEXT,
            structure_json TEXT NOT NULL DEFAULT '{}',
            reference_video_path TEXT,
            creative_contract_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT, updated_at TEXT
        );

        CREATE TABLE preference_models (
            id INTEGER PRIMARY KEY,
            scope TEXT NOT NULL UNIQUE,
            model_type TEXT NOT NULL, version TEXT NOT NULL,
            features_json TEXT NOT NULL DEFAULT '[]',
            model_json TEXT NOT NULL DEFAULT '{}',
            trained_on INTEGER NOT NULL DEFAULT 0,
            created_at TEXT, updated_at TEXT
        );

        CREATE TABLE project_assets (
            project_id TEXT NOT NULL,
            asset_id TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
            created_at TEXT,
            PRIMARY KEY(project_id, asset_id)
        );

        CREATE TABLE growth_experiments (
            id INTEGER PRIMARY KEY,
            project_id TEXT NOT NULL, name TEXT NOT NULL,
            base_spec_path TEXT NOT NULL, platform TEXT,
            status TEXT NOT NULL DEFAULT 'draft',
            created_at TEXT,
            UNIQUE(project_id, name)
        );

        CREATE TABLE growth_variants (
            id INTEGER PRIMARY KEY,
            experiment_id INTEGER NOT NULL REFERENCES growth_experiments(id) ON DELETE CASCADE,
            label TEXT NOT NULL, hook_text TEXT NOT NULL, hook_sub TEXT NOT NULL DEFAULT '',
            editspec_path TEXT NOT NULL,
            views INTEGER NOT NULL DEFAULT 0, likes INTEGER NOT NULL DEFAULT 0,
            comments INTEGER NOT NULL DEFAULT 0, shares INTEGER NOT NULL DEFAULT 0,
            follows INTEGER NOT NULL DEFAULT 0,
            retention_2s REAL, retention_3s REAL, completion_rate REAL, avg_watch_sec REAL,
            published_at TEXT, updated_at TEXT,
            factors_json TEXT NOT NULL DEFAULT '{}',
            retention_curve_json TEXT NOT NULL DEFAULT '[]',
            external_post_id TEXT,
            UNIQUE(experiment_id, label)
        );

        CREATE TABLE shot_outcomes (
            variant_id INTEGER NOT NULL REFERENCES growth_variants(id) ON DELETE CASCADE,
            shot_id TEXT NOT NULL,
            start_sec REAL NOT NULL, end_sec REAL NOT NULL,
            retention_in REAL, retention_out REAL, retention_drop REAL, updated_at TEXT,
            PRIMARY KEY(variant_id, shot_id, start_sec)
        );

        CREATE TABLE source_records (
            asset_id TEXT PRIMARY KEY REFERENCES assets(id) ON DELETE CASCADE,
            source_type TEXT, source_url TEXT, creator TEXT, title TEXT,
            license TEXT, license_url TEXT,
            commercial_allowed INTEGER, modification_allowed INTEGER,
            attribution_required INTEGER, attribution_text TEXT,
            permission_proof_path TEXT, acquired_at TEXT, license_checked_at TEXT,
            expires_at TEXT, status TEXT NOT NULL DEFAULT 'review', notes TEXT
        );

        CREATE TABLE characters (
            id TEXT PRIMARY KEY,
            canonical_name TEXT NOT NULL,
            aliases_json TEXT NOT NULL DEFAULT '[]',
            reference_images_json TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
        );
        CREATE TABLE music_tracks (
            id TEXT PRIMARY KEY REFERENCES assets(id),
            music_map_json TEXT, analysis_version TEXT, updated_at TEXT
        );
        CREATE TABLE reference_videos (
            id TEXT PRIMARY KEY REFERENCES assets(id),
            style_fingerprint_json TEXT, analysis_version TEXT, updated_at TEXT
        );
        CREATE TABLE director_plans (
            id TEXT NOT NULL, project_id TEXT NOT NULL, version INTEGER NOT NULL,
            plan_yaml TEXT NOT NULL, generation_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
            PRIMARY KEY(id, version)
        );
        CREATE TABLE edit_specs (
            project_id TEXT NOT NULL, version INTEGER NOT NULL,
            spec_json TEXT NOT NULL, parent_version INTEGER,
            created_by TEXT NOT NULL CHECK(created_by IN ('ai','user','critic','rule','migration')),
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
            PRIMARY KEY(project_id, version)
        );
        CREATE TABLE edit_spec_diffs (
            project_id TEXT NOT NULL,
            from_version INTEGER NOT NULL, to_version INTEGER NOT NULL,
            ops_json TEXT NOT NULL,
            source TEXT NOT NULL CHECK(source IN ('ai','user','critic','rule')),
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
            PRIMARY KEY(project_id, from_version, to_version)
        );
        CREATE TABLE candidate_groups (
            id TEXT PRIMARY KEY, project_id TEXT NOT NULL, role TEXT NOT NULL,
            shot_ids_json TEXT NOT NULL, selected_shot_id TEXT,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
        );
        CREATE TABLE preference_pairs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            winner_shot_id TEXT NOT NULL, loser_shot_id TEXT NOT NULL,
            context_json TEXT NOT NULL DEFAULT '{}', project_style TEXT,
            project_id TEXT, created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
        );
        CREATE TABLE recipes (
            id TEXT NOT NULL, version TEXT NOT NULL, kind TEXT NOT NULL,
            engine TEXT NOT NULL, params_schema_json TEXT NOT NULL,
            capability TEXT NOT NULL, verified INTEGER NOT NULL DEFAULT 0,
            artifact_path TEXT, preview_path TEXT, acceptance_path TEXT,
            PRIMARY KEY(id, version)
        );
        CREATE TABLE renders (
            id TEXT PRIMARY KEY, project_id TEXT NOT NULL, spec_version INTEGER NOT NULL,
            backend TEXT NOT NULL, preset TEXT NOT NULL, output_path TEXT,
            duration_sec REAL, status TEXT NOT NULL,
            started_at TEXT, finished_at TEXT, error_json TEXT
        );
        CREATE TABLE qa_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            render_id TEXT NOT NULL REFERENCES renders(id) ON DELETE CASCADE,
            kind TEXT NOT NULL CHECK(kind IN ('technical','creative')),
            passed INTEGER NOT NULL, checks_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
        );

        CREATE INDEX idx_shots_asset ON shots(asset_id, idx);
        CREATE INDEX idx_review_project ON review_decisions(project_id, shot_id);
        CREATE INDEX idx_growth_project ON growth_experiments(project_id, status);
        CREATE INDEX idx_pairs_project ON preference_pairs(project_id);
        """
    )
    ensure_state_schema(conn)


def _migration_002(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE shot_tracks (
            shot_id TEXT NOT NULL REFERENCES shots(id) ON DELETE CASCADE,
            version TEXT NOT NULL,
            sample_fps REAL NOT NULL,
            boxes_json TEXT NOT NULL,
            mean_confidence REAL NOT NULL,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
            PRIMARY KEY(shot_id, version)
        );
        CREATE INDEX idx_shot_tracks_confidence
          ON shot_tracks(version, mean_confidence);
        """
    )


def _migration_003(conn: sqlite3.Connection) -> None:
    """Complete the Phase 3 Shot contract required by docs/product/WANT.md §11."""
    additions = (
        ("character_confidence", "REAL"),
        ("action_confidence", "REAL"),
        ("emotion_confidence", "REAL"),
        ("camera_motion", "TEXT"),
        ("camera_motion_confidence", "REAL"),
        ("composition", "TEXT"),
        ("composition_confidence", "REAL"),
        ("image_quality", "REAL"),
        ("image_quality_confidence", "REAL"),
        ("blur_score", "REAL"),
        ("blur_score_confidence", "REAL"),
    )
    existing = {row[1] for row in conn.execute("PRAGMA table_info(shots)")}
    for name, kind in additions:
        if name not in existing:
            conn.execute(f"ALTER TABLE shots ADD COLUMN {name} {kind}")


def _migration_004(conn: sqlite3.Connection) -> None:
    """Separate cross-project intrinsic scores from project context scores."""
    conn.executescript(
        """
        CREATE TABLE shot_scores (
            shot_id TEXT NOT NULL REFERENCES shots(id) ON DELETE CASCADE,
            version TEXT NOT NULL,
            components_json TEXT NOT NULL,
            composite REAL NOT NULL,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
            PRIMARY KEY(shot_id, version)
        );
        CREATE TABLE candidate_scores (
            project_id TEXT NOT NULL,
            role TEXT NOT NULL,
            shot_id TEXT NOT NULL REFERENCES shots(id) ON DELETE CASCADE,
            version TEXT NOT NULL,
            components_json TEXT NOT NULL,
            contextual REAL NOT NULL,
            total REAL NOT NULL,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
            PRIMARY KEY(project_id, role, shot_id, version)
        );
        CREATE INDEX idx_candidate_scores_rank
          ON candidate_scores(project_id, role, version, total DESC);
        """
    )


def _migration_005(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE feedback_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id TEXT NOT NULL,
            spec_version INTEGER NOT NULL,
            kind TEXT NOT NULL CHECK(kind IN (
              'replacement','timing','effect','audio','reframe','survival'
            )),
            clip_id TEXT,
            before_json TEXT NOT NULL DEFAULT '{}',
            after_json TEXT NOT NULL DEFAULT '{}',
            source TEXT NOT NULL CHECK(source IN ('user','critic','metrics')),
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
        );
        CREATE INDEX idx_feedback_project
          ON feedback_events(project_id,spec_version,kind);
        """
    )


def _migration_006(conn: sqlite3.Connection) -> None:
    """Auditable Phase 7 model calls and retryable revision runs."""
    conn.executescript(
        """
        CREATE TABLE llm_calls (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id TEXT NOT NULL,
            stage TEXT NOT NULL,
            provider TEXT NOT NULL,
            model TEXT NOT NULL,
            request_id TEXT,
            duration_ms INTEGER NOT NULL CHECK(duration_ms >= 0),
            cost_usd REAL,
            schema_name TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('complete','failed')),
            error_json TEXT,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
        );
        CREATE TABLE revision_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id TEXT NOT NULL,
            from_version INTEGER NOT NULL,
            to_version INTEGER,
            source TEXT NOT NULL CHECK(source IN ('user','critic')),
            feedback TEXT,
            review_json TEXT,
            diff_json TEXT,
            status TEXT NOT NULL CHECK(status IN ('running','complete','failed')),
            attempt INTEGER NOT NULL DEFAULT 1 CHECK(attempt >= 1),
            error_json TEXT,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
            finished_at TEXT
        );
        CREATE INDEX idx_revision_runs_project
          ON revision_runs(project_id,from_version,created_at);
        """
    )


def _migration_007(conn: sqlite3.Connection) -> None:
    """Version candidate generations so a re-plan cannot leak stale groups."""
    existing = {row[1] for row in conn.execute("PRAGMA table_info(candidate_groups)")}
    if "plan_revision" not in existing:
        conn.execute(
            "ALTER TABLE candidate_groups ADD COLUMN plan_revision INTEGER NOT NULL DEFAULT 1"
        )
    if "active" not in existing:
        conn.execute(
            "ALTER TABLE candidate_groups ADD COLUMN active INTEGER NOT NULL DEFAULT 1 "
            "CHECK(active IN (0,1))"
        )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_candidate_groups_active "
        "ON candidate_groups(project_id,active,role,plan_revision)"
    )


def _migration_008(conn: sqlite3.Connection) -> None:
    """Keep delegated AI choices distinct from measured human acceptance."""
    existing = {row[1] for row in conn.execute("PRAGMA table_info(candidate_groups)")}
    if "selection_source" not in existing:
        conn.execute(
            "ALTER TABLE candidate_groups ADD COLUMN selection_source TEXT "
            "CHECK(selection_source IN ('human','ai'))"
        )


def _migration_009(conn: sqlite3.Connection) -> None:
    """Persist deterministic Graphic Match descriptors per representative frame."""
    existing = {row[1] for row in conn.execute("PRAGMA table_info(shots)")}
    if "graphic_features" not in existing:
        conn.execute("ALTER TABLE shots ADD COLUMN graphic_features TEXT")
    if "graphic_features_confidence" not in existing:
        conn.execute(
            "ALTER TABLE shots ADD COLUMN graphic_features_confidence REAL"
        )


def _migration_010(conn: sqlite3.Connection) -> None:
    """Persist measured frame-exact action peaks per shot (Action Sync)."""
    existing = {row[1] for row in conn.execute("PRAGMA table_info(shots)")}
    if "action_peaks" not in existing:
        conn.execute("ALTER TABLE shots ADD COLUMN action_peaks TEXT")
    if "action_peaks_version" not in existing:
        conn.execute("ALTER TABLE shots ADD COLUMN action_peaks_version TEXT")


def _migration_011(conn: sqlite3.Connection) -> None:
    """Persist self-hosted per-shot subject foreground layers (W4a)."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS subject_layers (
            shot_id TEXT NOT NULL REFERENCES shots(id) ON DELETE CASCADE,
            version TEXT NOT NULL,
            sample_fps REAL NOT NULL,
            mean_coverage REAL NOT NULL,
            horizontal_sweep REAL NOT NULL,
            samples_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
            PRIMARY KEY(shot_id, version)
        )
        """
    )


def _migration_012(conn: sqlite3.Connection) -> None:
    """Cache multi-frame delivery-crop quality for candidate hard gating."""
    conn.executescript(
        """
        CREATE TABLE shot_temporal_quality (
            shot_id TEXT NOT NULL REFERENCES shots(id) ON DELETE CASCADE,
            version TEXT NOT NULL,
            asset_hash TEXT NOT NULL,
            target_aspect REAL NOT NULL,
            sample_count INTEGER NOT NULL,
            quality_floor REAL NOT NULL,
            bad_frame_ratio REAL NOT NULL,
            crop_fitness REAL NOT NULL,
            details_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
            PRIMARY KEY(shot_id, version, target_aspect)
        );
        CREATE INDEX idx_temporal_quality_gate
          ON shot_temporal_quality(version,target_aspect,quality_floor,bad_frame_ratio,crop_fitness);
        """
    )


def _migration_013(conn: sqlite3.Connection) -> None:
    """Upgrade databases that applied the initial quality-cache schema."""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(shot_temporal_quality)")}
    if "asset_hash" not in columns:
        conn.execute(
            "ALTER TABLE shot_temporal_quality ADD COLUMN asset_hash TEXT NOT NULL DEFAULT ''"
        )


def _migration_014(conn: sqlite3.Connection) -> None:
    """Make candidate review groups address exact timeline slots."""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(candidate_groups)")}
    for name, kind in (
        ("slot_key", "TEXT"),
        ("timeline_in_sec", "REAL"),
        ("timeline_duration_sec", "REAL"),
    ):
        if name not in columns:
            conn.execute(f"ALTER TABLE candidate_groups ADD COLUMN {name} {kind}")
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_candidate_groups_slot
        ON candidate_groups(project_id,active,slot_key)
        """
    )


def _migration_015(conn: sqlite3.Connection) -> None:
    """New Demo-driven AMV chain (REFACTOR.md §14): ReferenceBlueprint/MusicTimeline
    caches keyed by source hash, plus AMV projects and their run history.

    These tables are additive. The old candidate/preference/growth/review
    tables above are untouched here; they are dropped only after the new
    chain is verified end-to-end (REFACTOR.md §0.1, §13).
    """
    conn.executescript(
        """
        CREATE TABLE reference_blueprints (
            source_hash TEXT PRIMARY KEY,
            version TEXT NOT NULL,
            blueprint_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
        );
        CREATE TABLE music_timelines (
            source_hash TEXT PRIMARY KEY,
            version TEXT NOT NULL,
            timeline_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
        );
        CREATE TABLE amv_projects (
            id TEXT PRIMARY KEY,
            demo_path TEXT NOT NULL,
            materials_dir TEXT NOT NULL,
            music_path TEXT,
            focus TEXT,
            series_scope TEXT,
            output_dir TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
            updated_at TEXT
        );
        CREATE TABLE amv_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id TEXT NOT NULL REFERENCES amv_projects(id) ON DELETE CASCADE,
            stage TEXT NOT NULL CHECK(stage IN (
              'analyze_reference','analyze_music','plan','compile',
              'render_probe','optimize','render_preview','qa','release'
            )),
            status TEXT NOT NULL CHECK(status IN ('running','complete','failed')),
            amv_spec_hash TEXT,
            details_json TEXT NOT NULL DEFAULT '{}',
            error_json TEXT,
            started_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
            finished_at TEXT
        );
        CREATE INDEX idx_amv_runs_project ON amv_runs(project_id, stage, started_at);
        """
    )


def _migration_016(conn: sqlite3.Connection) -> None:
    """Drop the old product's tables (REFACTOR.md §14): Candidate A/B/C,
    Preference, Growth, the old Director/EditSpec pipeline, and Recipe Zoo
    are gone from the codebase, so their storage goes too."""
    for table in (
        "candidate_groups",
        "preference_pairs",
        "preference_models",
        "growth_experiments",
        "growth_variants",
        "shot_outcomes",
        "director_plans",
        "edit_specs",
        "edit_spec_diffs",
        "recipes",
        "creative_briefs",
        "reference_videos",
        "feedback_events",
        "revision_runs",
        "project_assets",
        "llm_calls",
    ):
        conn.execute(f"DROP TABLE IF EXISTS {table}")


def _migration_017(conn: sqlite3.Connection) -> None:
    """ShotWindow selection stage (REFACTOR.md §6/§17): the unit GlobalSequencePlanner
    chooses is a precise time window inside a Shot, not the whole Shot. Also drops
    the legacy human-review table: the old Review UI is gone, and the automated
    selection flow must not depend on it (REFACTOR.md §6)."""
    conn.executescript(
        """
        DROP TABLE IF EXISTS review_decisions;

        CREATE TABLE shot_windows (
            id TEXT PRIMARY KEY,
            shot_id TEXT NOT NULL REFERENCES shots(id) ON DELETE CASCADE,
            asset_id TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
            start_sec REAL NOT NULL,
            end_sec REAL NOT NULL,
            anchor_sec REAL NOT NULL,
            kind TEXT NOT NULL,
            technical_pass INTEGER NOT NULL,
            features_json TEXT NOT NULL,
            analysis_version TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
            UNIQUE(shot_id, start_sec, end_sec, kind, analysis_version)
        );
        CREATE INDEX idx_shot_windows_shot
          ON shot_windows(shot_id, analysis_version);
        CREATE INDEX idx_shot_windows_kind
          ON shot_windows(kind, technical_pass);

        CREATE TABLE shot_window_embeddings (
            window_id TEXT NOT NULL REFERENCES shot_windows(id) ON DELETE CASCADE,
            kind TEXT NOT NULL CHECK(kind IN ('frame','subject','face')),
            model TEXT NOT NULL,
            embedding BLOB NOT NULL,
            PRIMARY KEY(window_id, kind, model)
        );

        CREATE TABLE shot_window_tracks (
            window_id TEXT NOT NULL REFERENCES shot_windows(id) ON DELETE CASCADE,
            backend TEXT NOT NULL,
            version TEXT NOT NULL,
            points_json TEXT NOT NULL,
            mean_confidence REAL NOT NULL,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
            PRIMARY KEY(window_id, backend, version)
        );
        """
    )


def _migration_018(conn: sqlite3.Connection) -> None:
    """Drop the CV candidate-generation/selection stage's storage (ingest,
    shot detection, embeddings, tracking, ShotWindow scoring caches). Shots
    now arrive pre-selected by ID from the external anime-shot-library
    catalog, so none of this is computed or cached locally any more; only
    the minimal ``assets``/``shots`` shape the planning/execution layer
    still reads (path/fps lookup, start_sec/end_sec, light tag metadata for
    continuity scoring) survives, plus a pointer back to the source library
    shot for traceability."""
    for table in (
        "shot_windows",
        "shot_window_embeddings",
        "shot_window_tracks",
        "shot_tracks",
        "shot_scores",
        "candidate_scores",
        "shot_temporal_quality",
        "subject_layers",
        "characters",
        "source_records",
        "shots_fts",
        "music_tracks",
    ):
        conn.execute(f"DROP TABLE IF EXISTS {table}")

    conn.executescript(
        """
        DROP TABLE shots;
        CREATE TABLE shots (
            id TEXT PRIMARY KEY,
            asset_id TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
            source_shot_id TEXT,
            start_sec REAL NOT NULL,
            end_sec REAL NOT NULL CHECK(end_sec > start_sec),
            character TEXT,
            series TEXT,
            tags_json TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
        );
        CREATE INDEX idx_shots_asset ON shots(asset_id);
        CREATE INDEX idx_shots_source ON shots(source_shot_id);
        """
    )


def _migration_019(conn: sqlite3.Connection) -> None:
    """amv_projects now records the shot_ids the caller supplied (resolved
    through anime-shot-library) instead of a materials directory to scan."""
    conn.executescript(
        """
        DROP TABLE amv_projects;
        CREATE TABLE amv_projects (
            id TEXT PRIMARY KEY,
            demo_path TEXT NOT NULL,
            shot_ids_json TEXT NOT NULL,
            music_path TEXT,
            focus TEXT,
            series_scope TEXT,
            output_dir TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
            updated_at TEXT
        );
        """
    )


MIGRATIONS = (
    (1, _migration_001),
    (2, _migration_002),
    (3, _migration_003),
    (4, _migration_004),
    (5, _migration_005),
    (6, _migration_006),
    (7, _migration_007),
    (8, _migration_008),
    (9, _migration_009),
    (10, _migration_010),
    (11, _migration_011),
    (12, _migration_012),
    (13, _migration_013),
    (14, _migration_014),
    (15, _migration_015),
    (16, _migration_016),
    (17, _migration_017),
    (18, _migration_018),
    (19, _migration_019),
)


def connect(path: Path = DEFAULT_V2_DB) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    existing = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
    ).fetchone()
    applied = (
        {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
        if existing else set()
    )
    for version, migration in MIGRATIONS:
        if version not in applied:
            with conn:
                migration(conn)
                conn.execute("INSERT INTO schema_migrations(version) VALUES (?)", (version,))
    return conn


__all__ = [
    "DEFAULT_V2_DB",
    "SCHEMA_VERSION",
    "connect",
]
