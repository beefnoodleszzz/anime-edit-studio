import json

from studio.core.database import connect
from studio.creative.preference import record_diff_feedback, record_final_survival
from studio.editspec.diff import EditSpecDiff, PatchClip, apply_diff
from studio.editspec.schema import (
    Canvas,
    Clip,
    EditSpec,
    SourceRange,
    Timebase,
    TimelinePlacement,
)


def _spec() -> EditSpec:
    return EditSpec(
        id="feedback-project",
        timebase=Timebase(num=24),
        canvas=Canvas(width=1080, height=1350),
        clips=[
            Clip(
                id="opening",
                asset_id="asset",
                shot_id="shot-a",
                source=SourceRange(in_sec=0, out_sec=1),
                timeline=TimelinePlacement(in_sec=0, duration_sec=1),
            ),
            Clip(
                id="ending",
                asset_id="asset",
                shot_id="shot-b",
                source=SourceRange(in_sec=1, out_sec=2),
                timeline=TimelinePlacement(in_sec=1, duration_sec=1),
            ),
        ],
    )


def test_records_applied_revision_and_final_survival(tmp_path):
    conn = connect(tmp_path / "db.sqlite")
    first = _spec()
    patch = EditSpecDiff(
        from_version=1,
        to_version=2,
        source="user",
        ops=[PatchClip(clip_id="ending", path="framing.offset_x", value=0.2)],
    )
    revised = apply_diff(first, patch)
    assert record_diff_feedback(
        conn,
        project_id=first.id,
        before=first,
        after=revised,
        diff=patch,
        source="user",
    ) == 1
    assert record_final_survival(
        conn, project_id=first.id, first_cut=first, final=revised
    ) == 2

    rows = conn.execute(
        "SELECT kind,clip_id,before_json,after_json FROM feedback_events ORDER BY id"
    ).fetchall()
    assert [row["kind"] for row in rows] == ["reframe", "survival", "survival"]
    assert json.loads(rows[0]["before_json"])["framing"]["offset_x"] == 0
    assert json.loads(rows[0]["after_json"])["framing"]["offset_x"] == 0.2
    assert json.loads(rows[1]["after_json"])["survived"] is True
