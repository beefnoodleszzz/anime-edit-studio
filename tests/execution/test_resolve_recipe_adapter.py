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
