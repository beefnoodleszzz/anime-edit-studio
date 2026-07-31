"""Editability scoring for a candidate ShotWindow (REFACTOR.md §12).

A window can score very high on action or portrait and still be unusable
for a normal slot if it has no runway before/after it inside its parent
Shot, no clean landing to cut on, or an entry/exit direction that fights
the neighbouring clip's motion. This is judged independently of
action/portrait score, matching REFACTOR.md's own example: a window can be
``action_score=0.95`` and ``editability_score=0.25`` at the same time — fit
only for a short insert, not a normal slot.
"""
from __future__ import annotations

import math

from studio.selection.schemas import ActionProfile, EditabilityProfile, MotionDirection

EDITABILITY_VERSION = "editability-1.0.0"

_DIRECTION_VECTORS: dict[MotionDirection, tuple[float, float]] = {
    "none": (0.0, 0.0),
    "left": (-1.0, 0.0),
    "right": (1.0, 0.0),
    "up": (0.0, -1.0),
    "down": (0.0, 1.0),
    "up-left": (-0.7071, -0.7071),
    "up-right": (0.7071, -0.7071),
    "down-left": (-0.7071, 0.7071),
    "down-right": (0.7071, 0.7071),
}

_WEIGHTS = {
    "handles": 0.25,
    "landing_or_hold_stability": 0.25,
    "safe_crop": 0.20,
    "motion_compatibility": 0.20,
    "duration_fit": 0.10,
}


def _direction_similarity(a: MotionDirection, b: MotionDirection) -> float:
    if a == "none" or b == "none":
        return 0.75
    va, vb = _DIRECTION_VECTORS[a], _DIRECTION_VECTORS[b]
    cosine = va[0] * vb[0] + va[1] * vb[1]
    return max(0.0, min(1.0, (cosine + 1.0) / 2.0))


def compute_editability(
    *,
    shot_start_sec: float,
    shot_end_sec: float,
    window_start_sec: float,
    window_end_sec: float,
    target_duration_sec: float,
    safe_crop_ratio: float = 0.5,
    action: ActionProfile | None = None,
    entry_motion: MotionDirection = "none",
    exit_motion: MotionDirection = "none",
    preferred_entry: MotionDirection = "none",
    preferred_exit: MotionDirection = "none",
) -> EditabilityProfile:
    handle_before_sec = max(0.0, window_start_sec - shot_start_sec)
    handle_after_sec = max(0.0, shot_end_sec - window_end_sec)
    handles_score = 1.0 - math.exp(-(handle_before_sec + handle_after_sec) / 0.6)

    if action is not None and action.landing_sec is not None:
        stable_after_sec = max(0.0, window_end_sec - action.landing_sec)
        stable_before_sec = (
            max(0.0, action.anticipation_sec - window_start_sec)
            if action.anticipation_sec is not None
            else 0.0
        )
        landing_or_hold_stability = action.landing_score
    elif action is not None and action.action_score > 0.0:
        # Real action with no located landing: editable only as a short
        # insert, not a stable hold — REFACTOR.md §12's action_score=0.95 /
        # editability_score=0.25 example.
        stable_before_sec = stable_after_sec = 0.0
        landing_or_hold_stability = 0.1
    else:
        # No action signal at all: treat the whole window as a held,
        # already-stable composition (portrait/hold windows).
        stable_before_sec = handle_before_sec
        stable_after_sec = handle_after_sec
        landing_or_hold_stability = 1.0

    duration = window_end_sec - window_start_sec
    duration_fit = 1.0 - min(
        1.0, abs(duration - target_duration_sec) / max(target_duration_sec, duration, 1e-6)
    )

    motion_compatibility = 0.5 * _direction_similarity(
        entry_motion, preferred_entry
    ) + 0.5 * _direction_similarity(exit_motion, preferred_exit)

    values = {
        "handles": handles_score,
        "landing_or_hold_stability": landing_or_hold_stability,
        "safe_crop": safe_crop_ratio,
        "motion_compatibility": motion_compatibility,
        "duration_fit": duration_fit,
    }
    editability_score = sum(values[key] * weight for key, weight in _WEIGHTS.items())

    return EditabilityProfile(
        handle_before_sec=handle_before_sec,
        handle_after_sec=handle_after_sec,
        stable_before_sec=stable_before_sec,
        stable_after_sec=stable_after_sec,
        entry_motion=entry_motion,
        exit_motion=exit_motion,
        motion_compatibility=max(0.0, min(1.0, motion_compatibility)),
        duration_fit=max(0.0, min(1.0, duration_fit)),
        editability_score=max(0.0, min(1.0, editability_score)),
    )


__all__ = ["EDITABILITY_VERSION", "compute_editability"]
