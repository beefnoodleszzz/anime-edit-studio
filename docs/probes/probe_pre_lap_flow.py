"""Four-shot Resolve probe for the review.md Pre-Lap flow model.

The probe is isolated from the user project and intentionally contains no blur
tool. It tests V1 retreat/landing plus a four-frame masked V2 intrusion.
"""

from __future__ import annotations

from pathlib import Path

from studio.core.timecode import Timebase
from studio.execution.resolve.adapter import ResolveAdapter, ResolveOperationError


ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "docs" / "probes" / "fusion_spline_out"
SOURCE = ROOT / "library" / "proxies" / "72d187a7caed.mp4"
MUSIC = ROOT / "projects" / "akaza-matadora-v1" / "music" / "matadora-slowed-7.848-29.780.wav"
FPS = Timebase(num=30, den=1)
PROJECT = "_aes_pre_lap_flow_probe"
START = 5.642
SHOTS = (
    (1138.00688125, 1138.52988125, 5.642, 0.523),
    (1220.334625, 1220.844625, 6.165, 0.510),
    (1125.563292, 1126.083292, 6.675, 0.520),
    (1192.269337275, 1192.788337275, 7.195, 0.519),
)
ENTRIES = ((0.78, 0.60), (0.78, 0.78), (0.50, 0.88))


def _tool(comp, reg_id: str):
    return next(
        tool
        for tool in (comp.GetToolList(False) or {}).values()
        if (tool.GetAttrs() or {}).get("TOOLS_RegID") == reg_id
    )


def _spline(comp, input_object, name: str, values: dict[float, float]):
    spline = comp.BezierSpline()
    spline.SetAttrs({"TOOLS_Name": name})
    spline.SetKeyFrames(
        {float(frame): {1: float(value)} for frame, value in values.items()}
    )
    actual = spline.GetKeyFrames() or {}
    if set(actual) != set(values):
        raise ResolveOperationError(f"{name}: spline readback mismatch")
    output = (spline.GetOutputList() or {}).get(1)
    if output is None or not input_object.ConnectTo(output):
        raise ResolveOperationError(f"{name}: spline connection failed")
    return spline


def _point_splines(comp, transform, name: str, x: dict[float, float], y: dict[float, float]):
    x_curve = comp.BezierSpline()
    x_curve.SetAttrs({"TOOLS_Name": f"{name}X"})
    x_curve.SetKeyFrames({float(frame): {1: value} for frame, value in x.items()})
    y_curve = comp.BezierSpline()
    y_curve.SetAttrs({"TOOLS_Name": f"{name}Y"})
    y_curve.SetKeyFrames({float(frame): {1: value} for frame, value in y.items()})
    expression = f"Point({name}X.Value, {name}Y.Value)"
    transform.Center.SetExpression(expression)
    if transform.Center.GetExpression() != expression:
        raise ResolveOperationError(f"{name}: point expression readback mismatch")


def _build_v1_motion(item, *, name: str, direction: float) -> None:
    comp = item.AddFusionComp()
    media_in = _tool(comp, "MediaIn")
    media_out = _tool(comp, "MediaOut")
    transform = comp.AddTool("Transform")
    transform.SetAttrs({"TOOLS_Name": f"{name}Transform"})
    transform.ConnectInput("Input", media_in)
    last = int(item.GetDuration() or 1) - 1
    settle = min(3, last)
    _spline(
        comp,
        transform.Size,
        f"{name}Size",
        {0: 1.05, settle: 1.0, last: 0.99},
    )
    _point_splines(
        comp,
        transform,
        f"{name}Center",
        {0: 0.5, settle: 0.5, last: 0.5 - 0.012 * direction},
        {0: 0.5, settle: 0.5, last: 0.5},
    )
    transform.SetInput("Angle", 0.0)
    if not media_out.ConnectInput("Input", transform):
        raise ResolveOperationError(f"{name}: V1 MediaOut connection failed")


def _build_v2_intrusion(
    item,
    *,
    name: str,
    entry: tuple[float, float],
) -> None:
    comp = item.AddFusionComp()
    media_in = _tool(comp, "MediaIn")
    media_out = _tool(comp, "MediaOut")
    transform = comp.AddTool("Transform")
    transform.SetAttrs({"TOOLS_Name": f"{name}Transform"})
    transform.ConnectInput("Input", media_in)
    last = int(item.GetDuration() or 1) - 1
    _spline(comp, transform.Size, f"{name}Size", {0: 0.78, last: 1.08})
    _point_splines(
        comp,
        transform,
        f"{name}Center",
        {0: entry[0], last: 0.5},
        {0: entry[1], last: 0.52},
    )
    transform.SetInput("Angle", 0.0)

    transparent = comp.AddTool("Background")
    transparent.SetAttrs({"TOOLS_Name": f"{name}Transparent"})
    transparent.SetInput("TopLeftAlpha", 0.0)
    mask = comp.AddTool("EllipseMask")
    mask.SetAttrs({"TOOLS_Name": f"{name}Mask"})
    penultimate = max(0, last - 1)
    _spline(
        comp,
        mask.Width,
        f"{name}MaskWidth",
        {0: 0.0, 1: 0.14, penultimate: 0.58, last: 1.50},
    )
    _spline(
        comp,
        mask.Height,
        f"{name}MaskHeight",
        {0: 0.0, 1: 0.14, penultimate: 0.58, last: 1.50},
    )
    _spline(
        comp,
        mask.SoftEdge,
        f"{name}MaskSoftness",
        {0: 0.08, 1: 0.07, penultimate: 0.04, last: 0.01},
    )
    merge = comp.AddTool("Merge")
    merge.SetAttrs({"TOOLS_Name": f"{name}Merge"})
    merge.ConnectInput("Background", transparent)
    merge.ConnectInput("Foreground", transform)
    merge.ConnectInput("EffectMask", mask)
    if not media_out.ConnectInput("Input", merge):
        raise ResolveOperationError(f"{name}: V2 MediaOut connection failed")


def main() -> None:
    rv = ResolveAdapter.open(auto_launch=False)
    rv.ensure_project(PROJECT, timebase=FPS, width=1080, height=1080, reset=True)
    rv.ensure_timeline("pre-lap-4-shot", reset=True)
    rv.ensure_video_tracks(2)
    rv.ensure_audio_tracks(1)
    media = rv.import_media([SOURCE, MUSIC], bin_name="probe")
    prelap_frames = 5
    prelap_sec = prelap_frames / FPS.fps_float

    v1_requests = []
    for index, (source_in, source_out, timeline_in, duration) in enumerate(SHOTS):
        source_shift = prelap_sec if index > 0 else 0.0
        v1_requests.append(
            {
                "media_path": SOURCE,
                "source_in_sec": source_in + source_shift,
                "source_out_sec": source_out + source_shift,
                "timeline_in_sec": timeline_in - START,
                "timeline_duration_sec": duration,
                "track_index": 1,
                "media_fps": media[str(SOURCE)].fps,
                "timeline_fps": FPS,
                "media_type": 1,
            }
        )
    v1_items = rv.append_clips(v1_requests)

    v2_requests = []
    for index, shot in enumerate(SHOTS[1:]):
        source_in, _, timeline_in, _ = shot
        v2_requests.append(
            {
                "media_path": SOURCE,
                "source_in_sec": source_in,
                "source_out_sec": source_in + prelap_sec,
                "timeline_in_sec": timeline_in - START - prelap_sec,
                "timeline_duration_sec": prelap_sec,
                "track_index": 2,
                "media_fps": media[str(SOURCE)].fps,
                "timeline_fps": FPS,
                "media_type": 1,
            }
        )
    v2_items = rv.append_clips(v2_requests)

    total_duration = SHOTS[-1][2] + SHOTS[-1][3] - START
    rv.append_audio(
        [
            {
                "media_path": MUSIC,
                "source_in_sec": START,
                "source_out_sec": START + total_duration,
                "timeline_in_sec": 0.0,
                "timeline_duration_sec": total_duration,
                "track_index": 1,
                "media_fps": FPS,
                "timeline_fps": FPS,
            }
        ]
    )
    for item in (*v1_items, *v2_items):
        results = rv.set_properties(
            item,
            {"ZoomX": 1.777778, "ZoomY": 1.777778, "ZoomGang": True},
        )
        if not all(results.values()):
            raise ResolveOperationError(f"cover zoom failed: {results}")

    directions = (1.0, -1.0, 1.0, -1.0)
    for index, item in enumerate(v1_items):
        _build_v1_motion(item, name=f"V1Shot{index + 1}", direction=directions[index])
    for index, item in enumerate(v2_items):
        _build_v2_intrusion(
            item,
            name=f"V2PreLap{index + 1}",
            entry=ENTRIES[index],
        )

    for index, (_, _, timeline_in, _) in enumerate(SHOTS[1:], start=1):
        rv.add_timeline_marker(
            FPS.to_frames(timeline_in - START),
            f"beat:{index}",
            "Pre-Lap completes; V1 formal shot takes over",
            duration_frames=1,
            color="Red",
        )

    if not rv._pm.SaveProject():
        raise ResolveOperationError("SaveProject failed")
    result = rv.render(
        output_dir=OUT,
        name="pre-lap-flow-4-shot-preview",
        preset="H.264 Master",
        timeout_sec=300,
    )
    print(result.output)


if __name__ == "__main__":
    main()
