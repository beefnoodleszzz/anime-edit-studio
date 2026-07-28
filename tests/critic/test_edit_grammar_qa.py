from studio.critic.creative import evaluate_edit_grammar
from studio.editspec.schema import (
    Canvas,
    Clip,
    CutRelation,
    EditSpec,
    SourceRange,
    SourceSelection,
    Timebase,
    TimelinePlacement,
)


def _clip(index: int, relation: str) -> Clip:
    return Clip(
        id=f"c{index}",
        asset_id="a",
        source=SourceRange(in_sec=index, out_sec=index + 1),
        timeline=TimelinePlacement(in_sec=index, duration_sec=1),
        incoming_cut=CutRelation(
            kind=relation,
            motivation=f"{relation} motivation",
            confidence=0.8,
        ),
        source_selection=SourceSelection(
            phase="action",
            anchor_sec=index + 0.5,
            confidence=0.7,
        ),
    )


def test_edit_grammar_qa_accepts_motivated_diverse_sequence():
    spec = EditSpec(
        id="grammar",
        timebase=Timebase(num=24),
        canvas=Canvas(width=1080, height=1080),
        clips=[
            _clip(0, "establish"),
            _clip(1, "match_action"),
            _clip(2, "graphic_match"),
            _clip(3, "contrast"),
        ],
    )
    result = evaluate_edit_grammar(spec)
    assert result.passed
    assert result.relation_diversity == 3
    assert result.source_phase_ratio == 1


def test_edit_grammar_qa_rejects_unexplained_legacy_sequence():
    spec = EditSpec(
        id="legacy",
        timebase=Timebase(num=24),
        canvas=Canvas(width=1080, height=1080),
        clips=[
            Clip(
                id=f"c{index}",
                asset_id="a",
                source=SourceRange(in_sec=index, out_sec=index + 1),
                timeline=TimelinePlacement(in_sec=index, duration_sec=1),
            )
            for index in range(4)
        ],
    )
    result = evaluate_edit_grammar(spec)
    assert not result.passed
    assert result.motivated_cut_ratio == 0
    assert result.source_phase_ratio == 0
