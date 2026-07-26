from studio.agents import LLMCall
from studio.core.database import connect
from studio.critic.creative import (
    parse_feedback,
    proposal_to_diff,
    select_replacement_from_db,
)
from studio.critic.creative.revision import CreativeReview, Replacement
from studio.editspec.diff import apply_diff
from studio.editspec.schema import (
    Canvas,
    Clip,
    EditSpec,
    SourceRange,
    Timebase,
    TimelinePlacement,
)


class FakeProvider:
    def generate(self, *, system, prompt, output_type):
        output = {
            "summary": "impact 和 ending 需要换镜",
            "issues": [
                {
                    "kind": "weak_impact",
                    "timeline_start_sec": 8,
                    "timeline_end_sec": 9,
                    "severity": "high",
                    "reason": "第 8 秒冲击不足",
                    "confidence": 0.92,
                    "suggested_fix": {
                        "op": "replace_clip",
                        "clip_id": "c1",
                        "requirements": {"min_visual_energy": 0.8},
                    },
                },
                {
                    "kind": "weak_ending",
                    "timeline_start_sec": 14,
                    "timeline_end_sec": 15,
                    "severity": "medium",
                    "reason": "结尾镜头不成立",
                    "confidence": 0.86,
                    "suggested_fix": {
                        "op": "replace_clip",
                        "clip_id": "c2",
                        "requirements": {"role": "ending"},
                    },
                },
            ],
        }
        return output_type.model_validate(output), LLMCall(
            "fake", "fake", 1, 0, "id"
        )


def _spec():
    return EditSpec(
        id="p",
        timebase=Timebase(num=24),
        canvas=Canvas(width=1080, height=1350),
        clips=[
            Clip(
                id="c0", asset_id="a", shot_id="s0",
                source=SourceRange(in_sec=0, out_sec=7),
                timeline=TimelinePlacement(in_sec=0, duration_sec=7),
            ),
            Clip(
                id="c1", asset_id="a", shot_id="s1",
                source=SourceRange(in_sec=8, out_sec=9),
                timeline=TimelinePlacement(in_sec=7, duration_sec=1),
            ),
            Clip(
                id="c2", asset_id="a", shot_id="s2",
                source=SourceRange(in_sec=14, out_sec=15),
                timeline=TimelinePlacement(in_sec=8, duration_sec=1),
            ),
        ],
    )


def test_feedback_updates_only_two_target_clips():
    spec = _spec()
    review, _ = parse_feedback(
        FakeProvider(),
        feedback="第 8 秒不够炸，结尾那个镜头换掉",
        spec=spec,
    )
    replacements = iter(
        [
            Replacement(asset_id="b", shot_id="impact-new", source_in_sec=20),
            Replacement(asset_id="c", shot_id="ending-new", source_in_sec=30),
        ]
    )
    patch = proposal_to_diff(
        review,
        spec,
        select_replacement=lambda clip, requirements: next(replacements),
        source="user",
    )
    assert len(patch.ops) == 2
    revised = apply_diff(spec, patch)
    assert revised.clip_by_id("c0") == spec.clip_by_id("c0")
    assert revised.clip_by_id("c1").shot_id == "impact-new"
    assert revised.clip_by_id("c2").shot_id == "ending-new"


def test_replacement_selection_uses_numeric_constraints(tmp_path):
    conn = connect(tmp_path / "db.sqlite")
    with conn:
        conn.execute(
            "INSERT INTO assets(id,path,sha256,fps_num,fps_den,duration_sec) "
            "VALUES ('asset','x','hash',24,1,3)"
        )
        conn.executemany(
            """
            INSERT INTO shots(
              id,asset_id,idx,start_sec,end_sec,visual_energy,image_quality,cutability
            ) VALUES (?,?,?,?,?,?,?,?)
            """,
            [
                ("low", "asset", 0, 0, 1, 0.3, 1.0, 1.0),
                ("high", "asset", 1, 1, 2, 0.9, 0.7, 0.8),
            ],
        )
    replacement = select_replacement_from_db(
        conn, _spec().clip_by_id("c1"), {"min_visual_energy": 0.8}
    )
    assert replacement.shot_id == "high"
    assert replacement.source_in_sec == 1
