"""Fake-Resolve tests for the unified Fusion compiler (REFACTOR.md §16.5)."""
from __future__ import annotations

import pytest

from studio.execution.resolve.adapter import ResolveOperationError
from studio.execution.resolve.fusion_program import (
    BASE_TRANSFORM_NAME,
    DIRECTIONAL_BLUR_NAME,
    MOTION_TRANSFORM_NAME,
    POST_COLOR_NAME,
    build_fusion_clip_program,
    comp_name_for,
)
from studio.spec.amv import (
    Canvas,
    Clip,
    DirectionalBlurKeyframe,
    Motion,
    MotionBlurKeyframe,
    SourceRange,
    Timebase,
    TimelinePlacement,
    TransformKeyframe,
    TransitionPair,
)


class _Expr:
    def __init__(self):
        self.expression = None

    def SetExpression(self, expression):
        self.expression = expression

    def GetExpression(self):
        return self.expression


class _ConnectableInput:
    def __init__(self):
        self.connected_output = None

    def ConnectTo(self, output):
        self.connected_output = output
        return True


class _Tool:
    def __init__(self, regid, name):
        self._attrs = {"TOOLS_RegID": regid, "TOOLS_Name": name}
        self.inputs: dict[str, object] = {}
        self.Center = _Expr()
        self.Size = _ConnectableInput()
        self.Angle = _ConnectableInput()
        self.ShutterAngle = _ConnectableInput()
        self.Length = _ConnectableInput()
        self.Gain = _ConnectableInput()

    def GetAttrs(self):
        return self._attrs

    def SetAttrs(self, attrs):
        self._attrs.update(attrs)

    def SetInput(self, name, value):
        self.inputs[name] = value

    def GetInput(self, name):
        return self.inputs.get(name)

    def ConnectInput(self, name, source):
        self.inputs[f"__connect_{name}"] = source
        return True


class _Spline:
    def __init__(self):
        self._attrs: dict[str, str] = {}
        self.keyframes: dict[float, dict] = {}

    def SetAttrs(self, attrs):
        self._attrs.update(attrs)

    def SetKeyFrames(self, keyframes):
        self.keyframes = keyframes

    def GetKeyFrames(self):
        return self.keyframes

    def GetOutputList(self):
        return {1: self}


class _Comp:
    def __init__(self):
        self.media_in = _Tool("MediaIn", "MediaIn")
        self.media_out = _Tool("MediaOut", "MediaOut")
        self.tools: list[_Tool] = []
        self.splines: list[_Spline] = []

    def GetToolList(self, _selected):
        registry = {"MediaIn": self.media_in, "MediaOut": self.media_out}
        for index, tool in enumerate(self.tools):
            registry[f"tool{index}"] = tool
        return registry

    def AddTool(self, kind):
        tool = _Tool(kind, kind)
        self.tools.append(tool)
        return tool

    def BezierSpline(self):
        spline = _Spline()
        self.splines.append(spline)
        return spline

    def spline_named(self, name):
        return next(s for s in self.splines if s._attrs.get("TOOLS_Name") == name)

    def tool_named(self, name):
        return next(t for t in self.tools if t._attrs.get("TOOLS_Name") == name)


class _Item:
    def __init__(self):
        self.comps: dict[str, _Comp] = {}
        self.order: list[str] = []

    def GetFusionCompNameList(self):
        return list(self.order)

    def AddFusionComp(self):
        comp = _Comp()
        name = f"Comp{len(self.order) + 1}"
        self.comps[name] = comp
        self.order.append(name)
        return comp

    def RenameFusionCompByName(self, old, new):
        if old not in self.comps:
            return False
        comp = self.comps.pop(old)
        self.comps[new] = comp
        self.order[self.order.index(old)] = new
        return True

    def GetFusionCompByName(self, name):
        return self.comps.get(name)

    def DeleteFusionCompByName(self, name):
        if name not in self.comps:
            return False
        del self.comps[name]
        self.order.remove(name)
        return True


CANVAS = Canvas(width=1080, height=1350, aspect="4:5")
TIMEBASE = Timebase(num=24000, den=1001)


def _clip(clip_id="c0", *, motion=None, in_sec=0.0, duration=2.0):
    return Clip(
        id=clip_id, asset_id="a0",
        source=SourceRange(in_sec=0.0, out_sec=duration),
        timeline=TimelinePlacement(in_sec=in_sec, duration_sec=duration),
        motion=motion or Motion(),
    )


def test_single_clip_gets_exactly_one_owned_comp_with_stable_node_chain():
    item = _Item()
    clip = _clip(motion=Motion(transform_keyframes=[
        TransformKeyframe(sec=0.0, center_x=0.5, center_y=0.5, scale=1.0),
        TransformKeyframe(sec=2.0, center_x=0.5, center_y=0.5, scale=1.1),
    ]))
    program = build_fusion_clip_program(item, clip, canvas=CANVAS, timebase=TIMEBASE)

    assert item.GetFusionCompNameList() == [comp_name_for("c0")]
    assert program.node_names == [BASE_TRANSFORM_NAME, MOTION_TRANSFORM_NAME, POST_COLOR_NAME]
    comp = program.comp
    base = comp.tool_named(BASE_TRANSFORM_NAME)
    motion_tool = comp.tool_named(MOTION_TRANSFORM_NAME)
    color = comp.tool_named(POST_COLOR_NAME)
    assert base.inputs["__connect_Input"] is comp.media_in
    assert motion_tool.inputs["__connect_Input"] is base
    assert color.inputs["__connect_Input"] is motion_tool
    assert comp.media_out.inputs["__connect_Input"] is color
    assert motion_tool.Center.expression == "Point(MotionCenterX.Value,MotionCenterY.Value)"
    assert motion_tool.GetInput("MotionBlur") == 1.0


def test_rebuilding_the_same_clip_is_idempotent_and_owns_only_one_comp():
    item = _Item()
    clip = _clip()
    build_fusion_clip_program(item, clip, canvas=CANVAS, timebase=TIMEBASE)
    build_fusion_clip_program(item, clip, canvas=CANVAS, timebase=TIMEBASE)
    assert item.GetFusionCompNameList() == [comp_name_for("c0")]


def test_foreign_comp_is_never_silently_deleted():
    item = _Item()
    item.AddFusionComp()  # some other system's comp, not ours
    clip = _clip()
    with pytest.raises(ResolveOperationError):
        build_fusion_clip_program(item, clip, canvas=CANVAS, timebase=TIMEBASE)
    # The foreign comp must still be there — no mass-deletion fallback.
    assert len(item.GetFusionCompNameList()) == 1


def test_transition_pair_adds_directional_blur_and_drives_both_curves():
    item = _Item()
    clip = _clip(clip_id="c1", in_sec=2.0, duration=2.0)
    pair = TransitionPair(
        id="t0", cut_sec=2.0, outgoing_clip_id="c0", incoming_clip_id="c1",
        direction="left", safe_scale=1.15, confidence=0.8,
        incoming_keyframes=[
            TransformKeyframe(sec=2.0, center_x=0.6, center_y=0.5, scale=1.15),
            TransformKeyframe(sec=2.33, center_x=0.5, center_y=0.5, scale=1.0),
        ],
        blur_keyframes=[
            DirectionalBlurKeyframe(sec=2.0, angle=0.0, strength=0.6),
            DirectionalBlurKeyframe(sec=2.33, angle=0.0, strength=0.0),
        ],
    )
    program = build_fusion_clip_program(
        item, clip, canvas=CANVAS, timebase=TIMEBASE, incoming_pair=pair,
    )
    assert DIRECTIONAL_BLUR_NAME in program.node_names
    comp = program.comp
    directional = comp.tool_named(DIRECTIONAL_BLUR_NAME)
    motion_tool = comp.tool_named(MOTION_TRANSFORM_NAME)
    assert directional.inputs["__connect_Input"] is motion_tool
    assert comp.media_out.inputs["__connect_Input"] is comp.tool_named(POST_COLOR_NAME)
    length_spline = comp.spline_named("DirectionalBlurLength")
    # BezierSpline keyframe *positions* only keep 4 decimal places on real
    # Resolve (values are unaffected) — _create_scalar_spline rounds to
    # that precision itself so callers don't have to remember it.
    assert set(length_spline.keyframes) == {0.0, round(0.33 * TIMEBASE.fps, 4)}


def test_directional_blur_length_is_a_small_fraction_of_image_width_not_the_raw_strength():
    # Regression: DirectionalBlur.Length is a fraction of image width, not a
    # 0-1 "how strong" dial. Feeding a blur_keyframe's strength (up to 0.6)
    # straight into Length produced a blur streak 60% of the frame wide on
    # nearly every cut — found by summing every transition's blur window
    # against a real render's total duration (~40% of runtime) after ruling
    # out native motion blur as the visible cause.
    item = _Item()
    clip = _clip(clip_id="c1", in_sec=2.0, duration=2.0)
    pair = TransitionPair(
        id="t0", cut_sec=2.0, outgoing_clip_id="c0", incoming_clip_id="c1",
        direction="left", safe_scale=1.15, confidence=0.8,
        blur_keyframes=[
            DirectionalBlurKeyframe(sec=2.0, angle=0.0, strength=0.6),
            DirectionalBlurKeyframe(sec=2.33, angle=0.0, strength=0.0),
        ],
    )
    program = build_fusion_clip_program(
        item, clip, canvas=CANVAS, timebase=TIMEBASE, incoming_pair=pair,
    )
    length_spline = program.comp.spline_named("DirectionalBlurLength")
    peak_length = max(v[1] for v in length_spline.keyframes.values())
    assert peak_length < 0.2


def test_outgoing_transition_spike_gets_a_zero_baseline_so_it_cant_smear_the_whole_clip():
    # Regression: a BezierSpline holds its first keyframe's value constant
    # for every frame *before* it. An outgoing clip's anticipation spike
    # commonly lands near the clip's own last frame (short clip, most of
    # its anticipation window folded into the next clip) with no earlier
    # point defined — with nothing before it, Fusion extrapolated that
    # peak Length backward across the *entire* clip instead of just the
    # anticipation window. Found by reading back a real render's connected
    # DirectionalBlurLength spline on a real 0.9s clip: its only two
    # keyframes both sat at/after the clip's own last valid frame, so
    # every actually-rendered frame preceded them and inherited full
    # blur strength throughout — this, not the native motion-blur shutter,
    # was the dominant cause of a "half the frame is a mosaic" complaint.
    item = _Item()
    duration = 0.9
    clip = _clip(clip_id="c0", in_sec=0.0, duration=duration)
    pair = TransitionPair(
        id="t0", cut_sec=duration, outgoing_clip_id="c0", incoming_clip_id="c1",
        direction="left", safe_scale=1.15, confidence=0.8,
        outgoing_keyframes=[
            TransformKeyframe(sec=duration - 0.1, center_x=0.5, center_y=0.5, scale=1.0),
            TransformKeyframe(sec=duration, center_x=0.55, center_y=0.5, scale=1.15),
        ],
        blur_keyframes=[
            DirectionalBlurKeyframe(sec=duration, angle=-90.0, strength=0.6),
            DirectionalBlurKeyframe(sec=duration + 0.2, angle=0.0, strength=0.0),
        ],
    )
    program = build_fusion_clip_program(
        item, clip, canvas=CANVAS, timebase=TIMEBASE, outgoing_pair=pair,
    )
    length_spline = program.comp.spline_named("DirectionalBlurLength")
    assert 0.0 in length_spline.keyframes
    assert length_spline.keyframes[0.0][1] == pytest.approx(0.0)


def test_transition_blur_keyframes_dont_erase_the_clips_own_baseline_shutter():
    # Regression: a transition's blur_keyframes (spike + decay near the cut)
    # land at the same frame as the clip's own start/end shutter keyframe
    # and overwrite it there, leaving nothing to anchor the middle of a
    # long clip — Fusion then Bezier-interpolates the shutter angle across
    # the entire remaining gap instead of holding the clip's real baseline.
    # Found by reading back a real render's connected ShutterAngle spline
    # tool: only the transition's spike/decay points had survived, and the
    # angle ramped smoothly across nearly the whole clip between them.
    item = _Item()
    duration = 3.0
    clip = _clip(
        clip_id="c1", in_sec=2.0, duration=duration,
        motion=Motion(
            transform_keyframes=[
                TransformKeyframe(sec=0.0, center_x=0.5, center_y=0.5, scale=1.0),
                TransformKeyframe(sec=duration, center_x=0.5, center_y=0.5, scale=1.1),
            ],
            native_motion_blur_keyframes=[
                MotionBlurKeyframe(sec=0.0, shutter_angle=50.0),
                MotionBlurKeyframe(sec=duration, shutter_angle=50.0),
            ],
        ),
    )
    pair = TransitionPair(
        id="t0", cut_sec=2.0, outgoing_clip_id="c0", incoming_clip_id="c1",
        direction="left", safe_scale=1.15, confidence=0.8,
        blur_keyframes=[
            DirectionalBlurKeyframe(sec=2.0, angle=0.0, strength=0.6),
            DirectionalBlurKeyframe(sec=2.33, angle=0.0, strength=0.0),
        ],
    )
    program = build_fusion_clip_program(
        item, clip, canvas=CANVAS, timebase=TIMEBASE, incoming_pair=pair,
    )
    comp = program.comp
    shutter_spline = comp.spline_named("MotionShutterAngle")
    frames = sorted(shutter_spline.keyframes)
    decay_frame = next(f for f in frames if shutter_spline.keyframes[f][1] == pytest.approx(0.0))
    next_frame = min(f for f in frames if f > decay_frame)
    # The gap right after the transition's decay must stay local to the
    # transition (a held baseline point within a couple of frames), not
    # span most of the clip's remaining duration.
    assert next_frame - decay_frame <= 2
    assert shutter_spline.keyframes[next_frame][1] == pytest.approx(50.0)


def test_flash_effect_kind_drives_a_gain_spike_on_the_existing_post_color_tool():
    item = _Item()
    clip = _clip(clip_id="c1", in_sec=2.0, duration=2.0)
    pair = TransitionPair(
        id="t0", cut_sec=2.0, outgoing_clip_id="c0", incoming_clip_id="c1",
        direction="none", safe_scale=1.0, confidence=0.8, effect_kind="flash",
    )
    program = build_fusion_clip_program(
        item, clip, canvas=CANVAS, timebase=TIMEBASE, incoming_pair=pair,
    )
    # No new node — the spike rides the existing PostColor tool.
    assert program.node_names.count(POST_COLOR_NAME) == 1
    comp = program.comp
    gain_spline = comp.spline_named("PostColorGain")
    cut_frame = round(0.0 * TIMEBASE.fps, 6)  # cut_sec=2.0 == clip in_sec -> local frame 0
    assert cut_frame in gain_spline.keyframes
    assert gain_spline.keyframes[cut_frame][1] > 1.0


def test_no_flash_gain_spline_when_effect_kind_is_none():
    item = _Item()
    clip = _clip(clip_id="c1", in_sec=2.0, duration=2.0)
    pair = TransitionPair(
        id="t0", cut_sec=2.0, outgoing_clip_id="c0", incoming_clip_id="c1",
        direction="none", safe_scale=1.0, confidence=0.8, effect_kind="none",
    )
    program = build_fusion_clip_program(
        item, clip, canvas=CANVAS, timebase=TIMEBASE, incoming_pair=pair,
    )
    comp = program.comp
    with pytest.raises(StopIteration):
        comp.spline_named("PostColorGain")


def test_no_directional_blur_tool_when_no_transition_pairs_supplied():
    item = _Item()
    clip = _clip()
    program = build_fusion_clip_program(item, clip, canvas=CANVAS, timebase=TIMEBASE)
    assert DIRECTIONAL_BLUR_NAME not in program.node_names


def test_base_framing_survives_independent_of_motion_curve():
    """§10 rule 1/8: disabling motion must not remove the static base framing."""
    item = _Item()
    clip = _clip(motion=Motion())  # no motion keyframes at all
    program = build_fusion_clip_program(item, clip, canvas=CANVAS, timebase=TIMEBASE)
    base = program.comp.tool_named(BASE_TRANSFORM_NAME)
    assert base.GetInput("Center") == (0.5, 0.5)
    assert base.GetInput("Size") == 1.0
