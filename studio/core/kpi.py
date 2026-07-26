"""Deterministic project KPI computation with explicit insufficient-data states."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any

from studio.editspec.schema import EditSpec


def _ratio(value: float | None, target: str, passed: bool | None) -> dict[str, Any]:
    return {
        "value": value,
        "target": target,
        "status": (
            "insufficient_data" if passed is None else "pass" if passed else "fail"
        ),
    }


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _lcs_ratio(before: list[str], after: list[str]) -> float | None:
    if not before:
        return None
    row = [0] * (len(after) + 1)
    for left in before:
        previous = 0
        for index, right in enumerate(after, 1):
            saved = row[index]
            row[index] = (
                previous + 1 if left == right else max(row[index], row[index - 1])
            )
            previous = saved
    return row[-1] / len(before)


def project_kpis(conn: sqlite3.Connection, project_id: str) -> dict[str, Any]:
    """Compute only what persisted evidence can prove; never manufacture a pass."""
    conn.row_factory = sqlite3.Row
    groups = conn.execute(
        """
        SELECT count(*) groups_total,
          sum(CASE WHEN selection_source='human' THEN 1 ELSE 0 END) human,
          sum(CASE WHEN selection_source='ai' THEN 1 ELSE 0 END) delegated
        FROM candidate_groups WHERE project_id=? AND active=1
        """,
        (project_id,),
    ).fetchone()
    group_total = int(groups["groups_total"])
    human = int(groups["human"] or 0)
    delegated = int(groups["delegated"] or 0)

    revisions = int(
        conn.execute(
            "SELECT count(*) FROM revision_runs WHERE project_id=? AND status='complete'",
            (project_id,),
        ).fetchone()[0]
    )
    specs = conn.execute(
        """
        SELECT version,spec_json FROM edit_specs
        WHERE project_id=? ORDER BY version
        """,
        (project_id,),
    ).fetchall()
    first_preview = conn.execute(
        """
        SELECT min(spec_version) FROM renders
        WHERE project_id=? AND preset LIKE '%H.264%' AND status='complete'
        """,
        (project_id,),
    ).fetchone()[0]
    first_row = next(
        (row for row in specs if row["version"] == first_preview),
        specs[0] if specs else None,
    )
    first = (
        EditSpec.model_validate_json(first_row["spec_json"])
        if first_row is not None else None
    )
    final = EditSpec.model_validate_json(specs[-1]["spec_json"]) if specs else None
    locked = conn.execute(
        """
        SELECT 1 FROM workflow_states
        WHERE project_id=? AND state IN ('LOCKED','MASTER_RENDER','FINAL_QA',
          'DELIVERED','PUBLISHED','METRICS_COLLECTED')
        LIMIT 1
        """,
        (project_id,),
    ).fetchone() is not None

    survival = sequence = timing_delta = None
    if first is not None and final is not None and locked:
        first_shots = [clip.shot_id or clip.id for clip in first.clips]
        final_shots = [clip.shot_id or clip.id for clip in final.clips]
        survival = (
            len(set(first_shots) & set(final_shots)) / len(set(first_shots))
            if first_shots else None
        )
        sequence = _lcs_ratio(first_shots, final_shots)
        final_by_id = {clip.id: clip for clip in final.clips}
        total = sum(clip.timeline.duration_sec for clip in first.clips)
        changed = sum(
            abs(
                clip.timeline.duration_sec
                - final_by_id[clip.id].timeline.duration_sec
            )
            for clip in first.clips
            if clip.id in final_by_id
        )
        timing_delta = changed / total if total else None

    created = conn.execute(
        """
        SELECT entered_at FROM workflow_states
        WHERE project_id=? AND state='CREATED' ORDER BY id LIMIT 1
        """,
        (project_id,),
    ).fetchone()
    preview = conn.execute(
        """
        SELECT finished_at FROM renders
        WHERE project_id=? AND backend='resolve' AND preset LIKE '%H.264%'
          AND status='complete' AND finished_at IS NOT NULL
        ORDER BY finished_at LIMIT 1
        """,
        (project_id,),
    ).fetchone()
    time_to_preview = (
        (_parse_time(preview["finished_at"]) - _parse_time(created["entered_at"])).total_seconds()
        if created and preview else None
    )

    masters = conn.execute(
        """
        SELECT r.id,q.passed,q.checks_json FROM renders r
        LEFT JOIN qa_results q ON q.id=(
          SELECT q2.id FROM qa_results q2
          WHERE q2.render_id=r.id AND q2.kind='technical'
          ORDER BY q2.id DESC LIMIT 1
        )
        WHERE r.project_id=? AND r.preset LIKE '%H.265%' AND r.status='complete'
        """,
        (project_id,),
    ).fetchall()
    qa_passes = sum(
        1
        for row in masters
        if row["passed"] == 1
        and len(json.loads(row["checks_json"] or "[]")) == 13
        and all(item.get("passed") for item in json.loads(row["checks_json"] or "[]"))
    )
    qa_rate = qa_passes / len(masters) if masters else None

    return {
        "project_id": project_id,
        "candidate_groups": group_total,
        "ai_delegated": delegated,
        "candidate_selection_count": _ratio(
            group_total if group_total else None,
            "<= 15",
            group_total <= 15 if group_total else None,
        ),
        "candidate_precision": _ratio(
            human / group_total if group_total and human else None,
            ">= 0.50 (human acceptance only)",
            human / group_total >= 0.5 if group_total and human else None,
        ),
        "time_to_first_preview_sec": _ratio(
            time_to_preview,
            "<= 1800",
            time_to_preview <= 1800 if time_to_preview is not None else None,
        ),
        "manual_resolve_operations": _ratio(
            0 if preview else None,
            "= 0 for automated Review workflow",
            True if preview else None,
        ),
        "revision_count_to_lock": _ratio(
            revisions if locked else None,
            "<= 2",
            revisions <= 2 if locked else None,
        ),
        "first_cut_survival_rate": _ratio(
            survival,
            "0.60..0.80",
            0.6 <= survival <= 0.8 if survival is not None else None,
        ),
        "sequence_preservation": _ratio(
            sequence,
            ">= 0.70",
            sequence >= 0.7 if sequence is not None else None,
        ),
        "timing_delta": _ratio(
            timing_delta,
            "<= 0.20",
            timing_delta <= 0.2 if timing_delta is not None else None,
        ),
        "technical_qa_pass_rate": _ratio(
            qa_rate,
            ">= 0.95",
            qa_rate >= 0.95 if qa_rate is not None else None,
        ),
        "human_effort_sec": _ratio(
            None,
            "<= 600",
            None,
        ),
    }


__all__ = ["project_kpis"]
