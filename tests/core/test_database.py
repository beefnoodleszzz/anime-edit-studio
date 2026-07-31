from __future__ import annotations

import sqlite3

from studio.core.database import SCHEMA_VERSION, connect


def test_schema_is_idempotent(tmp_path):
    path = tmp_path / "v2.sqlite"
    connect(path).close()
    connect(path).close()
    conn = sqlite3.connect(path)
    assert conn.execute("select count(*) from schema_migrations").fetchone()[0] == SCHEMA_VERSION
    assert conn.execute("pragma table_info(shots)").fetchall()


def test_old_product_tables_are_gone(tmp_path):
    """REFACTOR.md §14: Candidate/Preference/Growth/Director/EditSpec/Recipe
    storage must not survive in the runtime schema."""
    conn = connect(tmp_path / "v2.sqlite")
    tables = {
        row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    for name in (
        "candidate_groups", "preference_pairs", "preference_models",
        "growth_experiments", "growth_variants", "shot_outcomes",
        "director_plans", "edit_specs", "edit_spec_diffs", "recipes",
        "creative_briefs", "reference_videos", "feedback_events",
        "revision_runs", "project_assets", "llm_calls",
    ):
        assert name not in tables, name
    conn.close()
