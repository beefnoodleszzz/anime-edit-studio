from pathlib import Path

from studio.agents import LLMCall
from studio.core.database import connect
from studio.critic.creative import CreativeReview, RevisionIssue, SuggestedFix
from studio.editspec.schema import (
    Canvas,
    Clip,
    Decision,
    EditSpec,
    SourceRange,
    Timebase,
    TimelinePlacement,
)
from studio.workflows import revise_from_feedback


class FakeProvider:
    def generate(self, *, system, prompt, output_type):
        review = CreativeReview(
            summary="two changes",
            issues=[
                RevisionIssue(
                    kind="energy",
                    timeline_start_sec=0,
                    timeline_end_sec=1,
                    severity="medium",
                    reason="stronger",
                    confidence=0.9,
                    suggested_fix=SuggestedFix(
                        op="adjust_intensity",
                        clip_id="a",
                        requirements={"scale": 1.4},
                    ),
                ),
                RevisionIssue(
                    kind="ending",
                    timeline_start_sec=1,
                    timeline_end_sec=2,
                    severity="medium",
                    reason="replace",
                    confidence=0.8,
                    suggested_fix=SuggestedFix(
                        op="replace_clip",
                        clip_id="b",
                        requirements={"min_visual_energy": 0.6},
                    ),
                ),
            ],
        )
        return review, LLMCall("fake", "test", 5, 0, "request")


def test_revision_workflow_persists_exact_executable_diff(tmp_path: Path):
    db = tmp_path / "db.sqlite"
    media = tmp_path / "source.mov"
    media.write_bytes(b"x")
    conn = connect(db)
    with conn:
        conn.execute(
            """
            INSERT INTO assets(
              id,path,sha256,width,height,fps_num,fps_den,duration_sec,codec,created_at
            )
            VALUES ('asset',?,'h',1920,1080,24,1,10,'h264','now')
            """,
            (str(media),),
        )
        for shot_id, start, energy in (
            ("old-a", 0, 0.4), ("old-b", 1, 0.4), ("replacement", 3, 0.9)
        ):
            conn.execute(
                """
                INSERT INTO shots(
                  id,asset_id,idx,start_sec,end_sec,visual_energy,image_quality,cutability
                ) VALUES (?,?,?,?,?,?,?,?)
                """,
                (shot_id, "asset", int(start), start, start + 1, energy, 0.8, 0.8),
            )
    spec = EditSpec(
        id="project",
        timebase=Timebase(num=24),
        canvas=Canvas(width=1080, height=1350),
        clips=[
            Clip(
                id="a", asset_id="asset", shot_id="old-a",
                source=SourceRange(in_sec=0, out_sec=1),
                timeline=TimelinePlacement(in_sec=0, duration_sec=1),
            ),
            Clip(
                id="b", asset_id="asset", shot_id="old-b",
                source=SourceRange(in_sec=1, out_sec=2),
                timeline=TimelinePlacement(in_sec=1, duration_sec=1),
                decision=Decision(alternatives=["replacement"]),
            ),
        ],
    )
    path = tmp_path / "editspec.json"
    path.write_text(spec.model_dump_json(by_alias=True), encoding="utf-8")
    with conn:
        conn.execute(
            """
            INSERT INTO edit_specs(project_id,version,spec_json,created_by)
            VALUES (?,?,?,'rule')
            """,
            ("project", 1, spec.model_dump_json(by_alias=True)),
        )

    result = revise_from_feedback(
        conn,
        provider=FakeProvider(),
        feedback="第一个更炸，结尾换掉",
        spec_path=path,
        database_path=db,
    )

    assert result.operations == 2
    assert result.changed_clip_ids == ("a", "b")
    assert conn.execute("SELECT count(*) FROM llm_calls").fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM feedback_events").fetchone()[0] == 2
    assert conn.execute(
        "SELECT status FROM revision_runs WHERE id=?", (result.run_id,)
    ).fetchone()[0] == "complete"
