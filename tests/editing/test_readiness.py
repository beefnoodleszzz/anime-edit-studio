"""The gate that should have stopped an eighteen-second film of the wrong person."""
import sqlite3

import pytest

from studio.editing.readiness import (
    MIN_IDENTIFIED_SHOTS,
    ProductionNotReady,
    evaluate_production_readiness,
)


def _conn(rows: list[tuple[str, str, str]]) -> sqlite3.Connection:
    """rows: (shot_id, character, asset_path)"""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE assets (id TEXT PRIMARY KEY, path TEXT)")
    conn.execute(
        "CREATE TABLE shots (id TEXT PRIMARY KEY, character TEXT, asset_id TEXT)"
    )
    seen = {}
    for shot_id, character, path in rows:
        asset_id = seen.setdefault(path, f"a{len(seen)}")
        conn.execute("INSERT OR IGNORE INTO assets VALUES (?,?)", (asset_id, path))
        conn.execute("INSERT INTO shots VALUES (?,?,?)", (shot_id, character, asset_id))
    return conn


def _kimetsu(index: int) -> str:
    return f"/lib/sources/kimetsu/s2/kimetsu_s2_ep06.mkv"


def _jjk(index: int) -> str:
    return f"/lib/sources/jujutsukaisen/s2/jjk_s2_ep17.mkv"


def test_unresolvable_character_blocks_the_cut():
    """A name the tagger never learned resolves to nothing — that must stop us."""
    rows = [(f"s{i}", "", _kimetsu(i)) for i in range(30)]
    report = evaluate_production_readiness(
        _conn(rows), project_id="p", character="pink_hair",
        candidate_shot_ids=[f"s{i}" for i in range(30)],
    )
    assert not report.ready
    failed = {check.name for check in report.blocking}
    assert "character_identified" in failed
    remedy = next(c for c in report.checks if c.name == "character_identified").remedy
    assert remedy and "Appearance Catalog" in remedy


def test_cross_work_pool_blocks_the_cut():
    """This is the actual defect: 10 of 26 shots came from a different anime."""
    rows = [(f"k{i}", "akaza", _kimetsu(i)) for i in range(16)]
    rows += [(f"j{i}", "akaza", _jjk(i)) for i in range(10)]
    ids = [row[0] for row in rows]
    report = evaluate_production_readiness(
        _conn(rows), project_id="p", character="akaza", candidate_shot_ids=ids
    )
    assert not report.ready
    check = next(c for c in report.checks if c.name == "single_work_pool")
    assert not check.passed
    assert "jujutsukaisen" in check.detail


def test_identified_single_work_pool_passes():
    rows = [(f"k{i}", "akaza", _kimetsu(i)) for i in range(MIN_IDENTIFIED_SHOTS + 4)]
    report = evaluate_production_readiness(
        _conn(rows), project_id="p", character="akaza",
        candidate_shot_ids=[row[0] for row in rows],
    )
    assert report.ready, [c.detail for c in report.blocking]


def test_a_few_stray_shots_do_not_block():
    """The bar is a dominant work, not a pure one — one stray is not a wrong film."""
    rows = [(f"k{i}", "akaza", _kimetsu(i)) for i in range(19)]
    rows += [("j0", "akaza", _jjk(0))]
    report = evaluate_production_readiness(
        _conn(rows), project_id="p", character="akaza",
        candidate_shot_ids=[row[0] for row in rows],
    )
    assert report.ready


def test_not_ready_error_names_every_blocking_reason():
    rows = [(f"j{i}", "", _jjk(i)) for i in range(20)]
    report = evaluate_production_readiness(
        _conn(rows), project_id="p", character="ghost",
        candidate_shot_ids=[row[0] for row in rows],
    )
    with pytest.raises(ProductionNotReady) as excinfo:
        raise ProductionNotReady(report)
    message = str(excinfo.value)
    assert "character_identified" in message
    assert excinfo.value.report is report


def test_no_character_named_skips_the_identity_check():
    """A montage brief with no target character is legitimate."""
    rows = [(f"k{i}", "", _kimetsu(i)) for i in range(20)]
    report = evaluate_production_readiness(
        _conn(rows), project_id="p", character=None,
        candidate_shot_ids=[row[0] for row in rows],
    )
    assert report.ready
    assert {check.name for check in report.checks} == {"single_work_pool"}
