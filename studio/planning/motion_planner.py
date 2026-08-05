"""Unified Motion Planner: continuous cross-cut motion.

Produces per-clip ``Motion`` (a simple hold/push curve honoring the slot's
energy and hold flag) and per-cut ``TransitionPair`` keyframes whose
outgoing and incoming curves are generated together, so an outgoing clip
decelerating into a cut and the incoming clip picking up its direction are
never independent decisions.

``relation`` (carry/reverse/reset/none — the Demo's qualitative cut-to-cut
relationship) and ``direction`` (an actual measured 9-way screen direction)
are deliberately two separate parameters here, not one. ``relation`` alone
carries no geometry (it is not a member of ``MotionDirection``); feeding it
into a direction lookup — as this module used to — silently resolves to
"not found" and produces a motionless transition regardless of what the cut
was supposed to look like.

``safe_scale`` follows a coverage estimate plus a rotation-aware corner
check, with a safety margin so panned/rotated frames never expose canvas
edges.
"""
from __future__ import annotations

import math

from studio.planning.schemas import DIRECTION_VECTORS, MotionDirection
from studio.planning.slots import EntryMotion, TimelineSlot
from studio.planning.transition_profile import TransitionProfile
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

# A push/transition sized for a ~0.6s hold reads as unnatural whip-speed
# motion blur when the same magnitude gets crammed into a much shorter
# clip (found running a real fast-cut AMV: most clips averaged ~0.65s but
# anticipation+release windows averaged ~0.6s combined, leaving almost no
# clip ever showing a readable, un-blurred frame). Both the base per-clip
# push and the transition windows scale down for clips shorter than this
# reference instead of always running at full designed magnitude/duration.
REFERENCE_DURATION_SEC = 0.6
MAX_TRANSITION_FRACTION = 0.35

# Motion blur must reflect the transform curve's actual (constant) velocity,
# not an independent time-based ramp: build_clip_motion's transform is a
# single linear interpolation from start to end, i.e. one constant speed for
# the whole clip. A shutter angle that escalates 0.3x -> 1x across the same
# duration has no mechanical basis and was found (via dense per-frame
# sharpness measurement on a real render) to decay a clip's readability by
# ~60x from its sharpest to its final frame even though nothing sped up.
# translation_unit is itself duration-damped for clips under
# REFERENCE_DURATION_SEC (see _duration_damped), which cancels out of this
# velocity ratio: every sub-reference clip lands on the exact same
# velocity (TRANSLATION_UNIT / REFERENCE_DURATION_SEC), and clips at/above
# the reference can only approach that same value from below. That single
# number is therefore the true ceiling this system can ever produce, by
# construction — and it was calibrated to sit within a few degrees of
# MAX_SHUTTER_ANGLE, so nearly every clip in a real fast-cut edit (most
# clips run well under 0.6s) landed pinned at the cap. Found visually
# inspecting a real render after the transition-blur fix still looked
# heavily smeared *outside* any transition window — the base per-clip
# blur itself, not just transitions, was the remaining source. Recalibrated
# so that ceiling velocity produces a moderate, not maximal, shutter angle.
BASE_SHUTTER_ANGLE = 8.0
SHUTTER_VELOCITY_GAIN = 220.0
MAX_SHUTTER_ANGLE = 90.0


def _duration_damped(value: float, duration_sec: float) -> float:
    return value * min(1.0, duration_sec / REFERENCE_DURATION_SEC)


def required_safe_scale(
    dx_frac: float, dy_frac: float, canvas: Canvas, *, rotation_deg: float = 0.0,
) -> float:
    """Minimum scale so a translated/rotated frame never exposes canvas edges."""
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


def direction_vector_for(direction: MotionDirection) -> tuple[float, float]:
    return DIRECTION_VECTORS[direction]


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
    translation_unit = _duration_damped(TRANSLATION_UNIT, slot.duration_sec)
    target_scale = required_safe_scale(dx * translation_unit, dy * translation_unit, canvas)
    end_x = 0.5 - dx * translation_unit * 0.5
    end_y = 0.5 - dy * translation_unit * 0.5
    keyframes = [
        TransformKeyframe(sec=0.0, center_x=0.5, center_y=0.5, scale=1.0 + BASE_PUSH_SCALE * 0.2),
        TransformKeyframe(
            sec=slot.duration_sec, center_x=end_x, center_y=end_y, scale=target_scale,
        ),
    ]
    velocity = translation_unit / max(slot.duration_sec, 1e-6)
    shutter = min(MAX_SHUTTER_ANGLE, BASE_SHUTTER_ANGLE + velocity * SHUTTER_VELOCITY_GAIN)
    blur = [
        MotionBlurKeyframe(sec=0.0, shutter_angle=shutter),
        MotionBlurKeyframe(sec=slot.duration_sec, shutter_angle=shutter),
    ]
    return Motion(transform_keyframes=keyframes, native_motion_blur_keyframes=blur)


def build_transition_pair(
    pair_id: str,
    cut_sec: float,
    outgoing_clip_id: str,
    incoming_clip_id: str,
    *,
    relation: EntryMotion,
    direction: MotionDirection,
    canvas: Canvas,
    confidence: float,
    profile: TransitionProfile | None = None,
    outgoing_duration_sec: float | None = None,
    incoming_duration_sec: float | None = None,
) -> TransitionPair:
    """Generate outgoing deceleration and incoming pickup from one shared
    decision. ``relation`` decides the qualitative shape (carry continues
    into the same side, reverse/reset pick up from the opposite side, only
    carry overshoots); ``direction`` is the actual measured screen direction
    driving the geometry — normally the outgoing clip's own exit motion, so
    the transition looks like a continuation of what was already on screen,
    not an invented direction.

    ``profile`` (``studio.planning.transition_profile``), when given and
    ``.usable``, replaces the fixed anticipation/release/translation/
    overshoot constants with values actually measured from the Demo's own
    transitions of this relation, and adds an "attack" keyframe on each side
    placed at the Demo's own measured attack timing rather than always the
    midpoint. An unusable (low-confidence) profile falls back to a plain
    hard cut — no direction, no overshoot — rather than inventing a
    transition shape from noise.

    ``outgoing_duration_sec``/``incoming_duration_sec`` (the actual clip
    each side of the cut occupies on the timeline) cap the anticipation/
    release windows and damp the translation magnitude for short clips:
    without this, a fast-cut sequence (Demo shots averaging well under a
    second) spends almost its entire runtime inside the transition ramp on
    both sides of every cut, so the viewer never sees a settled, readable
    frame — found running a real fast-cut AMV, not a hypothetical."""
    if profile is not None and not profile.usable:
        relation, direction = "reset", "none"
    effect_kind = profile.effect_kind if profile is not None and profile.usable else "none"

    anticipation_budget = profile.anticipation_sec if profile is not None else ANTICIPATION_SEC
    release_budget = profile.release_sec if profile is not None else RELEASE_SEC
    translation_unit = profile.translation_unit if profile is not None else TRANSLATION_UNIT
    attack_fraction = profile.attack_fraction if profile is not None else 0.5

    if outgoing_duration_sec is not None:
        anticipation_budget = min(anticipation_budget, outgoing_duration_sec * MAX_TRANSITION_FRACTION)
    if incoming_duration_sec is not None:
        release_budget = min(release_budget, incoming_duration_sec * MAX_TRANSITION_FRACTION)
    shortest_side = min(
        d for d in (outgoing_duration_sec, incoming_duration_sec) if d is not None
    ) if (outgoing_duration_sec is not None or incoming_duration_sec is not None) else REFERENCE_DURATION_SEC
    translation_unit = _duration_damped(translation_unit, shortest_side)

    dx, dy = direction_vector_for(direction if relation != "reset" else "none")
    output_direction = "none" if relation in ("reset", "none") else direction
    safe_scale = required_safe_scale(dx * translation_unit, dy * translation_unit, canvas)
    overshoot = (profile.overshoot if profile is not None else 0.04) if relation == "carry" else 0.0

    # Keyframe seconds sit on the AMVSpec's absolute timeline, matching
    # Clip.timeline — not clip-relative — so anticipation before the cut and
    # release after it both stay >= 0 as long as the cut itself does.
    anticipation_sec = min(anticipation_budget, cut_sec)
    outgoing_start_sec = cut_sec - anticipation_sec
    outgoing_end_x = 0.5 - dx * translation_unit * 0.5
    outgoing_end_y = 0.5 - dy * translation_unit * 0.5
    outgoing_attack_sec = outgoing_start_sec + attack_fraction * anticipation_sec
    outgoing_keyframes = [
        TransformKeyframe(sec=outgoing_start_sec, center_x=0.5, center_y=0.5, scale=1.0),
        TransformKeyframe(
            sec=outgoing_attack_sec,
            center_x=0.5 + (outgoing_end_x - 0.5) * attack_fraction,
            center_y=0.5 + (outgoing_end_y - 0.5) * attack_fraction,
            scale=1.0 + (safe_scale - 1.0) * attack_fraction,
        ),
        TransformKeyframe(sec=cut_sec, center_x=outgoing_end_x, center_y=outgoing_end_y, scale=safe_scale),
    ]

    incoming_start_x = 0.5 + dx * translation_unit * (0.5 if relation == "carry" else -0.5)
    incoming_start_y = 0.5 + dy * translation_unit * (0.5 if relation == "carry" else -0.5)
    settle_scale = safe_scale * (1 + overshoot)
    settle_fraction = 1.0 - attack_fraction
    incoming_settle_sec = cut_sec + settle_fraction * release_budget
    incoming_keyframes = [
        TransformKeyframe(sec=cut_sec, center_x=incoming_start_x, center_y=incoming_start_y, scale=settle_scale),
        TransformKeyframe(
            sec=incoming_settle_sec,
            center_x=incoming_start_x + (0.5 - incoming_start_x) * settle_fraction,
            center_y=incoming_start_y + (0.5 - incoming_start_y) * settle_fraction,
            scale=settle_scale + (1.0 - settle_scale) * settle_fraction,
        ),
        TransformKeyframe(sec=cut_sec + release_budget, center_x=0.5, center_y=0.5, scale=1.0),
    ]

    blur_strength = min(1.0, 0.3 + confidence * 0.5) if relation != "none" else 0.0
    blur_angle = math.degrees(math.atan2(dy, dx)) if (dx or dy) else 0.0
    # A zero-strength point at the anticipation window's own start, not
    # just the cut and the release: without it, the outgoing clip's blur
    # curve only ever defines the peak — Fusion's compiled spline has
    # nothing before that peak to interpolate from, so it either holds
    # the peak constant across the whole clip (found reading back a real
    # render's connected spline) or, once the compiler papers over that by
    # anchoring zero at the clip's absolute frame 0, ramps in slowly from
    # the clip's start instead of sharply within the real anticipation
    # window. anticipation_sec is already capped to a fraction of the
    # outgoing clip's own duration, so outgoing_start_sec always falls
    # inside that clip.
    blur_keyframes = [
        DirectionalBlurKeyframe(sec=outgoing_start_sec, angle=0.0, strength=0.0),
        DirectionalBlurKeyframe(sec=cut_sec, angle=blur_angle, strength=blur_strength),
        DirectionalBlurKeyframe(sec=cut_sec + release_budget, angle=0.0, strength=0.0),
    ]

    return TransitionPair(
        id=pair_id, cut_sec=cut_sec,
        outgoing_clip_id=outgoing_clip_id, incoming_clip_id=incoming_clip_id,
        direction=output_direction,
        outgoing_keyframes=outgoing_keyframes, incoming_keyframes=incoming_keyframes,
        blur_keyframes=blur_keyframes,
        safe_scale=safe_scale, overshoot=overshoot, confidence=confidence,
        effect_kind=effect_kind,
    )


__all__ = [
    "build_clip_motion",
    "build_transition_pair",
    "direction_vector_for",
    "required_safe_scale",
]
