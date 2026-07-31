"""Assemble an AMVSpec from TimelineSlots + GlobalSequencePlanner choices + MotionPlanner curves.

This is the final planning step (REFACTOR.md §4): everything upstream
(RhythmStyleMapper, GlobalSequencePlanner, MotionPlanner) produces
intermediate structures; this module is the only place that constructs the
AMVSpec object the ResolveCompiler consumes.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from studio.planning.global_sequence_planner import SequenceChoice
from studio.planning.motion_planner import build_clip_motion, build_transition_pair, direction_vector_for
from studio.planning.slots import TimelineSlot
from studio.spec.amv import (
    AMVSpec,
    Canvas,
    Clip,
    InputHashes,
    MusicRef,
    RenderSettings,
    SourceRange,
    Timebase,
    TimelinePlacement,
)
from studio.spec.music_timeline import MusicTimeline


_SHOT_BOUNDS_EPS = 1e-3


def _validate_source_within_shot(conn: sqlite3.Connection, choice: SequenceChoice) -> None:
    """REFACTOR.md §17: "SourceRange 必须位于原 Shot 内". The selector already
    generates windows within their Shot's bounds; this is a defensive check
    against a stale/corrupt choice, not where that guarantee is enforced."""
    row = conn.execute(
        "SELECT start_sec,end_sec FROM shots WHERE id=?", (choice.shot_id,)
    ).fetchone()
    if row is None:
        raise ValueError(f"unknown shot_id referenced by planner: {choice.shot_id}")
    shot_start, shot_end = row["start_sec"], row["end_sec"]
    if (
        choice.source_in_sec < shot_start - _SHOT_BOUNDS_EPS
        or choice.source_out_sec > shot_end + _SHOT_BOUNDS_EPS
    ):
        raise ValueError(
            f"choice for shot {choice.shot_id} has source range "
            f"[{choice.source_in_sec},{choice.source_out_sec}] outside shot bounds "
            f"[{shot_start},{shot_end}]"
        )


def build_amv_spec(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    slots: list[TimelineSlot],
    choices: list[SequenceChoice],
    canvas: Canvas,
    timebase: Timebase,
    music: MusicTimeline,
    music_path: Path,
    demo_hash: str,
    materials_index_hash: str,
    output_path: Path,
) -> AMVSpec:
    conn.row_factory = sqlite3.Row
    if len(slots) != len(choices):
        raise ValueError("slots and choices must be the same length and order")

    clips: list[Clip] = []
    transition_pairs = []
    cursor_sec = 0.0
    previous_clip_id: str | None = None

    for slot, choice in zip(slots, choices):
        if not choice.shot_id:
            cursor_sec += slot.duration_sec
            previous_clip_id = None
            continue
        _validate_source_within_shot(conn, choice)
        clip_id = f"c{slot.index}"
        clip = Clip(
            id=clip_id,
            asset_id=choice.asset_id,
            shot_id=choice.shot_id,
            window_id=choice.window_id or None,
            window_kind=choice.window_kind,
            anchor_sec=choice.anchor_sec,
            source=SourceRange(in_sec=choice.source_in_sec, out_sec=choice.source_out_sec),
            timeline=TimelinePlacement(in_sec=cursor_sec, duration_sec=slot.duration_sec),
            motion=build_clip_motion(slot, canvas, direction=direction_vector_for(slot.entry_motion)),
        )
        clips.append(clip)

        if previous_clip_id is not None and slot.entry_motion != "none":
            transition_pairs.append(
                build_transition_pair(
                    pair_id=f"t{slot.index}",
                    cut_sec=cursor_sec,
                    outgoing_clip_id=previous_clip_id,
                    incoming_clip_id=clip_id,
                    entry_motion=slot.entry_motion,
                    canvas=canvas,
                    confidence=0.6,
                )
            )
        previous_clip_id = clip_id
        cursor_sec += slot.duration_sec

    return AMVSpec(
        id=project_id,
        input_hashes=InputHashes(
            demo=demo_hash, music=music.source_hash, materials_index=materials_index_hash,
        ),
        timebase=timebase,
        canvas=canvas,
        duration_sec=cursor_sec,
        music=MusicRef(path=str(music_path), timeline_hash=music.source_hash),
        clips=clips,
        transition_pairs=transition_pairs,
        render=RenderSettings(output_path=str(output_path)),
    )


__all__ = ["build_amv_spec"]
