from studio.core.database import connect
from studio.core.kpi import project_kpis
from studio.core.state import WorkflowState, transition
from studio.editspec.schema import (
    Clip,
    Canvas,
    EditSpec,
    SourceRange,
    TimelinePlacement,
    Timebase,
)


def _spec(project: str, revision: int, shots: list[str]) -> EditSpec:
    return EditSpec(
        id=project,
        revision=revision,
        timebase=Timebase(num=24, den=1),
        canvas=Canvas(width=1080, height=1350, aspect="4:5"),
        clips=[
            Clip(
                id=f"c{index}",
                asset_id="a",
                shot_id=shot,
                source=SourceRange(in_sec=index, out_sec=index + 1),
                timeline=TimelinePlacement(in_sec=index, duration_sec=1, track="V1"),
            )
            for index, shot in enumerate(shots)
        ],
    )


def test_kpis_distinguish_missing_evidence_and_ai_delegation(tmp_path):
    conn = connect(tmp_path / "db.sqlite")
    with conn:
        conn.execute(
            "INSERT INTO candidate_groups("
            "id,project_id,role,shot_ids_json,selected_shot_id,selection_source"
            ") VALUES ('g','p','opening','[\"a\",\"b\",\"c\"]','a','ai')"
        )
    result = project_kpis(conn, "p")
    assert result["candidate_precision"]["value"] is None
    assert result["candidate_precision"]["status"] == "insufficient_data"
    assert result["first_cut_survival_rate"]["status"] == "insufficient_data"
    assert result["technical_qa_pass_rate"]["status"] == "insufficient_data"


def test_locked_project_computes_edit_metrics(tmp_path):
    conn = connect(tmp_path / "db.sqlite")
    first = _spec("p", 1, ["s0", "s1", "s2"])
    final = _spec("p", 2, ["s0", "s1", "s3"])
    with conn:
        conn.executemany(
            """
            INSERT INTO edit_specs(project_id,version,spec_json,parent_version,created_by)
            VALUES (?,?,?,?,?)
            """,
            [
                ("p", 1, first.model_dump_json(by_alias=True), None, "rule"),
                ("p", 2, final.model_dump_json(by_alias=True), 1, "user"),
            ],
        )
    transition(conn, "p", WorkflowState.CREATED)
    # State history is evidence of owner lock even when testing without Resolve.
    conn.execute(
        "INSERT INTO workflow_states(project_id,state,payload_json) "
        "VALUES ('p','LOCKED','{}')"
    )
    conn.commit()
    result = project_kpis(conn, "p")
    assert result["first_cut_survival_rate"]["value"] == 2 / 3
    assert result["first_cut_survival_rate"]["status"] == "pass"
    assert result["sequence_preservation"]["value"] == 2 / 3
    assert result["sequence_preservation"]["status"] == "fail"
    assert result["timing_delta"]["value"] == 0
