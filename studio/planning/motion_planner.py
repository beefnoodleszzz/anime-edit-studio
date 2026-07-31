"""Unified Motion Planner (REFACTOR.md §9): continuous cross-cut motion.

Produces per-clip ``Motion`` (a simple hold/push curve honoring the slot's
energy and hold flag) and per-cut ``TransitionPair`` keyframes whose
outgoing and incoming curves are generated together, so an outgoing clip
decelerating into a cut and the incoming clip picking up its direction are
never independent decisions (the "旧的 Camera Move + 独立 Zoom Punch" pattern
REFACTOR.md explicitly forbids in §9).

``safe_scale`` follows §9.1's coverage estimate plus a rotation-aware
corner check, with a safety margin so panned/rotated frames never expose
canvas edges.
"""
from __future__ import annotations

import math

from studio.planning.slots import EntryMotion, TimelineSlot
from studio.spec.amv import (
    Canvas,
    DirectionalBlurKeyframe,
    Motion,
    MotionBlurKeyframe,
    TransformKeyframe,
    TransitionPair,
)

SAFETY_MARGIN = 1.06
BASE_PUSH_SCALE = 0.06
TRANSLATION_UNIT = 0.10
ANTICIPATION_SEC = 8 / 24
RELEASE_SEC = 8 / 24

_DIRECTION_VECTOR = {
    "left": (-1.0, 0.0), "right": (1.0, 0.0),
    "up": (0.0, -1.0), "down": (0.0, 1.0),
    "up-left": (-0.7, -0.7), "up-right": (0.7, -0.7),
    "down-left": (-0.7, 0.7), "down-right": (0.7, 0.7),
    "none": (0.0, 0.0),
}


def required_safe_scale(
    dx_frac: float, dy_frac: float, canvas: Canvas, *, rotation_deg: float = 0.0,
) -> float:
    """Minimum scale so a translated/rotated frame never exposes canvas edges (§9.1)."""
    dx = dx_frac * canvas.width
    dy = dy_frac * canvas.height
    required_x = 1 + 2 * abs(dx) / canvas.width
    required_y = 1 + 2 * abs(dy) / canvas.height
    base = max(required_x, required_y)
    if rotation_deg:
        theta = math.radians(abs(rotation_deg))
        aspect = canvas.width / canvas.height
        rotation_scale = abs(math.cos(theta)) + abs(math.sin(theta)) / aspect
        base = max(base, rotation_scale)
    return base * SAFETY_MARGIN


def direction_vector_for(entry_motion: EntryMotion) -> tuple[float, float]:
    return _DIRECTION_VECTOR.get(entry_motion if entry_motion != "none" else "none", (0.0, 0.0))


def build_clip_motion(slot: TimelineSlot, canvas: Canvas, *, direction: tuple[float, float] = (0.0, 0.0)) -> Motion:
    """A single clip's own hold/push curve, independent of neighbours."""
    if slot.hold:
        scale = 1.0 + BASE_PUSH_SCALE * 0.3
        keyframes = [
            TransformKeyframe(sec=0.0, center_x=0.5, center_y=0.5, scale=scale),
            TransformKeyframe(sec=slot.duration_sec, center_x=0.5, center_y=0.5, scale=scale),
        ]
        return Motion(transform_keyframes=keyframes)

    dx, dy = direction
    target_scale = required_safe_scale(dx * TRANSLATION_UNIT, dy * TRANSLATION_UNIT, canvas)
    end_x = 0.5 - dx * TRANSLATION_UNIT * 0.5
    end_y = 0.5 - dy * TRANSLATION_UNIT * 0.5
    keyframes = [
        TransformKeyframe(sec=0.0, center_x=0.5, center_y=0.5, scale=1.0 + BASE_PUSH_SCALE * 0.2),
        TransformKeyframe(
            sec=slot.duration_sec, center_x=end_x, center_y=end_y, scale=target_scale,
        ),
    ]
    shutter = min(180.0, 40.0 + slot.target_energy * 140.0)
    blur = [
        MotionBlurKeyframe(sec=0.0, shutter_angle=shutter * 0.3),
        MotionBlurKeyframe(sec=slot.duration_sec, shutter_angle=shutter),
    ]
    return Motion(transform_keyframes=keyframes, native_motion_blur_keyframes=blur)


def build_transition_pair(
    pair_id: str,
    cut_sec: float,
    outgoing_clip_id: str,
    incoming_clip_id: str,
    entry_motion: EntryMotion,
    canvas: Canvas,
    *,
    confidence: float,
) -> TransitionPair:
    """Generate outgoing deceleration and incoming pickup from one shared decision."""
    dx, dy = direction_vector_for(entry_motion)
    direction = (
        "none" if entry_motion in ("reset", "none")
        else _direction_key(dx, dy)
    )
    safe_scale = required_safe_scale(dx * TRANSLATION_UNIT, dy * TRANSLATION_UNIT, canvas)
    overshoot = 0.04 if entry_motion == "carry" else 0.0

    # Keyframe seconds sit on the AMVSpec's absolute timeline, matching
    # Clip.timeline (§5.3) — not clip-relative — so anticipation before the
    # cut and release after it both stay >= 0 as long as the cut itself does.
    anticipation_sec = min(ANTICIPATION_SEC, cut_sec)
    outgoing_end_x = 0.5 - dx * TRANSLATION_UNIT * 0.5
    outgoing_end_y = 0.5 - dy * TRANSLATION_UNIT * 0.5
    outgoing_keyframes = [
        TransformKeyframe(sec=cut_sec - anticipation_sec, center_x=0.5, center_y=0.5, scale=1.0),
        TransformKeyframe(sec=cut_sec, center_x=outgoing_end_x, center_y=outgoing_end_y, scale=safe_scale),
    ]

    incoming_start_x = 0.5 + dx * TRANSLATION_UNIT * (0.5 if entry_motion == "carry" else -0.5)
    incoming_start_y = 0.5 + dy * TRANSLATION_UNIT * (0.5 if entry_motion == "carry" else -0.5)
    settle_scale = safe_scale * (1 + overshoot)
    incoming_keyframes = [
        TransformKeyframe(sec=cut_sec, center_x=incoming_start_x, center_y=incoming_start_y, scale=settle_scale),
        TransformKeyframe(sec=cut_sec + RELEASE_SEC, center_x=0.5, center_y=0.5, scale=1.0),
    ]

    blur_strength = min(1.0, 0.3 + confidence * 0.5) if entry_motion != "none" else 0.0
    blur_keyframes = [
        DirectionalBlurKeyframe(sec=cut_sec, angle=math.degrees(math.atan2(dy, dx)) if (dx or dy) else 0.0, strength=blur_strength),
        DirectionalBlurKeyframe(sec=cut_sec + RELEASE_SEC, angle=0.0, strength=0.0),
    ]

    return TransitionPair(
        id=pair_id, cut_sec=cut_sec,
        outgoing_clip_id=outgoing_clip_id, incoming_clip_id=incoming_clip_id,
        direction=direction,
        outgoing_keyframes=outgoing_keyframes, incoming_keyframes=incoming_keyframes,
        blur_keyframes=blur_keyframes,
        safe_scale=safe_scale, overshoot=overshoot, confidence=confidence,
    )


def _direction_key(dx: float, dy: float) -> str:
    if dx == 0 and dy == 0:
        return "none"
    angle = math.degrees(math.atan2(dy, dx))
    buckets = [
        ("right", -22.5, 22.5), ("down-right", 22.5, 67.5), ("down", 67.5, 112.5),
        ("down-left", 112.5, 157.5),
    ]
    for name, low, high in buckets:
        if low <= angle < high:
            return name
    if angle >= 157.5 or angle < -157.5:
        return "left"
    if -157.5 <= angle < -112.5:
        return "up-left"
    if -112.5 <= angle < -67.5:
        return "up"
    return "up-right"


__all__ = [
    "build_clip_motion",
    "build_transition_pair",
    "direction_vector_for",
    "required_safe_scale",
]
