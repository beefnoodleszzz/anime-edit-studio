"""Resolve-only render orchestration and persistent render records."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from studio.core.hashing import stable_hash
from studio.core.timecode import Timebase
from studio.editspec.schema import EditSpec
from studio.execution.resolve import RenderResult, ResolveAdapter

PRESETS = {"preview": "H.264 Master", "master": "H.265 Master"}
EXECUTION_PIPELINE_VERSION = "resolve-execution-2.1.0"


def render_flattened_master(
    adapter: ResolveAdapter,
    *,
    project_id: str,
    revision: int,
    media: Path,
    duration_sec: float,
    timebase: Timebase,
    width: int,
    height: int,
    output_dir: Path,
) -> RenderResult:
    """Return a post-processed locked picture to Resolve for final delivery.

    The input is a picture-locked, audio-bearing mezzanine (for example the
    Real-ESRGAN result).  Resolve remains the final renderer: picture and audio
    are appended explicitly so source audio cannot leak in through P19.
    """
    if not media.is_file():
        raise FileNotFoundError(media)
    adapter.ensure_project(
        f"{project_id}-delivery",
        timebase=timebase,
        width=width,
        height=height,
    )
    adapter.ensure_timeline("master", reset=True)
    info = adapter.import_media([media], bin_name="master")[str(media)]
    request = {
        "media_path": media,
        "source_in_sec": 0.0,
        "source_out_sec": duration_sec,
        "timeline_in_sec": 0.0,
        "track_index": 1,
        "media_fps": info.fps,
        "timeline_fps": timebase,
    }
    adapter.append_clips([{**request, "media_type": 1}])
    adapter.append_audio([request])
    return adapter.render(
        output_dir=output_dir,
        name=(
            f"{project_id}-r{revision}-"
            f"{EXECUTION_PIPELINE_VERSION}-4k-master"
        ),
        preset=PRESETS["master"],
    )


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
        mark_in, mark_out = adapter.timeline_frame_range(
            duration_sec=spec.duration_sec,
            timebase=Timebase(
                spec.timebase.num,
                spec.timebase.den,
                drop_frame=spec.timebase.drop_frame,
            ),
        )
        result = adapter.render(
            output_dir=output_dir,
            name=f"{spec.id}-{'preview' if kind == 'preview' else 'release'}",
            preset=preset or PRESETS[kind],
            mark_in=mark_in,
            mark_out=mark_out,
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
