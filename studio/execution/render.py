"""Resolve-only render orchestration and persistent render records."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from studio.core.hashing import stable_hash
from studio.editspec.schema import EditSpec
from studio.execution.resolve import RenderResult, ResolveAdapter

PRESETS = {"preview": "H.264 Master", "master": "H.265 Master"}
EXECUTION_PIPELINE_VERSION = "resolve-execution-2.1.0"


def render_spec(
    adapter: ResolveAdapter,
    conn: sqlite3.Connection,
    spec: EditSpec,
    *,
    kind: str,
    output_dir: Path,
    preset: str | None = None,
) -> tuple[str, RenderResult]:
    if kind not in PRESETS:
        raise ValueError("render kind 必须是 preview 或 master")
    render_id = "render-" + stable_hash(
        {
            "project": spec.id,
            "revision": spec.revision,
            "kind": kind,
            "preset": preset or PRESETS[kind],
            "execution_pipeline": EXECUTION_PIPELINE_VERSION,
        }
    )[:20]
    with conn:
        conn.execute(
            """
            INSERT INTO renders(
              id,project_id,spec_version,backend,preset,status,started_at
            ) VALUES (?,?,?,?,?,'running',strftime('%Y-%m-%dT%H:%M:%fZ','now'))
            ON CONFLICT(id) DO UPDATE SET
              status='running',started_at=excluded.started_at,error_json=NULL
            """,
            (render_id, spec.id, spec.revision, "resolve", preset or PRESETS[kind]),
        )
    try:
        result = adapter.render(
            output_dir=output_dir,
            name=(
                f"{spec.id}-r{spec.revision}-"
                f"{EXECUTION_PIPELINE_VERSION}-{kind}"
            ),
            preset=preset or PRESETS[kind],
        )
        with conn:
            conn.execute(
                """
                UPDATE renders SET output_path=?,duration_sec=?,status='complete',
                  finished_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')
                WHERE id=?
                """,
                (str(result.output), spec.duration_sec, render_id),
            )
        return render_id, result
    except Exception as exc:
        with conn:
            conn.execute(
                """
                UPDATE renders SET status='failed',error_json=?,
                  finished_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')
                WHERE id=?
                """,
                (json.dumps({"error": str(exc)}, ensure_ascii=False), render_id),
            )
        raise


__all__ = ["EXECUTION_PIPELINE_VERSION", "PRESETS", "render_spec"]
