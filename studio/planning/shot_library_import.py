"""Resolve external anime-shot-library shot IDs into this studio's own DB.

Footage selection now happens entirely in anime-shot-library (镜头切分/打标
/人工精选，see its own ``catalog.sqlite``): the user hands over a list of
already-chosen shot IDs, and this module is the only bridge between the two
projects. It reads the shot-library catalog read-only and upserts the
minimal ``assets``/``shots`` rows the planning/execution layer needs — no
other module talks to the shot-library DB directly.
"""
from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path

DEFAULT_CATALOG_DB = Path.home() / "Desktop" / "anime-shot-library" / "data" / "catalog.sqlite"


def catalog_db_path() -> Path:
    override = os.environ.get("ANIME_SHOT_LIBRARY_DB")
    return Path(override) if override else DEFAULT_CATALOG_DB


@dataclass(frozen=True)
class ManifestShot:
    source_shot_id: str
    media_path: Path
    start_sec: float
    end_sec: float
    width: int | None
    height: int | None
    fps: float | None
    asset_duration_sec: float | None
    sha256: str | None
    character: str | None
    series: str | None
    tags: list[str] = field(default_factory=list)


def resolve_shot_ids(shot_ids: list[str], catalog_db: Path | None = None) -> list[ManifestShot]:
    """Look up each shot-library shot id and its parent asset.

    Prefers the original ingested video (``assets.path``) with the shot's
    own ``[start_sec, end_sec)``; if the source has since been deleted
    (shotvault's cold-storage workflow), falls back to the shot's own
    single-shot archive file (``shots.archive_path``), whose timeline is
    reset to start at 0 (shot-library's README: "重置镜头时间轴").
    """
    db = catalog_db or catalog_db_path()
    if not shot_ids:
        return []
    if not db.exists():
        raise FileNotFoundError(f"anime-shot-library catalog not found: {db}")

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        placeholders = ",".join("?" for _ in shot_ids)
        rows = conn.execute(
            f"""
            SELECT s.*, a.path AS asset_path, a.sha256 AS asset_sha256,
                   a.width AS asset_width, a.height AS asset_height,
                   a.fps AS asset_fps, a.duration_sec AS asset_duration_sec, a.anime AS anime
            FROM shots s JOIN assets a ON a.id = s.asset_id
            WHERE s.id IN ({placeholders})
            """,
            shot_ids,
        ).fetchall()
        found = {row["id"]: row for row in rows}
        missing = [shot_id for shot_id in shot_ids if shot_id not in found]
        if missing:
            raise ValueError(f"shot ids not found in anime-shot-library catalog: {missing}")

        manifest: list[ManifestShot] = []
        for shot_id in shot_ids:
            row = found[shot_id]
            tags = [
                tag_row["value"]
                for tag_row in conn.execute(
                    "SELECT value FROM tags WHERE shot_id=? AND category='character' ORDER BY confidence DESC",
                    (shot_id,),
                ).fetchall()
            ]
            source_path = Path(row["asset_path"]) if row["asset_path"] else None
            if source_path is not None and source_path.is_file():
                media_path, start_sec, end_sec = source_path, row["start_sec"], row["end_sec"]
                width, height, fps = row["asset_width"], row["asset_height"], row["asset_fps"]
                asset_duration_sec = row["asset_duration_sec"]
                sha256 = row["asset_sha256"]
            elif row["archive_path"] and Path(row["archive_path"]).is_file():
                archive = Path(row["archive_path"])
                media_path = archive
                start_sec, end_sec = 0.0, row["end_sec"] - row["start_sec"]
                width, height, fps = row["asset_width"], row["asset_height"], row["asset_fps"]
                asset_duration_sec = end_sec
                sha256 = row["archive_checksum"]
            else:
                raise FileNotFoundError(
                    f"shot {shot_id}: neither source asset nor archive is present on disk"
                )
            manifest.append(
                ManifestShot(
                    source_shot_id=shot_id,
                    media_path=media_path,
                    start_sec=float(start_sec),
                    end_sec=float(end_sec),
                    width=width, height=height, fps=fps,
                    asset_duration_sec=asset_duration_sec,
                    sha256=sha256,
                    character=tags[0] if tags else None,
                    series=row["anime"],
                    tags=tags,
                )
            )
        return manifest
    finally:
        conn.close()


def _fps_fraction(fps: float | None) -> tuple[int, int]:
    if not fps:
        return 24000, 1001
    frac = Fraction(fps).limit_denominator(1001)
    return frac.numerator, frac.denominator


def import_shot_manifest(conn: sqlite3.Connection, manifest: list[ManifestShot]) -> list[str]:
    """Upsert each manifest entry as one ``assets`` row + one ``shots`` row.

    One shot-library shot becomes one local asset (its own file already is
    exactly that clip's usable range) plus one shot spanning the whole
    thing, keeping the existing planning/execution SQL (which only ever
    reads ``shots``/``assets`` by id) untouched.
    """
    conn.row_factory = sqlite3.Row
    asset_ids: list[str] = []
    with conn:
        for entry in manifest:
            asset_id = entry.sha256[:12] if entry.sha256 else entry.source_shot_id
            fps_num, fps_den = _fps_fraction(entry.fps)
            conn.execute(
                """
                INSERT INTO assets(id, path, sha256, width, height, fps_num, fps_den, duration_sec)
                VALUES (?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                  path=excluded.path, sha256=excluded.sha256,
                  width=excluded.width, height=excluded.height,
                  fps_num=excluded.fps_num, fps_den=excluded.fps_den,
                  duration_sec=excluded.duration_sec
                """,
                (
                    asset_id, str(entry.media_path), entry.sha256 or "",
                    entry.width, entry.height, fps_num, fps_den,
                    entry.asset_duration_sec,
                ),
            )
            conn.execute(
                """
                INSERT INTO shots(id, asset_id, source_shot_id, start_sec, end_sec, character, series, tags_json)
                VALUES (?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                  asset_id=excluded.asset_id, source_shot_id=excluded.source_shot_id,
                  start_sec=excluded.start_sec, end_sec=excluded.end_sec,
                  character=excluded.character, series=excluded.series, tags_json=excluded.tags_json
                """,
                (
                    entry.source_shot_id, asset_id, entry.source_shot_id,
                    entry.start_sec, entry.end_sec, entry.character, entry.series,
                    json.dumps(entry.tags, ensure_ascii=False),
                ),
            )
            asset_ids.append(asset_id)
    return asset_ids


__all__ = [
    "DEFAULT_CATALOG_DB",
    "ManifestShot",
    "catalog_db_path",
    "import_shot_manifest",
    "resolve_shot_ids",
]
