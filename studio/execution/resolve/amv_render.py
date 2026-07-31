"""Place an AMVSpec on a Resolve timeline and render it (REFACTOR.md §4, §15).

This is the only module that turns an ``AMVSpec`` into an actual Resolve
timeline: it resolves each clip's asset to media, places clips/audio with
the already-verified ``ResolveAdapter.append_clips``/``append_audio`` frame
math, hands every clip to the single Fusion compiler
(:mod:`studio.execution.amv_compiler`), and renders. No other module is
allowed to place AMVSpec clips on a timeline.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from studio.core.timecode import Timebase as CoreTimebase
from studio.execution.amv_compiler import compile_amv_spec
from studio.execution.resolve.adapter import RenderResult, ResolveAdapter
from studio.execution.resolve.fusion_program import FusionProgram, comp_name_for
from studio.spec.amv import AMVSpec

EXPECTED_NODES = {"BaseTransform", "MotionTransform", "PostColor"}


def _asset_media(conn: sqlite3.Connection, asset_id: str) -> tuple[Path, CoreTimebase]:
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT path,proxy_path,fps_num,fps_den FROM assets WHERE id=?", (asset_id,)
    ).fetchone()
    if row is None:
        raise ValueError(f"AMVSpec references an asset not present in the database: {asset_id}")
    media = Path(row["proxy_path"] or row["path"])
    return media, CoreTimebase(row["fps_num"], row["fps_den"])


def build_amv_timeline(
    adapter: ResolveAdapter,
    spec: AMVSpec,
    conn: sqlite3.Connection,
    *,
    project_name: str,
    timeline_name: str = "main",
    reset: bool = False,
) -> dict[str, object]:
    """Place every AMVSpec clip and the music track; return TimelineItems by clip id."""
    timebase = CoreTimebase(spec.timebase.num, spec.timebase.den)
    adapter.ensure_project(
        project_name, timebase=timebase, width=spec.canvas.width, height=spec.canvas.height,
        reset=reset,
    )
    adapter.ensure_timeline(timeline_name, reset=reset)

    media_by_clip = {clip.id: _asset_media(conn, clip.asset_id) for clip in spec.clips}
    adapter.import_media(sorted({media for media, _fps in media_by_clip.values()}))

    requests = [
        {
            "media_path": media_by_clip[clip.id][0],
            "source_in_sec": clip.source.in_sec,
            "source_out_sec": clip.source.out_sec,
            "timeline_in_sec": clip.timeline.in_sec,
            "timeline_duration_sec": clip.timeline.duration_sec,
            "track_index": 1,
            "media_fps": media_by_clip[clip.id][1],
            "timeline_fps": timebase,
            "media_type": 1,  # video only — source audio must not leak in unannounced
        }
        for clip in spec.clips
    ]
    items = adapter.append_clips(requests) if requests else []
    items_by_clip_id: dict[str, object] = {}
    for clip, item in zip(spec.clips, items):
        adapter.mark_clip(item, clip.id)
        items_by_clip_id[clip.id] = item

    music_path = Path(spec.music.path)
    adapter.import_media([music_path])
    adapter.append_audio(
        [
            {
                "media_path": music_path,
                "source_in_sec": 0.0,
                "source_out_sec": spec.duration_sec,
                "timeline_in_sec": 0.0,
                "timeline_duration_sec": spec.duration_sec,
                "track_index": 1,
                "media_fps": timebase,
                "timeline_fps": timebase,
            }
        ]
    )
    return items_by_clip_id


def verify_fusion_graph_consistency(programs: dict[str, FusionProgram]) -> bool:
    """Structural readback (REFACTOR.md §10 rule 6-7): every clip got its own,
    correctly-named comp with the fixed node chain — nothing skipped, nothing
    collapsed by a mass-delete fallback."""
    for clip_id, program in programs.items():
        if program.comp is None:
            return False
        if program.comp_name != comp_name_for(clip_id):
            return False
        if not EXPECTED_NODES.issubset(set(program.node_names)):
            return False
    return True


def render_amv(
    adapter: ResolveAdapter,
    spec: AMVSpec,
    items_by_clip_id: dict[str, object],
    *,
    output_dir: Path,
    name: str,
    mark_in_sec: float | None = None,
    mark_out_sec: float | None = None,
) -> tuple[RenderResult, bool]:
    """Compile every clip's Fusion program, verify it, then render.

    Returns ``(render_result, fusion_graph_consistent)`` — the caller (QA)
    must not treat a render as passable when the second value is False.
    """
    programs = compile_amv_spec(adapter, spec, items_by_clip_id)
    consistent = verify_fusion_graph_consistency(programs)

    timebase = CoreTimebase(spec.timebase.num, spec.timebase.den)
    mark_in = timebase.to_frames(mark_in_sec) if mark_in_sec is not None else None
    mark_out = timebase.to_frames(mark_out_sec) if mark_out_sec is not None else None
    result = adapter.render(
        output_dir=output_dir, name=name, preset="H.264 Master",
        mark_in=mark_in, mark_out=mark_out,
    )
    return result, consistent


__all__ = ["build_amv_timeline", "render_amv", "verify_fusion_graph_consistency"]
