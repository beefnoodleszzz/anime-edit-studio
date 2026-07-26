from unittest.mock import Mock

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
