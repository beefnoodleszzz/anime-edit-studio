import sqlite3

import pytest

from studio.core.state import (
    StateTransitionError,
    WorkflowState,
    current_state,
    fail,
    transition,
)


@pytest.fixture
def conn():
    value = sqlite3.connect(":memory:")
    yield value
    value.close()


def test_new_project_must_start_created(conn):
    with pytest.raises(StateTransitionError):
        transition(conn, "p", WorkflowState.INGESTING)
    assert transition(conn, "p", WorkflowState.CREATED).state == "CREATED"


def test_happy_path_is_persisted(conn):
    transition(conn, "p", WorkflowState.CREATED)
    transition(conn, "p", WorkflowState.INGESTING, payload={"assets": 2})
    record = transition(conn, "p", WorkflowState.ANALYZED, duration_ms=123)
    assert record.payload == {}
    assert record.duration_ms == 123
    assert current_state(conn, "p").state == "ANALYZED"


def test_illegal_skip_is_rejected(conn):
    transition(conn, "p", WorkflowState.CREATED)
    with pytest.raises(StateTransitionError, match="非法"):
        transition(conn, "p", WorkflowState.LOCKED)


def test_retry_increments_attempt(conn):
    transition(conn, "p", WorkflowState.CREATED)
    transition(conn, "p", WorkflowState.INGESTING)
    assert transition(conn, "p", WorkflowState.INGESTING).attempt == 2


def test_failure_retains_diagnostics_and_can_retry(conn):
    transition(conn, "p", WorkflowState.CREATED)
    transition(conn, "p", WorkflowState.INGESTING)
    failed = fail(conn, "p", error="decoder crashed", payload={"asset": "a"})
    assert failed.state == "FAILED_INGESTING"
    assert failed.payload == {"asset": "a", "error": "decoder crashed"}
    retried = transition(conn, "p", WorkflowState.INGESTING)
    assert retried.attempt == 2


def test_revision_loop_is_legal(conn):
    states = [
        WorkflowState.CREATED,
        WorkflowState.INGESTING,
        WorkflowState.ANALYZED,
        WorkflowState.DIRECTING,
        WorkflowState.CANDIDATES_READY,
        WorkflowState.USER_SELECTION,
        WorkflowState.EDIT_PLANNING,
        WorkflowState.RESOLVE_BUILD,
        WorkflowState.PREVIEW_RENDER,
        WorkflowState.USER_REVIEW,
        WorkflowState.REVISION,
        WorkflowState.RESOLVE_BUILD,
    ]
    for state in states:
        transition(conn, "p", state)
    assert current_state(conn, "p").state == "RESOLVE_BUILD"


def test_delivered_requires_technical_qa_gate(conn):
    states = [
        WorkflowState.CREATED,
        WorkflowState.INGESTING,
        WorkflowState.ANALYZED,
        WorkflowState.DIRECTING,
        WorkflowState.CANDIDATES_READY,
        WorkflowState.USER_SELECTION,
        WorkflowState.EDIT_PLANNING,
        WorkflowState.RESOLVE_BUILD,
        WorkflowState.PREVIEW_RENDER,
        WorkflowState.USER_REVIEW,
        WorkflowState.LOCKED,
        WorkflowState.MASTER_RENDER,
        WorkflowState.FINAL_QA,
    ]
    for state in states:
        transition(conn, "p", state)
    with pytest.raises(StateTransitionError, match="Technical QA"):
        transition(conn, "p", WorkflowState.DELIVERED)
    record = transition(
        conn,
        "p",
        WorkflowState.DELIVERED,
        payload={"technical_qa_passed": True, "qa_result_id": 7},
    )
    assert record.state == "DELIVERED"
