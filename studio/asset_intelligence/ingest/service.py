"""Deterministic asset intake into the v2 catalog."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from studio.core.database import DEFAULT_V2_DB, connect
from studio.core.hashing import file_sha256
from studio.execution.ffmpeg import create_proxy, probe_media


def ingest_asset(
    source: Path,
    *,
    database: Path = DEFAULT_V2_DB,
    proxies_dir: Path | None = None,
) -> dict:
    source = source.expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    digest = file_sha256(source)
    asset_id = digest[:12]
    probe = probe_media(source)
    proxy_root = proxies_dir or database.parent / "proxies"
    proxy = create_proxy(source, proxy_root / f"{asset_id}.mp4")
    conn = connect(database)
    with conn:
        conn.execute(
            """
            INSERT INTO assets(
              id,path,sha256,width,height,fps_num,fps_den,duration_sec,codec,proxy_path
            ) VALUES (?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
              path=excluded.path,sha256=excluded.sha256,
              width=excluded.width,height=excluded.height,
              fps_num=excluded.fps_num,fps_den=excluded.fps_den,
              duration_sec=excluded.duration_sec,codec=excluded.codec,
              proxy_path=excluded.proxy_path
            """,
            (
                asset_id, str(source), digest, probe.width, probe.height,
                probe.fps_num, probe.fps_den, probe.duration_sec, probe.codec,
                str(proxy),
            ),
        )
    conn.close()
    return {
        "id": asset_id,
        "path": str(source),
        "sha256": digest,
        "width": probe.width,
        "height": probe.height,
        "fps": {"num": probe.fps_num, "den": probe.fps_den},
        "duration_sec": probe.duration_sec,
        "codec": probe.codec,
        "has_audio": probe.has_audio,
        "proxy_path": str(proxy),
    }


__all__ = ["ingest_asset"]
