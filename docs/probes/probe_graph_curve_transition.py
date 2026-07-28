"""Render a minimal two-shot Fusion Graph Curve transition in Resolve.

This is an isolated capability probe. It deliberately does not alter a user
project or promote the capability to verified.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np

from studio.core.timecode import Timebase
from studio.execution.resolve.adapter import ResolveAdapter, ResolveOperationError


ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "docs" / "probes" / "fusion_spline_out"
FPS = Timebase(num=24000, den=1001)
PROJECT = "_aes_graph_curve_transition_probe"
TIMELINE = "two-shot"
SOURCE_A = ROOT / "library" / "proxies" / "72d187a7caed.mp4"
SOURCE_B = ROOT / "library" / "proxies" / "fe54b2a6e032.mp4"
MUSIC = ROOT / "projects" / "akaza-matadora-v1" / "music" / "matadora-slowed-7.848-29.780.wav"
SHOTS = (
    (SOURCE_A, 927.78611736, 928.30311736, 8.234, 0.517),
    (SOURCE_B, 570.303827, 570.822827, 8.751, 0.519),
    (SOURCE_A, 1043.1523625, 1043.6573625, 9.270, 0.505),
    (SOURCE_A, 1024.73614595, 1025.27014595, 9.775, 0.534),
    (SOURCE_B, 283.042225925, 283.559225925, 10.309, 0.517),
    (SOURCE_B, 445.30999665, 445.82399665, 10.826, 0.514),
    (SOURCE_B, 608.924075, 609.443075, 11.340, 0.519),
    (SOURCE_B, 488.493265, 489.013265, 11.859, 0.520),
    (SOURCE_B, 535.357667, 535.868667, 12.379, 0.511),
)
REFERENCE_PHASE = (
    0.0, 0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50,
    0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95, 1.0,
)
REFERENCE_POSITION = (
    0.0, 0.0351, 0.0567, 0.0588, 0.0628, 0.0772, 0.1021, 0.1296,
    0.1553, 0.1725, 0.1809, 0.1913, 0.2104, 0.2496, 0.3147, 0.4318,
    0.6030, 0.7697, 0.8763, 0.9440, 1.0,
)
REFERENCE_VELOCITY = (
    0.1618, 0.2208, 0.0141, 0.0088, 0.0342, 0.1225, 0.1486, 0.1515,
    0.1282, 0.0586, 0.0334, 0.0796, 0.1285, 0.2979, 0.4103, 0.8645,
    1.0, 0.8149, 0.3449, 0.3929, 0.2163,
)


def _tool(comp, reg_id: str):
    return next(
        tool
        for tool in (comp.GetToolList(False) or {}).values()
        if (tool.GetAttrs() or {}).get("TOOLS_RegID") == reg_id
    )


def _spline(comp, name: str, keys: dict[float, float]):
    spline = comp.BezierSpline()
    spline.SetAttrs({"TOOLS_Name": name})
    spline.SetKeyFrames({float(frame): {1: float(value)} for frame, value in keys.items()})
    actual = spline.GetKeyFrames() or {}
    if set(actual) != {float(frame) for frame in keys}:
        raise ResolveOperationError(f"{name} keyframe readback mismatch: {actual}")
    return spline


def _sampled_curve(last: int, start: float, end: float) -> dict[float, float]:
    return {
        float(frame): float(
            start
            + np.interp(frame / last, REFERENCE_PHASE, REFERENCE_POSITION)
            * (end - start)
        )
        for frame in range(last + 1)
    }


def _velocity_curve(last: int, peak: float) -> dict[float, float]:
    return {
        float(frame): float(
            peak * np.interp(frame / last, REFERENCE_PHASE, REFERENCE_VELOCITY)
        )
        for frame in range(last + 1)
    }


def _beat_curves(last: int, index: int) -> dict[str, dict[float, float]]:
    direction = 1.0 if index % 2 == 0 else -1.0
    source_time: dict[float, float] = {}
    center_x: dict[float, float] = {}
    center_y: dict[float, float] = {}
    size: dict[float, float] = {}
    angle: dict[float, float] = {}
    blur: dict[float, float] = {}
    for frame in range(last + 1):
        phase = frame / last
        progress = float(np.interp(phase, REFERENCE_PHASE, REFERENCE_POSITION))
        pulse = math.sin(math.pi * progress)
        # Position and rotation make a closed path: no net drift at either beat.
        center_x[float(frame)] = 0.5 + direction * 0.0035 * math.sin(
            2 * math.pi * progress
        )
        center_y[float(frame)] = 0.5 + 0.0007 * math.sin(4 * math.pi * progress)
        angle[float(frame)] = direction * 0.40 * math.sin(2 * math.pi * progress)
        # The first probe only reached 39% of the reference's measured
        # per-frame scale peak. A bounded 10% pulse closes that gap.
        size[float(frame)] = 1.0 + direction * 0.14 * pulse
        blur[float(frame)] = 0.11 * pulse
        source_time[float(frame)] = progress * last
    return {
        "source_time": source_time,
        "center_x": center_x,
        "center_y": center_y,
        "size": size,
        "angle": angle,
        "blur": blur,
    }


def _build_motion(
    item,
    *,
    name: str,
    source_time: dict[float, float],
    center_x: dict[float, float],
    center_y: dict[float, float],
    size: dict[float, float],
    angle: dict[float, float],
    blur: dict[float, float],
) -> None:
    comp = item.AddFusionComp()
    if not comp:
        raise ResolveOperationError(f"{name}: AddFusionComp failed")
    media_in = _tool(comp, "MediaIn")
    media_out = _tool(comp, "MediaOut")

    speed = comp.AddTool("TimeStretcher")
    speed.SetAttrs({"TOOLS_Name": f"{name}Velocity"})
    if not speed.ConnectInput("Input", media_in):
        raise ResolveOperationError(f"{name}: TimeStretcher connection failed")
    source_curve = _spline(comp, f"{name}SourceTime", source_time)
    if not speed.SourceTime.ConnectTo(source_curve.GetOutputList()[1]):
        raise ResolveOperationError(f"{name}: SourceTime spline connection failed")

    transform = comp.AddTool("Transform")
    transform.SetAttrs({"TOOLS_Name": f"{name}Transform"})
    if not transform.ConnectInput("Input", speed):
        raise ResolveOperationError(f"{name}: Transform connection failed")

    x_curve = _spline(comp, f"{name}CenterX", center_x)
    y_curve = _spline(comp, f"{name}CenterY", center_y)
    size_curve = _spline(comp, f"{name}Size", size)
    angle_curve = _spline(comp, f"{name}Angle", angle)

    center_expression = f"Point({name}CenterX.Value, {name}CenterY.Value)"
    transform.Center.SetExpression(center_expression)
    transform.Size.ConnectTo(size_curve.GetOutputList()[1])
    transform.Angle.ConnectTo(angle_curve.GetOutputList()[1])
    if transform.Center.GetExpression() != center_expression:
        raise ResolveOperationError(f"{name}: Center expression readback mismatch")

    directional_blur = comp.AddTool("DirectionalBlur")
    directional_blur.SetAttrs({"TOOLS_Name": f"{name}VelocityBlur"})
    # DirectionalBlur Type 3 is the Resolve/Fusion "Zoom" mode. Linear mode
    # was the reason the previous probe read as a horizontal whip transition.
    directional_blur.SetInput("Type", 3.0)
    directional_blur.SetInput("Center", {1: 0.5, 2: 0.5})
    if not directional_blur.ConnectInput("Input", transform):
        raise ResolveOperationError(f"{name}: DirectionalBlur connection failed")
    blur_curve = _spline(comp, f"{name}Blur", blur)
    directional_blur.Length.ConnectTo(blur_curve.GetOutputList()[1])
    if not media_out.ConnectInput("Input", directional_blur):
        raise ResolveOperationError(f"{name}: MediaOut connection failed")


def main() -> None:
    rv = ResolveAdapter.open(auto_launch=False)
    rv.ensure_project(PROJECT, timebase=FPS, width=640, height=800, reset=True)
    rv.ensure_timeline(TIMELINE, reset=True)
    rv.ensure_video_tracks(1)
    rv.ensure_audio_tracks(1)

    media = rv.import_media([SOURCE_A, SOURCE_B, MUSIC], bin_name="probe")
    items = rv.append_clips(
        [
            {
                "media_path": path,
                "source_in_sec": source_in,
                "source_out_sec": source_out,
                "timeline_in_sec": timeline_in - SHOTS[0][3],
                "timeline_duration_sec": duration,
                "track_index": 1,
                "media_fps": media[str(path)].fps,
                "timeline_fps": FPS,
                "media_type": 1,
            }
            for path, source_in, source_out, timeline_in, duration in SHOTS
        ]
    )
    total_duration = SHOTS[-1][3] + SHOTS[-1][4] - SHOTS[0][3]
    rv.append_audio(
        [
            {
                "media_path": MUSIC,
                "source_in_sec": 8.234,
                "source_out_sec": 8.234 + total_duration,
                "timeline_in_sec": 0.0,
                "timeline_duration_sec": total_duration,
                "track_index": 1,
                "media_fps": FPS,
                "timeline_fps": FPS,
            }
        ]
    )

    # Native cover zoom keeps the 16:9 source beyond every edge of the 4:5 canvas.
    for item in items:
        result = rv.set_properties(
            item,
            {"ZoomX": 2.222222, "ZoomY": 2.222222, "ZoomGang": True},
        )
        if not all(result.values()):
            raise ResolveOperationError(f"cover zoom failed: {result}")

    # One complete reference-measured curve spans each beat interval. A cut is
    # simultaneously the previous curve's endpoint and the next curve's start.
    # There is no independent post-cut bounce.
    for index, (item, shot) in enumerate(zip(items, SHOTS, strict=True)):
        frames = FPS.duration_frames(shot[3] - SHOTS[0][3], shot[4])
        last = frames - 1
        curves = _beat_curves(last, index)

        _build_motion(
            item,
            name=f"Shot{index + 1:02d}",
            source_time=curves["source_time"],
            center_x=curves["center_x"],
            center_y=curves["center_y"],
            size=curves["size"],
            angle=curves["angle"],
            blur=curves["blur"],
        )

    if not rv._pm.SaveProject():
        raise ResolveOperationError("SaveProject failed")
    rendered = rv.render(
        output_dir=OUT,
        name="velocity-smooth-shake-final-intensity-preview",
        preset="H.264 Master",
        timeout_sec=300,
    )
    print(rendered.output)


if __name__ == "__main__":
    main()
