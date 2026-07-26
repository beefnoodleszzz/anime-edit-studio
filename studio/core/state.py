"""Persistent deterministic workflow state machine (TARGET §9).

State transitions are code, never prompt instructions.  Every successful or
failed step is appended to SQLite so a process can resume after interruption.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class WorkflowState(StrEnum):
    CREATED = "CREATED"
    INGESTING = "INGESTING"
    ANALYZED = "ANALYZED"
    DIRECTING = "DIRECTING"
    CANDIDATES_READY = "CANDIDATES_READY"
    USER_SELECTION = "USER_SELECTION"
    EDIT_PLANNING = "EDIT_PLANNING"
    RESOLVE_BUILD = "RESOLVE_BUILD"
    PREVIEW_RENDER = "PREVIEW_RENDER"
    AI_REVIEW = "AI_REVIEW"
    USER_REVIEW = "USER_REVIEW"
    REVISION = "REVISION"
    LOCKED = "LOCKED"
    MASTER_RENDER = "MASTER_RENDER"
    FINAL_QA = "FINAL_QA"
    DELIVERED = "DELIVERED"
    PUBLISHED = "PUBLISHED"
    METRICS_COLLECTED = "METRICS_COLLECTED"


_NEXT: dict[WorkflowState, set[WorkflowState]] = {
    WorkflowState.CREATED: {WorkflowState.INGESTING},
    WorkflowState.INGESTING: {WorkflowState.ANALYZED},
    WorkflowState.ANALYZED: {WorkflowState.DIRECTING},
    WorkflowState.DIRECTING: {WorkflowState.CANDIDATES_READY},
    WorkflowState.CANDIDATES_READY: {WorkflowState.USER_SELECTION},
    WorkflowState.USER_SELECTION: {WorkflowState.EDIT_PLANNING},
    WorkflowState.EDIT_PLANNING: {WorkflowState.RESOLVE_BUILD},
    WorkflowState.RESOLVE_BUILD: {WorkflowState.PREVIEW_RENDER},
    WorkflowState.PREVIEW_RENDER: {WorkflowState.AI_REVIEW, WorkflowState.USER_REVIEW},
    WorkflowState.AI_REVIEW: {WorkflowState.USER_REVIEW, WorkflowState.REVISION},
    WorkflowState.USER_REVIEW: {WorkflowState.REVISION, WorkflowState.LOCKED},
    WorkflowState.REVISION: {WorkflowState.RESOLVE_BUILD, WorkflowState.LOCKED},
    WorkflowState.LOCKED: {WorkflowState.MASTER_RENDER},
    WorkflowState.MASTER_RENDER: {WorkflowState.FINAL_QA},
    WorkflowState.FINAL_QA: {WorkflowState.DELIVERED, WorkflowState.MASTER_RENDER},
    WorkflowState.DELIVERED: {WorkflowState.PUBLISHED},
    WorkflowState.PUBLISHED: {WorkflowState.METRICS_COLLECTED},
    WorkflowState.METRICS_COLLECTED: set(),
}


class StateTransitionError(ValueError):
    pass


@dataclass(frozen=True)
class StateRecord:
    project_id: str
    state: str
    payload: dict[str, Any]
    entered_at: str
    duration_ms: int | None
    attempt: int

    @property
    def failed(self) -> bool:
        return self.state.startswith("FAILED_")


def ensure_state_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS workflow_states (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id   TEXT NOT NULL,
            state        TEXT NOT NULL,
            payload_json TEXT NOT NULL DEFAULT '{}',
            entered_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
            duration_ms  INTEGER CHECK(duration_ms IS NULL OR duration_ms >= 0),
            attempt      INTEGER NOT NULL DEFAULT 1 CHECK(attempt >= 1)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_workflow_project ON workflow_states(project_id, id)"
    )


def current_state(conn: sqlite3.Connection, project_id: str) -> StateRecord | None:
    ensure_state_schema(conn)
    row = conn.execute(
        """
        SELECT project_id,state,payload_json,entered_at,duration_ms,attempt
        FROM workflow_states WHERE project_id=? ORDER BY id DESC LIMIT 1
        """,
        (project_id,),
    ).fetchone()
    if row is None:
        return None
    return StateRecord(
        project_id=row[0],
        state=row[1],
        payload=json.loads(row[2]),
        entered_at=row[3],
        duration_ms=row[4],
        attempt=row[5],
    )


def _base_state(value: str) -> WorkflowState:
    raw = value.removeprefix("FAILED_")
    try:
        return WorkflowState(raw)
    except ValueError as exc:
        raise StateTransitionError(f"未知 workflow state: {value}") from exc


def transition(
    conn: sqlite3.Connection,
    project_id: str,
    target: WorkflowState,
    *,
    payload: dict[str, Any] | None = None,
    duration_ms: int | None = None,
) -> StateRecord:
    """Append a legal state transition.

    Re-entering the current state is an idempotent retry and increments attempt.
    A failed state may retry its base state or continue through the base state's
    normal outgoing transitions.
    """
    ensure_state_schema(conn)
    current = current_state(conn, project_id)
    attempt = 1
    if current is None:
        if target is not WorkflowState.CREATED:
            raise StateTransitionError("新项目必须从 CREATED 开始")
    else:
        base = _base_state(current.state)
        if target == base:
            attempt = current.attempt + 1
        elif target not in _NEXT[base]:
            raise StateTransitionError(f"非法状态转换: {current.state} → {target.value}")
    data = payload or {}
    if target is WorkflowState.DELIVERED and data.get("technical_qa_passed") is not True:
        raise StateTransitionError("Technical QA 未通过，禁止进入 DELIVERED")

    conn.execute(
        """
        INSERT INTO workflow_states(project_id,state,payload_json,duration_ms,attempt)
        VALUES (?,?,?,?,?)
        """,
        (
            project_id,
            target.value,
            json.dumps(data, ensure_ascii=False, sort_keys=True),
            duration_ms,
            attempt,
        ),
    )
    conn.commit()
    return current_state(conn, project_id)  # type: ignore[return-value]


def fail(
    conn: sqlite3.Connection,
    project_id: str,
    *,
    error: str,
    payload: dict[str, Any] | None = None,
    duration_ms: int | None = None,
) -> StateRecord:
    current = current_state(conn, project_id)
    if current is None:
        raise StateTransitionError("尚无状态，不能记录失败")
    base = _base_state(current.state)
    data = dict(payload or {})
    data["error"] = error
    conn.execute(
        """
        INSERT INTO workflow_states(project_id,state,payload_json,duration_ms,attempt)
        VALUES (?,?,?,?,?)
        """,
        (
            project_id,
            f"FAILED_{base.value}",
            json.dumps(data, ensure_ascii=False, sort_keys=True),
            duration_ms,
            current.attempt,
        ),
    )
    conn.commit()
    return current_state(conn, project_id)  # type: ignore[return-value]


__all__ = [
    "StateRecord",
    "StateTransitionError",
    "WorkflowState",
    "current_state",
    "ensure_state_schema",
    "fail",
    "transition",
]
