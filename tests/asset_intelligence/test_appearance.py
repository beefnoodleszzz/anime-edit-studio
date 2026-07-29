import sqlite3

import numpy as np
import pytest

from studio.asset_intelligence.appearance import (
    APPEARANCE_CATALOG_VERSION,
    build_appearance_catalog,
    persist_appearance_catalog,
)


def _unit(*values: float) -> bytes:
    vector = np.asarray(values, dtype=np.float32)
    return (vector / np.linalg.norm(vector)).astype(np.float32).tobytes()


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE assets (id TEXT PRIMARY KEY, path TEXT)")
    conn.execute(
        "CREATE TABLE shots (id TEXT PRIMARY KEY, character TEXT,"
        " character_confidence REAL, asset_id TEXT, embedding BLOB)"
    )
    conn.execute(
        "CREATE TABLE characters (id TEXT PRIMARY KEY, canonical_name TEXT,"
        " aliases_json TEXT, reference_images_json TEXT, created_at TEXT)"
    )
    conn.execute("INSERT INTO assets VALUES ('in','/lib/sources/kimetsu/ep06.mkv')")
    conn.execute("INSERT INTO assets VALUES ('out','/lib/sources/jujutsu/ep17.mkv')")
    return conn


def _add(conn, shot_id, asset, vector, character=""):
    conn.execute(
        "INSERT INTO shots VALUES (?,?,?,?,?)",
        (shot_id, character, None, asset, vector),
    )


def _catalog(conn, **kw):
    params = dict(
        character_id="akaza", canonical_name="Akaza",
        seed_shot_ids=["seed0", "seed1"],
        scope_asset_patterns=["%kimetsu%"],
        negative_character_ids=["rengoku"],
    )
    params.update(kw)
    return build_appearance_catalog(conn, **params)


def _fixture() -> sqlite3.Connection:
    conn = _conn()
    # Two distinct confirmed looks, a near neighbour of each, a co-star, and a
    # look-alike from another work.
    _add(conn, "seed0", "in", _unit(1, 0, 0))
    _add(conn, "seed1", "in", _unit(0, 1, 0))
    _add(conn, "near0", "in", _unit(0.97, 0.05, 0.05))
    _add(conn, "near1", "in", _unit(0.05, 0.97, 0.05))
    _add(conn, "costar", "in", _unit(0, 0, 1), character="rengoku")
    _add(conn, "lookalike", "out", _unit(0.98, 0.03, 0.03))
    _add(conn, "rengoku_ref", "in", _unit(0, 0, 1), character="rengoku")
    return conn


def test_expands_seeds_to_their_neighbours():
    catalog = _catalog(_fixture())
    found = {match.shot_id for match in catalog.matches}
    assert {"near0", "near1"} <= found
    assert catalog.version == APPEARANCE_CATALOG_VERSION


def test_scope_is_a_hard_gate_no_score_overrides_it():
    """The look-alike is nearer the seed than anything else — and still excluded."""
    catalog = _catalog(_fixture())
    assert "lookalike" not in {match.shot_id for match in catalog.matches}


def test_costar_in_the_same_episodes_is_rejected_by_the_margin():
    """Scope cannot separate co-stars; only the negative seeds can."""
    catalog = _catalog(_fixture())
    assert "costar" not in {match.shot_id for match in catalog.matches}


def test_max_to_any_seed_not_centroid():
    """Two distant looks both keep their neighbours; a centroid would lose both."""
    conn = _fixture()
    catalog = _catalog(conn)
    similarities = {m.shot_id: m.similarity for m in catalog.matches}
    assert similarities["near0"] > 0.9
    assert similarities["near1"] > 0.9


def test_negative_seeds_are_mandatory():
    with pytest.raises(ValueError, match="负种子"):
        _catalog(_fixture(), negative_character_ids=[])


def test_empty_scope_is_rejected():
    with pytest.raises(ValueError, match="范围是硬门禁"):
        _catalog(_fixture(), scope_asset_patterns=[])


def test_seeds_are_mandatory():
    with pytest.raises(ValueError, match="种子"):
        _catalog(_fixture(), seed_shot_ids=[])


def test_persist_stamps_character_and_records_the_catalog():
    conn = _fixture()
    catalog = _catalog(conn)
    written = persist_appearance_catalog(conn, catalog)
    assert written == len(catalog.matches)
    labelled = {
        row["id"]
        for row in conn.execute("SELECT id FROM shots WHERE character='akaza'")
    }
    assert {"near0", "near1"} <= labelled
    row = conn.execute("SELECT * FROM characters WHERE id='akaza'").fetchone()
    assert row["canonical_name"] == "Akaza"


def test_confidence_tracks_margin_not_raw_similarity():
    catalog = _catalog(_fixture())
    ordered = sorted(catalog.matches, key=lambda m: m.margin)
    assert ordered[0].confidence <= ordered[-1].confidence


def test_build_is_deterministic():
    first = _catalog(_fixture())
    second = _catalog(_fixture())
    assert first.model_dump_json() == second.model_dump_json()
