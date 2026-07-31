from __future__ import annotations

from studio.execution.resolve.amv_render import verify_fusion_graph_consistency
from studio.execution.resolve.fusion_program import FusionProgram, comp_name_for


def _program(clip_id: str, *, nodes=("BaseTransform", "MotionTransform", "PostColor")) -> FusionProgram:
    return FusionProgram(comp=object(), comp_name=comp_name_for(clip_id), node_names=list(nodes))


def test_consistent_when_every_clip_has_its_own_correctly_named_comp_with_the_fixed_chain():
    programs = {"c0": _program("c0"), "c1": _program("c1", nodes=("BaseTransform", "MotionTransform", "DirectionalBlur", "PostColor"))}
    assert verify_fusion_graph_consistency(programs)


def test_inconsistent_when_a_comp_is_missing_a_required_node():
    programs = {"c0": _program("c0", nodes=("BaseTransform", "PostColor"))}
    assert not verify_fusion_graph_consistency(programs)


def test_inconsistent_when_comp_name_does_not_match_the_clip():
    programs = {"c0": FusionProgram(comp=object(), comp_name="aes:clip:wrong", node_names=["BaseTransform", "MotionTransform", "PostColor"])}
    assert not verify_fusion_graph_consistency(programs)


def test_inconsistent_when_comp_is_none():
    programs = {"c0": FusionProgram(comp=None, comp_name=comp_name_for("c0"), node_names=["BaseTransform", "MotionTransform", "PostColor"])}
    assert not verify_fusion_graph_consistency(programs)
