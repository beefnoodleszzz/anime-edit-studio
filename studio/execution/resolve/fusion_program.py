"""build_fusion_clip_program: the single Fusion-compilation entry point (REFACTOR.md §10).

Every AMVSpec clip gets exactly one Fusion comp, built by this module and
nothing else. The node chain is::

    MediaIn -> BaseTransform -> MotionTransform (+ native motion blur)
             -> [DirectionalBlur] -> PostColor -> MediaOut

``BaseTransform`` (static framing) and ``MotionTransform`` (the animated
curve, carrying the outgoing/incoming TransitionPair overrides near cuts)
are deliberately separate tools (§10 rule 1) so one can be inspected or
disabled without collapsing the other. Center/Size and native Motion Blur's
ShutterAngle share the same BezierSpline-per-parameter mechanism already
proven in ``ResolveAdapter`` (``_create_scalar_spline`` /
``_connect_scalar_spline``), reused here rather than reimplemented.

Ownership rule (§10 rule 3, the "conflicts must not be solved by mass
deletion" constraint the old ``_fresh_transform_comp`` helper violates): this
compiler only ever creates or rebuilds the single comp named
``aes:clip:<clip_id>``. If the item carries any *other* Fusion comp, that is
a real conflict and raises rather than silently deleting it.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from studio.execution.resolve.adapter import ResolveAdapter, ResolveOperationError
from studio.spec.amv import Canvas, Clip, Timebase, TransitionPair

BASE_TRANSFORM_NAME = "BaseTransform"
MOTION_TRANSFORM_NAME = "MotionTransform"
DIRECTIONAL_BLUR_NAME = "DirectionalBlur"
POST_COLOR_NAME = "PostColor"


def comp_name_for(clip_id: str) -> str:
    return f"aes:clip:{clip_id}"


@dataclass(frozen=True)
class FusionProgram:
    comp: object
    comp_name: str
    node_names: list[str] = field(default_factory=list)


def _local_frame(sec: float, clip_in_sec: float, fps: float) -> float:
    return round((sec - clip_in_sec) * fps, 6)


def _ensure_owned_comp(item, comp_name: str):
    """Create-or-rebuild only the comp this compiler owns; never delete a foreign one."""
    existing_names = item.GetFusionCompNameList() or []
    if comp_name in existing_names:
        comp = item.GetFusionCompByName(comp_name)
        if comp is None:
            raise ResolveOperationError(f"comp declared but not retrievable: {comp_name}")
    elif existing_names:
        raise ResolveOperationError(
            f"clip already owns unexpected Fusion comp(s) {existing_names}; "
            "the unified compiler refuses to delete comps it does not own"
        )
    else:
        comp = item.AddFusionComp()
        if not comp:
            raise ResolveOperationError("AddFusionComp failed")
        if not item.RenameFusionCompByName(item.GetFusionCompNameList()[-1], comp_name):
            raise ResolveOperationError(f"failed to name new comp: {comp_name}")

    tools = comp.GetToolList(False) or {}
    media_in = next(
        (t for t in tools.values() if (t.GetAttrs() or {}).get("TOOLS_RegID") == "MediaIn"), None
    )
    media_out = next(
        (t for t in tools.values() if (t.GetAttrs() or {}).get("TOOLS_RegID") == "MediaOut"), None
    )
    if media_in is None or media_out is None:
        raise ResolveOperationError(f"{comp_name}: comp missing MediaIn/MediaOut")
    return comp, media_in, media_out


def _merge_transform_curve(
    clip: Clip, *, clip_in_sec: float, fps: float,
    incoming: TransitionPair | None, outgoing: TransitionPair | None,
) -> dict[str, dict[float, float]]:
    center_x: dict[float, float] = {}
    center_y: dict[float, float] = {}
    size: dict[float, float] = {}

    for kf in clip.motion.transform_keyframes:
        frame = round(kf.sec * fps, 6)
        center_x[frame] = kf.center_x
        center_y[frame] = kf.center_y
        size[frame] = kf.scale

    if incoming is not None:
        for kf in incoming.incoming_keyframes:
            frame = _local_frame(kf.sec, clip_in_sec, fps)
            center_x[frame] = kf.center_x
            center_y[frame] = kf.center_y
            size[frame] = kf.scale

    if outgoing is not None:
        for kf in outgoing.outgoing_keyframes:
            frame = _local_frame(kf.sec, clip_in_sec, fps)
            center_x[frame] = kf.center_x
            center_y[frame] = kf.center_y
            size[frame] = kf.scale

    return {
        "center_x": dict(sorted(center_x.items())),
        "center_y": dict(sorted(center_y.items())),
        "size": dict(sorted(size.items())),
    }


def _merge_blur_curve(
    clip: Clip, *, clip_in_sec: float, fps: float,
    incoming: TransitionPair | None, outgoing: TransitionPair | None,
) -> dict[float, float]:
    strength: dict[float, float] = {}
    for kf in clip.motion.native_motion_blur_keyframes:
        strength[round(kf.sec * fps, 6)] = kf.shutter_angle
    for pair in (incoming, outgoing):
        if pair is None:
            continue
        for kf in pair.blur_keyframes:
            frame = _local_frame(kf.sec, clip_in_sec, fps)
            strength[frame] = max(strength.get(frame, 0.0), kf.strength * 180.0)
    return dict(sorted(strength.items()))


def build_fusion_clip_program(
    item,
    clip: Clip,
    *,
    canvas: Canvas,
    timebase: Timebase,
    incoming_pair: TransitionPair | None = None,
    outgoing_pair: TransitionPair | None = None,
) -> FusionProgram:
    """Build (or idempotently rebuild) the single Fusion comp for ``clip``."""
    fps = timebase.fps
    comp_name = comp_name_for(clip.id)
    comp, media_in, media_out = _ensure_owned_comp(item, comp_name)

    base = comp.AddTool("Transform")
    motion = comp.AddTool("Transform")
    if base is None or motion is None:
        raise ResolveOperationError(f"{comp_name}: BaseTransform/MotionTransform creation failed")
    base.SetAttrs({"TOOLS_Name": BASE_TRANSFORM_NAME})
    motion.SetAttrs({"TOOLS_Name": MOTION_TRANSFORM_NAME})
    if not base.ConnectInput("Input", media_in):
        raise ResolveOperationError(f"{comp_name}: BaseTransform connect failed")
    if not motion.ConnectInput("Input", base):
        raise ResolveOperationError(f"{comp_name}: MotionTransform connect failed")

    base.SetInput("Center", (clip.framing.center_x, clip.framing.center_y))
    base.SetInput("Size", clip.framing.scale)
    base.SetInput("Angle", clip.framing.rotation)

    curve = _merge_transform_curve(
        clip, clip_in_sec=clip.timeline.in_sec, fps=fps,
        incoming=incoming_pair, outgoing=outgoing_pair,
    )
    node_names = [BASE_TRANSFORM_NAME, MOTION_TRANSFORM_NAME]
    if curve["center_x"]:
        # Transform.Center is a Point, not a scalar: drive it with two
        # scalar BezierSplines combined through an expression, exactly like
        # the real-machine-verified pattern in build_transition_pair_comp.
        ResolveAdapter._create_scalar_spline(comp, "MotionCenterX", curve["center_x"])
        ResolveAdapter._create_scalar_spline(comp, "MotionCenterY", curve["center_y"])
        motion.Center.SetExpression("Point(MotionCenterX.Value,MotionCenterY.Value)")
        if motion.Center.GetExpression() != "Point(MotionCenterX.Value,MotionCenterY.Value)":
            raise ResolveOperationError(f"{comp_name}: MotionTransform.Center expression read-back mismatch")
        ResolveAdapter._connect_scalar_spline(comp, motion.Size, "MotionSize", curve["size"])

    motion.SetInput("MotionBlur", 1.0)
    motion.SetInput("Quality", 8.0)
    motion.SetInput("CenterBias", 0.0)
    motion.SetInput("SampleSpread", 1.0)
    blur_curve = _merge_blur_curve(
        clip, clip_in_sec=clip.timeline.in_sec, fps=fps,
        incoming=incoming_pair, outgoing=outgoing_pair,
    )
    if blur_curve:
        shutter = getattr(motion, "ShutterAngle", None)
        if shutter is None:
            raise ResolveOperationError(f"{comp_name}: MotionTransform.ShutterAngle unavailable")
        ResolveAdapter._connect_scalar_spline(comp, shutter, "MotionShutterAngle", blur_curve)
    if motion.GetInput("MotionBlur") != 1.0:
        raise ResolveOperationError(f"{comp_name}: MotionTransform.MotionBlur read-back mismatch")

    upstream = motion
    has_directional_blur = bool(
        (incoming_pair and any(k.strength > 0 for k in incoming_pair.blur_keyframes))
        or (outgoing_pair and any(k.strength > 0 for k in outgoing_pair.blur_keyframes))
    )
    if has_directional_blur:
        directional = comp.AddTool("DirectionalBlur")
        if directional is None:
            raise ResolveOperationError(f"{comp_name}: DirectionalBlur creation failed")
        directional.SetAttrs({"TOOLS_Name": DIRECTIONAL_BLUR_NAME})
        if not directional.ConnectInput("Input", upstream):
            raise ResolveOperationError(f"{comp_name}: DirectionalBlur connect failed")
        length_curve: dict[float, float] = {}
        angle_curve: dict[float, float] = {}
        for pair, keyframes in (
            (incoming_pair, incoming_pair.blur_keyframes if incoming_pair else []),
            (outgoing_pair, outgoing_pair.blur_keyframes if outgoing_pair else []),
        ):
            if pair is None:
                continue
            for kf in keyframes:
                frame = _local_frame(kf.sec, clip.timeline.in_sec, fps)
                length_curve[frame] = kf.strength
                angle_curve[frame] = kf.angle
        if length_curve:
            ResolveAdapter._connect_scalar_spline(comp, directional.Length, "DirectionalBlurLength", length_curve)
        if angle_curve:
            ResolveAdapter._connect_scalar_spline(comp, directional.Angle, "DirectionalBlurAngle", angle_curve)
        upstream = directional
        node_names.append(DIRECTIONAL_BLUR_NAME)

    color = comp.AddTool("BrightnessContrast")
    if color is None:
        raise ResolveOperationError(f"{comp_name}: PostColor creation failed")
    color.SetAttrs({"TOOLS_Name": POST_COLOR_NAME})
    if not color.ConnectInput("Input", upstream):
        raise ResolveOperationError(f"{comp_name}: PostColor connect failed")
    node_names.append(POST_COLOR_NAME)

    if not media_out.ConnectInput("Input", color):
        raise ResolveOperationError(f"{comp_name}: MediaOut connect failed")

    return FusionProgram(comp=comp, comp_name=comp_name, node_names=node_names)


__all__ = [
    "BASE_TRANSFORM_NAME",
    "DIRECTIONAL_BLUR_NAME",
    "MOTION_TRANSFORM_NAME",
    "POST_COLOR_NAME",
    "FusionProgram",
    "build_fusion_clip_program",
    "comp_name_for",
]
