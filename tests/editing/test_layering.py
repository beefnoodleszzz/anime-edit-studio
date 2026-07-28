from studio.editing.sequence.layering import detect_layering_opportunities
from studio.editspec.schema import (
    Canvas,
    Clip,
    CutRelation,
    EditSpec,
    SourceRange,
    Timebase,
    TimelinePlacement,
)
from studio.execution.external_ai.subject_mask import SubjectLayer


def _clip(cid, t_in, dur, relation="continuation"):
    return Clip(
        id=cid, asset_id="a", shot_id=cid,
        source=SourceRange(in_sec=0.0, out_sec=dur),
        timeline=TimelinePlacement(in_sec=t_in, duration_sec=dur),
        incoming_cut=CutRelation(kind=relation, motivation="x", confidence=0.8),
    )


def _spec(clips):
    return EditSpec(
        id="t", timebase=Timebase(num=24, den=1),
        canvas=Canvas(width=3072, height=3072, aspect="1:1"),
        clips=clips,
    )


def _layer(coverage, sweep):
    return SubjectLayer(sample_fps=6.0, mean_coverage=coverage, horizontal_sweep=sweep)


def test_parallax_opportunity_on_held_foreground_subject():
    spec = _spec([_clip("c1", 0.0, 1.5)])
    layers = {"c1": _layer(0.4, 0.05)}
    plan = detect_layering_opportunities(spec, subject_layers=layers)
    kinds = {o.kind for o in plan.opportunities}
    assert "parallax_25d" in kinds
    parallax = next(o for o in plan.opportunities if o.kind == "parallax_25d")
    assert parallax.recipe == "parallax_25d_v1"


def test_no_parallax_when_subject_fills_frame():
    spec = _spec([_clip("c1", 0.0, 1.5)])
    layers = {"c1": _layer(0.95, 0.05)}
    plan = detect_layering_opportunities(spec, subject_layers=layers)
    assert all(o.kind != "parallax_25d" for o in plan.opportunities)


def test_occlusion_opportunity_on_high_sweep_before_hard_cut():
    spec = _spec([_clip("c1", 0.0, 1.0), _clip("c2", 1.0, 1.0)])
    # c1's subject sweeps across with high coverage -> can occlude the c2 join.
    layers = {"c1": _layer(0.4, 0.6), "c2": _layer(0.3, 0.1)}
    plan = detect_layering_opportunities(spec, subject_layers=layers)
    occlusions = [o for o in plan.opportunities if o.kind == "occlusion_cut"]
    assert occlusions
    assert occlusions[0].clip_id == "c2"
    assert occlusions[0].recipe == "occlusion_cut_v1"


def test_no_occlusion_without_sweep():
    spec = _spec([_clip("c1", 0.0, 1.0), _clip("c2", 1.0, 1.0)])
    layers = {"c1": _layer(0.4, 0.1), "c2": _layer(0.3, 0.1)}
    plan = detect_layering_opportunities(spec, subject_layers=layers)
    assert all(o.kind != "occlusion_cut" for o in plan.opportunities)


def test_missing_layer_is_skipped():
    spec = _spec([_clip("c1", 0.0, 1.5)])
    plan = detect_layering_opportunities(spec, subject_layers={})
    assert plan.opportunities == []
