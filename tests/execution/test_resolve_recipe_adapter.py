from unittest.mock import Mock

import pytest

from studio.execution.resolve import ResolveAdapter
from studio.core.timecode import Timebase


class _Resolve:
    def GetProjectManager(self):
        return object()


class _Tool:
    def __init__(self):
        self.values = {}

    def SetInput(self, name, value):
        self.values[name] = value

    def GetInput(self, name):
        return self.values.get(name)

    def GetAttrs(self):
        return {"TOOLS_Name": "Control"}


class _Expression:
    def __init__(self):
        self.expression = None

    def SetExpression(self, expression):
        self.expression = expression

    def GetExpression(self):
        return self.expression


class _MotionTool(_Tool):
    def __init__(self, name):
        super().__init__()
        self.name = name
        self.SourceTime = _Expression()
        self.Length = _Expression()

    def GetAttrs(self):
        return {"TOOLS_Name": self.name}


class _MotionComp:
    def __init__(self):
        self.speed = _MotionTool("SpeedRamp")
        self.blur = _MotionTool("MotionBlurTransition")

    def GetToolList(self, _selected):
        return {"SpeedRamp": self.speed, "MotionBlurTransition": self.blur}


class _SettleTool(_Tool):
    def __init__(self):
        super().__init__()
        self.Size = _Expression()

    def GetAttrs(self):
        return {"TOOLS_Name": "SettleTransform"}


class _MotionCompWithSettle(_MotionComp):
    def __init__(self):
        super().__init__()
        self.settle = _SettleTool()

    def GetToolList(self, _selected):
        tools = super().GetToolList(_selected)
        tools["SettleTransform"] = self.settle
        return tools


class _Comp:
    def __init__(self):
        self.tool = _Tool()

    def GetToolList(self, _selected):
        return {"Control": self.tool}


class _Item:
    def __init__(self):
        self.comp = _Comp()

    def GetFusionCompNameList(self):
        return []

    def ImportFusionComp(self, _path):
        return self.comp


def test_short_beat_locked_curve_keeps_blur_at_both_cut_edges():
    curves = ResolveAdapter._velocity_smooth_shake_curves(
        duration_frames=10,
        sign=1.0,
        translation=0.1,
        scale_delta=0.14,
        rotation_deg=0.0,
        blur_strength=0.5,
        intensity=1.0,
    )

    assert curves["blur"][0.0] == 0.5
    assert curves["blur"][4.0] == 0.0
    assert curves["blur"][9.0] == 0.5


def test_fusion_recipe_parameters_are_injected_and_read_back(tmp_path):
    artifact = tmp_path / "effect.comp"
    artifact.write_text("composition")
    adapter = ResolveAdapter(_Resolve())
    item = _Item()
    comp = adapter.replace_fusion_comp(
        item,
        artifact,
        comp_name="aes:test@1",
        parameters={"Control.Gain": 0.75},
    )
    assert comp is item.comp
    assert item.comp.tool.values["Gain"] == 0.75


def test_group_lut_uses_registered_id_and_reads_back(tmp_path):
    lut = tmp_path / "look.cube"
    lut.write_text("LUT_3D_SIZE 2")
    graph = Mock()
    graph.SetLUT.return_value = True
    graph.GetLUT.return_value = "AES/look.cube"
    group = Mock()
    group.GetPostClipNodeGraph.return_value = graph
    item = Mock()
    item.AssignToColorGroup.return_value = True
    adapter = ResolveAdapter(_Resolve())
    adapter._project = Mock()
    adapter._project.AddColorGroup.return_value = group

    adapter.apply_group_lut(
        [item],
        group_name="look",
        lut_path=lut,
        registered_path="AES/look.cube",
    )

    graph.SetLUT.assert_called_once_with(1, "AES/look.cube")


def test_resolve_ntsc_setting_uses_real_rate_not_nominal_rate():
    assert ResolveAdapter._fps_setting(Timebase(24000, 1001)) == "23.976"
    assert ResolveAdapter._fps_setting(Timebase(30000, 1001)) == "29.97"
    assert ResolveAdapter._fps_setting(Timebase(60000, 1001)) == "59.94"


def test_speed_ramp_expression_ends_inside_available_source_range():
    adapter = ResolveAdapter(_Resolve())
    comp = _MotionComp()
    expression = adapter.configure_speed_ramp(
        comp,
        duration_frames=96,
        entry_speed=0.45,
        impact_speed=1.8,
        exit_speed=0.65,
        impact_frame=48,
    )
    assert expression == comp.speed.SourceTime.GetExpression()
    assert "iif(time <= 48" in expression
    assert "(time-48)" in expression


def test_whip_blur_expression_targets_requested_clip_side():
    adapter = ResolveAdapter(_Resolve())
    incoming = _MotionComp()
    outgoing = _MotionComp()
    adapter.configure_whip_blur_side(
        incoming,
        side="in",
        duration_frames=48,
        transition_frames=7,
        length=0.24,
        angle=15.0,
    )
    adapter.configure_whip_blur_side(
        outgoing,
        side="out",
        duration_frames=48,
        transition_frames=7,
        length=0.24,
        angle=-15.0,
    )
    assert "time - (0)" in incoming.blur.Length.GetExpression()
    assert "time - (47)" in outgoing.blur.Length.GetExpression()
    assert incoming.blur.values["Angle"] == 15.0
    assert outgoing.blur.values["Angle"] == -15.0


def test_whip_blur_without_settle_tool_is_a_silent_noop():
    """v1 comps have no SettleTransform; settle_scale must not raise."""
    adapter = ResolveAdapter(_Resolve())
    comp = _MotionComp()
    adapter.configure_whip_blur_side(
        comp,
        side="in",
        duration_frames=48,
        transition_frames=7,
        length=0.24,
        angle=0.0,
        settle_scale=0.05,
    )


def test_settle_landing_eases_incoming_zoom_to_rest_but_leaves_exit_still():
    adapter = ResolveAdapter(_Resolve())
    incoming = _MotionCompWithSettle()
    outgoing = _MotionCompWithSettle()
    adapter.configure_whip_blur_side(
        incoming,
        side="in",
        duration_frames=48,
        transition_frames=7,
        length=0.24,
        angle=0.0,
        settle_scale=0.05,
    )
    adapter.configure_whip_blur_side(
        outgoing,
        side="out",
        duration_frames=48,
        transition_frames=7,
        length=0.24,
        angle=0.0,
        settle_scale=0.05,
    )
    size_expr = incoming.settle.Size.GetExpression()
    assert "0.050000000" in size_expr
    scope = {"time": 0, "min": min, "max": max}
    assert eval(size_expr, {"__builtins__": {}}, scope) == pytest.approx(1.05)
    scope["time"] = 6
    assert eval(size_expr, {"__builtins__": {}}, scope) == pytest.approx(1.0)
    assert outgoing.settle.Size.GetExpression() == "1"


def test_reverse_motion_stage_pushes_away_without_scaling_below_fill():
    expression = ResolveAdapter._reverse_scale_expression(0.12, "(time/12)^3")
    assert expression == "1 + (0.120000000)*(1-((time/12)^3))"


def test_reverse_motion_stage_returns_from_pull_endpoint_to_center():
    expression = ResolveAdapter._reverse_offset_expression(-0.04, "(time/12)^3")
    assert expression == "(-0.040000000)*(1-((time/12)^3))"


def test_localized_cut_envelopes_hold_the_middle_of_the_clip():
    entry, exit = ResolveAdapter._localized_cut_envelopes(last=23, width=4)
    assert "time/(4)" in entry
    assert "time-(19)" in exit
    assert entry.startswith("(1-(")
    assert exit.endswith(")*(min(1,max(0,(time-(19))/(4))))")


def test_curve_flow_has_fast_settle_stable_and_anticipation_points():
    curves = ResolveAdapter._velocity_smooth_shake_curves(
        duration_frames=13,
        sign=1.0,
        translation=0.035,
        scale_delta=0.06,
        rotation_deg=0.4,
        blur_strength=0.0,
        intensity=1.0,
    )

    assert set(curves["center_x"]) == {0.0, 3.0, 6.0, 8.0, 12.0}
    assert curves["center_x"][0.0] == pytest.approx(0.465)
    assert curves["center_x"][3.0] == pytest.approx(0.4951)
    assert curves["center_x"][6.0] == pytest.approx(0.5)
    assert curves["center_x"][8.0] == pytest.approx(0.5049)
    assert curves["center_x"][12.0] == pytest.approx(0.535)
    assert curves["center_y"][0.0] == pytest.approx(0.5)
    assert curves["center_y"][12.0] == pytest.approx(0.5)
    assert curves["size"][0.0] == pytest.approx(1.10)
    assert curves["size"][6.0] == pytest.approx(1.04)
    assert curves["size"][12.0] == pytest.approx(1.09)
    assert "blur" not in curves


def test_liquid_curve_is_dense_monotonic_and_has_smooth_velocity():
    curves = ResolveAdapter._liquid_motion_curves(
        duration_frames=31,
        x_sign=-1.0,
        y_sign=0.0,
        distance=0.12,
        scale=0.18,
        zoom_direction="in",
        rotation=1.2,
        blur_peak=0.25,
        peak_phase=0.72,
        anticipation_ratio=0.20,
        release_ratio=0.22,
    )

    assert len(curves["center_x"]) == 31
    positions = list(curves["center_x"].values())
    assert positions[0] == pytest.approx(0.5)
    assert positions[-1] == pytest.approx(0.38)
    assert all(a >= b for a, b in zip(positions, positions[1:]))
    velocities = [
        abs(b - a) for a, b in zip(positions, positions[1:])
    ]
    assert max(velocities) > velocities[0] * 3
    assert curves["blur"][0.0] == pytest.approx(0.25 * 0.35)
    assert curves["blur"][30.0] == pytest.approx(0.0)
    assert max(curves["blur"].values()) == pytest.approx(0.25)


def test_liquid_reverse_curve_overshoots_and_returns_without_a_corner():
    curves = ResolveAdapter._liquid_motion_curves(
        duration_frames=31,
        x_sign=1.0,
        y_sign=0.0,
        distance=0.12,
        scale=0.18,
        zoom_direction="in",
        rotation=1.2,
        blur_peak=0.25,
        reverse=True,
        peak_phase=0.72,
        anticipation_ratio=0.20,
        release_ratio=0.22,
    )

    positions = list(curves["center_x"].values())
    peak = positions.index(max(positions))
    assert 0 < peak < len(positions) - 1
    assert positions[0] == pytest.approx(0.5)
    assert positions[-1] == pytest.approx(0.5)
    assert all(a <= b for a, b in zip(positions[:peak], positions[1:peak + 1]))
    assert all(a >= b for a, b in zip(positions[peak:], positions[peak + 1:]))


def test_music_peak_moves_liquid_velocity_peak():
    early = ResolveAdapter._liquid_motion_curves(
        duration_frames=61, x_sign=1.0, y_sign=0.0, distance=0.1,
        scale=0.1, zoom_direction="in", rotation=0.0, blur_peak=0.1,
        peak_phase=0.30, anticipation_ratio=0.16, release_ratio=0.20,
    )
    late = ResolveAdapter._liquid_motion_curves(
        duration_frames=61, x_sign=1.0, y_sign=0.0, distance=0.1,
        scale=0.1, zoom_direction="in", rotation=0.0, blur_peak=0.1,
        peak_phase=0.78, anticipation_ratio=0.16, release_ratio=0.20,
    )

    early_peak = max(early["blur"], key=early["blur"].get)
    late_peak = max(late["blur"], key=late["blur"].get)
    assert early_peak < late_peak
    assert early_peak == pytest.approx(18, abs=5)
    assert late_peak == pytest.approx(47, abs=5)


def test_liquid_curve_inherits_velocity_after_cut_then_settles():
    inherited = ResolveAdapter._liquid_motion_curves(
        duration_frames=31,
        x_sign=1.0,
        y_sign=0.0,
        distance=0.1,
        scale=0.1,
        zoom_direction="in",
        rotation=0.0,
        blur_peak=0.1,
        peak_phase=0.08,
        anticipation_ratio=0.08,
        release_ratio=0.28,
        inherited_velocity=1.0,
    )
    positions = list(inherited["center_x"].values())
    velocities = [b - a for a, b in zip(positions, positions[1:])]
    assert velocities[0] > velocities[len(velocities) // 2] * 2
    assert all(value >= 0 for value in velocities)


def test_settle_curve_starts_on_zoomed_impact_and_recovers_without_pullout():
    curves = ResolveAdapter._liquid_motion_curves(
        duration_frames=31,
        x_sign=1.0,
        y_sign=0.0,
        distance=0.05,
        scale=0.24,
        zoom_direction="in",
        rotation=0.0,
        blur_peak=0.1,
        peak_phase=0.08,
        anticipation_ratio=0.08,
        release_ratio=0.28,
        inherited_velocity=1.0,
        settle=True,
    )
    sizes = list(curves["size"].values())
    assert sizes[0] == pytest.approx(1.24)
    assert sizes[-1] == pytest.approx(1.0)
    assert all(a >= b for a, b in zip(sizes, sizes[1:]))
    assert min(sizes) >= 1.0


def test_impact_zoom_stays_clean_until_final_two_frames():
    curves = ResolveAdapter._liquid_motion_curves(
        duration_frames=31,
        x_sign=0.0,
        y_sign=0.0,
        distance=0.0,
        scale=0.22,
        zoom_direction="in",
        rotation=0.0,
        blur_peak=0.1,
    )
    sizes = list(curves["size"].values())
    assert sizes[:-2] == pytest.approx([1.0] * 29)
    assert sizes[-2] > 1.0
    assert sizes[-1] == pytest.approx(1.22)


def test_beat_pull_template_normalizes_time_and_relays_zoom_across_cut():
    outgoing = ResolveAdapter._beat_pull_curves(
        duration_frames=42,
        stage="accelerate",
        intensity=0.75,
        window_frames=8,
    )
    incoming = ResolveAdapter._beat_pull_curves(
        duration_frames=42,
        stage="settle",
        intensity=0.75,
        window_frames=8,
    )

    out_time = outgoing["source_time"]
    in_time = incoming["source_time"]
    assert out_time[0.0] == pytest.approx(0.0)
    assert out_time[41.0] == pytest.approx(41.0)
    assert in_time[0.0] == pytest.approx(0.0)
    assert in_time[41.0] == pytest.approx(41.0)
    assert all(
        out_time[float(frame)] > out_time[float(frame - 1)]
        for frame in range(1, 42)
    )
    assert (
        (out_time[41.0] - out_time[40.0])
        / (out_time[1.0] - out_time[0.0])
        == pytest.approx(4.0, rel=0.08)
    )
    assert (
        in_time[1.0] - in_time[0.0]
        > in_time[41.0] - in_time[40.0]
    )

    out_size = outgoing["size"]
    in_size = incoming["size"]
    assert out_size[40.0] == pytest.approx(1.0)
    assert out_size[41.0] == pytest.approx(in_size[0.0])
    assert out_size[41.0] == pytest.approx(1.08)
    assert in_size[1.0] < 1.08
    assert in_size[2.0] == pytest.approx(1.0)
    assert in_size[41.0] == pytest.approx(1.0)


class _CurveTool(_Tool):
    def __init__(self, regid="Control", name="Control"):
        super().__init__()
        self._regid = regid
        self._name = name
        self.Center = _Expression()
        self.Size = _Expression()

    def GetAttrs(self):
        return {"TOOLS_RegID": self._regid, "TOOLS_Name": self._name}

    def SetAttrs(self, _attrs):
        return None

    def ConnectInput(self, _name, _src):
        return True


class _CurveComp:
    def __init__(self):
        self.media_in = _CurveTool("MediaIn", "MediaIn")
        self.media_out = _CurveTool("MediaOut", "MediaOut")
        self.transform = _CurveTool("Transform", "CameraCurve")

    def GetToolList(self, _selected):
        return {"MediaIn": self.media_in, "MediaOut": self.media_out}

    def AddTool(self, _kind):
        return self.transform


class _CurveItem:
    def __init__(self):
        self.comp = _CurveComp()
        self.renamed = None

    def GetFusionCompNameList(self):
        return ["Comp1"]

    def DeleteFusionCompByName(self, _name):
        return True

    def AddFusionComp(self):
        return self.comp

    def RenameFusionCompByName(self, _old, new):
        self.renamed = new
        return True


def test_camera_curve_pan_uses_eased_signed_offset_with_base_zoom():
    adapter = ResolveAdapter(_Resolve())
    item = _CurveItem()
    adapter.build_camera_curve_comp(
        item, comp_name="aes:camera:c1", direction="pan_right"
        if False else "right",
        magnitude=0.2, curve="ease_in", duration_frames=13,
    )
    center = item.comp.transform.Center.GetExpression()
    size = item.comp.transform.Size.GetExpression()
    # right pans content negative; ease_in is t*t over the clip length.
    assert "(-0.200000)*(((time/12))*((time/12)))" in center
    assert center.startswith("Point(0.5 + (")
    # A base zoom keeps the pan from exposing the canvas edge.
    assert size == "1.160000"
    assert item.renamed == "aes:camera:c1"


def test_camera_curve_push_in_grows_size_only():
    adapter = ResolveAdapter(_Resolve())
    item = _CurveItem()
    adapter.build_camera_curve_comp(
        item, comp_name="aes:camera:c2", direction="in",
        magnitude=0.15, curve="ease_out", duration_frames=21,
    )
    center = item.comp.transform.Center.GetExpression()
    size = item.comp.transform.Size.GetExpression()
    assert center == "Point(0.5, 0.5)"      # push does not pan
    assert size.startswith("1.000000 + ")   # zoom grows from the fill baseline
    assert "(1-(1-(time/20))*(1-(time/20)))" in size  # ease_out curve


def test_camera_curve_push_out_settles_to_fill_without_exposing_canvas():
    adapter = ResolveAdapter(_Resolve())
    item = _CurveItem()
    adapter.build_camera_curve_comp(
        item, comp_name="aes:camera:c3", direction="out",
        magnitude=0.18, curve="ease_in", duration_frames=16,
    )
    center = item.comp.transform.Center.GetExpression()
    size = item.comp.transform.Size.GetExpression()
    assert center == "Point(0.5, 0.5)"
    assert size == "1.180000 - (0.180000)*(((time/15))*((time/15)))"


def test_transition_pair_curves_join_incoming_left_and_outgoing_right():
    curves = ResolveAdapter.transition_pair_curves(
        duration_frames=21,
        incoming_direction="left",
        outgoing_direction="right",
    )
    assert curves["position_px"][0.0] == 110
    assert curves["position_px"][4.0] == -5
    assert curves["position_px"][6.0] == 0
    assert curves["position_px"][16.0] == 0
    assert curves["position_px"][18.0] == 12
    assert curves["position_px"][19.0] == 35
    assert curves["position_px"][20.0] == 85
    assert curves["zoom"][0.0] == 1.080
    assert curves["zoom"][6.0] == 1.020
    assert curves["zoom"][16.0] == 1.000
    assert curves["blur"][20.0] == 0.75
    assert curves["center_x"][0.0] == pytest.approx(0.5 - 110 / 1080)
