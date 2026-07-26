from pathlib import Path

from studio.editspec.schema import (
    Canvas,
    Clip,
    EditSpec,
    Framing,
    SourceRange,
    Timebase,
    TimelinePlacement,
)
from studio.execution.compiler import BuildReport, ResolveCompiler
from studio.execution.resolve.adapter import MediaInfo
from studio.core.timecode import Timebase as CoreTimebase


class FakeAdapter:
    def __init__(self):
        self.properties = []

    def set_properties(self, item, props):
        self.properties.append(props)
        return {key: True for key in props}


def test_crop_transform_converts_normalized_offsets_to_resolve_values():
    adapter = FakeAdapter()
    compiler = ResolveCompiler(adapter, lambda _: Path("x"))
    clip = Clip(
        id="c",
        asset_id="a",
        source=SourceRange(in_sec=0, out_sec=1),
        timeline=TimelinePlacement(in_sec=0, duration_sec=1),
        framing=Framing(mode="manual", offset_x=0.1, offset_y=-0.1, scale=1.1),
    )
    spec = EditSpec(
        id="p",
        timebase=Timebase(num=24),
        canvas=Canvas(width=1080, height=1350, aspect="4:5"),
        clips=[clip],
    )
    info = MediaInfo(
        path=Path("x"),
        fps=CoreTimebase(24),
        width=1920,
        height=1080,
        duration_frames=24,
    )
    report = BuildReport(project="p", timeline="main", mode="test")
    compiler._apply_clip_properties(spec, [(clip, object())], {"a": info}, report)
    props = adapter.properties[0]
    assert props["ZoomX"] == props["ZoomY"]
    assert props["ZoomX"] > 2
    assert props["Pan"] == 108
    assert props["Tilt"] == -135
    assert report.warnings == []
