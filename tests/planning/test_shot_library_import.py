from __future__ import annotations

import sqlite3

import pytest

from studio.core.database import connect
from studio.planning.shot_library_import import import_shot_manifest, resolve_shot_ids


def _make_catalog(path, *, source_exists: bool):
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE assets (
          id TEXT PRIMARY KEY, path TEXT, sha256 TEXT, anime TEXT,
          duration_sec REAL, width INTEGER, height INTEGER, fps REAL
        );
        CREATE TABLE shots (
          id TEXT PRIMARY KEY, asset_id TEXT, start_sec REAL, end_sec REAL,
          archive_path TEXT, archive_checksum TEXT
        );
        CREATE TABLE tags (
          shot_id TEXT, category TEXT, value TEXT, confidence REAL
        );
        """
    )
    source_path = path.parent / "episode01.mp4"
    if source_exists:
        source_path.write_bytes(b"fake video bytes")
    conn.execute(
        "INSERT INTO assets VALUES ('a1',?,?,?,?,?,?,?)",
        (str(source_path), "deadbeefcafe0011", "鬼灭之刃", 1440.0, 1920, 1080, 23.976),
    )
    conn.execute(
        "INSERT INTO shots VALUES ('shot-1','a1',10.0,12.5,?,?)",
        (None, None),
    )
    conn.execute(
        "INSERT INTO tags VALUES ('shot-1','character','tanjirou',0.98)"
    )
    conn.commit()
    conn.close()
    return source_path


def test_resolve_shot_ids_uses_source_asset_when_present(tmp_path):
    catalog = tmp_path / "catalog.sqlite"
    _make_catalog(catalog, source_exists=True)

    manifest = resolve_shot_ids(["shot-1"], catalog)

    assert len(manifest) == 1
    entry = manifest[0]
    assert entry.source_shot_id == "shot-1"
    assert entry.start_sec == 10.0
    assert entry.end_sec == 12.5
    assert entry.character == "tanjirou"
    assert entry.series == "鬼灭之刃"
    assert entry.media_path.name == "episode01.mp4"


def test_resolve_shot_ids_raises_for_unknown_id(tmp_path):
    catalog = tmp_path / "catalog.sqlite"
    _make_catalog(catalog, source_exists=True)

    with pytest.raises(ValueError):
        resolve_shot_ids(["does-not-exist"], catalog)


def test_resolve_shot_ids_raises_when_neither_source_nor_archive_exists(tmp_path):
    catalog = tmp_path / "catalog.sqlite"
    _make_catalog(catalog, source_exists=False)

    with pytest.raises(FileNotFoundError):
        resolve_shot_ids(["shot-1"], catalog)


def test_resolve_shot_ids_falls_back_to_archive_path_relative_to_media_root(tmp_path):
    # shots.archive_path is stored relative to the shot-library's own
    # media/ root (e.g. "archive/<id>/<shot>.mkv"), matching the real
    # layout: <root>/data/catalog.sqlite + <root>/media/archive/... — not
    # absolute like assets.path. A prior bug resolved it against the
    # current working directory instead and silently fell through to
    # FileNotFoundError even when the archive file genuinely existed.
    root = tmp_path / "anime-shot-library"
    data_dir = root / "data"
    data_dir.mkdir(parents=True)
    catalog = data_dir / "catalog.sqlite"
    _make_catalog(catalog, source_exists=False)

    archive_dir = root / "media" / "archive" / "grp1"
    archive_dir.mkdir(parents=True)
    (archive_dir / "shot-1.mkv").write_bytes(b"fake archive bytes")

    conn = sqlite3.connect(catalog)
    conn.execute(
        "UPDATE shots SET archive_path=?, archive_checksum=? WHERE id='shot-1'",
        ("archive/grp1/shot-1.mkv", "deadbeef"),
    )
    conn.commit()
    conn.close()

    manifest = resolve_shot_ids(["shot-1"], catalog)

    assert len(manifest) == 1
    entry = manifest[0]
    assert entry.media_path == archive_dir / "shot-1.mkv"
    assert entry.start_sec == 0.0
    assert entry.end_sec == pytest.approx(2.5)
    assert entry.sha256 == "deadbeef"


def test_import_shot_manifest_round_trips_into_studio_db(tmp_path):
    catalog = tmp_path / "catalog.sqlite"
    _make_catalog(catalog, source_exists=True)
    manifest = resolve_shot_ids(["shot-1"], catalog)

    conn = connect(tmp_path / "engine.v2.sqlite")
    asset_ids = import_shot_manifest(conn, manifest)

    assert len(asset_ids) == 1
    row = conn.execute("SELECT id,start_sec,end_sec,character,series FROM shots WHERE id='shot-1'").fetchone()
    assert row["start_sec"] == 10.0
    assert row["end_sec"] == 12.5
    assert row["character"] == "tanjirou"
    assert row["series"] == "鬼灭之刃"

    asset_row = conn.execute("SELECT fps_num,fps_den FROM assets WHERE id=?", (asset_ids[0],)).fetchone()
    assert asset_row["fps_num"] / asset_row["fps_den"] == pytest.approx(23.976, abs=0.01)

    # Re-importing the same manifest is idempotent.
    import_shot_manifest(conn, manifest)
    count = conn.execute("SELECT COUNT(*) FROM shots WHERE id='shot-1'").fetchone()[0]
    assert count == 1
    conn.close()
