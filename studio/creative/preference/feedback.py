"""Persist auditable revision and final-survival preference signals."""
from __future__ import annotations

import json
import sqlite3
from typing import Literal

from studio.editspec.diff import EditSpecDiff, PatchClip, ReplaceClip
from studio.editspec.schema import Clip, EditSpec


def _kind(op: ReplaceClip | PatchClip) -> str:
    if isinstance(op, ReplaceClip):
        return "replacement"
    root = op.path.split(".", 1)[0]
    return {
        "timeline": "timing",
        "source": "timing",
        "retime": "timing",
        "effects": "effect",
        "color": "effect",
        "audio": "audio",
        "framing": "reframe",
        "camera": "reframe",
    }.get(root, "effect")


def record_diff_feedback(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    before: EditSpec,
    after: EditSpec,
    diff: EditSpecDiff,
    source: Literal["user", "critic"],
) -> int:
    """Record only executable operations, never un-applied LLM suggestions."""
    if before.id != project_id or after.id != project_id:
        raise ValueError("feedback project_id 与 EditSpec 不一致")
    if diff.from_version != before.revision or diff.to_version != after.revision:
        raise ValueError("feedback diff revision 与 EditSpec 不一致")
    old = {clip.id: clip for clip in before.clips}
    new = {clip.id: clip for clip in after.clips}
    events = []
    for op in diff.ops:
        if not isinstance(op, (ReplaceClip, PatchClip)):
            continue
        clip_id = op.clip_id
        if clip_id not in old or clip_id not in new:
            continue
        events.append(
            (
                project_id,
                after.revision,
                _kind(op),
                clip_id,
                old[clip_id].model_dump_json(by_alias=True),
                new[clip_id].model_dump_json(by_alias=True),
                source,
            )
        )
    with conn:
        conn.executemany(
            """
            INSERT INTO feedback_events(
              project_id,spec_version,kind,clip_id,before_json,after_json,source
            ) VALUES (?,?,?,?,?,?,?)
            """,
            events,
        )
    return len(events)


def record_final_survival(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    first_cut: EditSpec,
    final: EditSpec,
) -> int:
    """Record whether each first-cut shot survived into the locked picture."""
    if first_cut.id != project_id or final.id != project_id:
        raise ValueError("survival project_id 与 EditSpec 不一致")
    final_shots = {clip.shot_id for clip in final.clips if clip.shot_id}
    events = [
        (
            project_id,
            final.revision,
            "survival",
            clip.id,
            json.dumps(
                {"shot_id": clip.shot_id, "role": clip.role},
                ensure_ascii=False,
                sort_keys=True,
            ),
            json.dumps(
                {"survived": bool(clip.shot_id and clip.shot_id in final_shots)},
                sort_keys=True,
            ),
            "user",
        )
        for clip in first_cut.clips
    ]
    with conn:
        conn.executemany(
            """
            INSERT INTO feedback_events(
              project_id,spec_version,kind,clip_id,before_json,after_json,source
            ) VALUES (?,?,?,?,?,?,?)
            """,
            events,
        )
    return len(events)


__all__ = ["record_diff_feedback", "record_final_survival"]
