"""Slot-conditioned ShotWindow scoring (REFACTOR.md §14).

No single fixed weight vector serves every slot: a Portrait slot must
reward direct_gaze/eye-visibility, an Action/Impact slot must reward
residual motion and Hero Landing, a Transition slot cares about motion
compatibility and safe crop, and a Hold slot wants low residual motion and
a clean, stable frame. All slots still always fold in
technical_pass/identity_fit/series_scope_fit/reference_fit/music_fit/
editability (REFACTOR.md §14's closing list) — ``sequence_fit`` (beam
transition continuity) is scored separately by the sequence planner across
the whole beam, not per-window here.
"""
from __future__ import annotations

import math

from studio.planning.slots import TimelineSlot
from studio.selection.schemas import ShotWindow

SEQUENCE_SCORING_VERSION = "sequence-scoring-1.0.0"

_ALWAYS_WEIGHTS = {
    "intrinsic": 0.40,
    "reference_fit": 0.15,
    "music_fit": 0.15,
    "editability": 0.15,
    "identity_fit": 0.075,
    "series_scope_fit": 0.075,
}


def _fit(a: float | None, b: float | None) -> float:
    return 1.0 - abs(a - b) if a is not None and b is not None else 0.5


def _center_fit(a: tuple[float, float] | None, b: tuple[float, float] | None) -> float:
    if a is None or b is None:
        return 0.5
    distance = math.hypot(a[0] - b[0], a[1] - b[1])
    return max(0.0, 1.0 - distance / math.sqrt(2))


def _portrait_intrinsic(window: ShotWindow, slot: TimelineSlot) -> dict[str, float]:
    gaze_kinds = {"direct_gaze", "eye_reveal", "turn_to_camera"}
    return {
        "portrait_score": window.portrait.portrait_score,
        "direct_gaze_bonus": 1.0 if window.kind in gaze_kinds else 0.5,
        "eye_visibility": window.portrait.eye_visible_ratio,
        "expression": window.portrait.expression_strength,
        "subject_scale_fit": _fit(window.subject.subject_scale, slot.target_subject_scale),
        "subject_center_fit": _center_fit(window.subject.subject_center, slot.target_subject_center),
        "temporal_stability": window.portrait.temporal_stability,
    }


_PORTRAIT_WEIGHTS = {
    "portrait_score": 0.30, "direct_gaze_bonus": 0.15, "eye_visibility": 0.15,
    "expression": 0.10, "subject_scale_fit": 0.10, "subject_center_fit": 0.10,
    "temporal_stability": 0.10,
}


def _action_intrinsic(window: ShotWindow) -> dict[str, float]:
    # anchor_sec is a source-media timestamp, not a target-timeline one, so
    # "how well the peak aligns with a beat once placed" is a timeline
    # concern (motion_planner/amv_spec_builder), not assessable here. This
    # module instead scores how centred the peak sits in the window — a
    # proxy for how much retime room there is to align it to a beat later.
    span = window.duration_sec
    centered = 1.0 - min(1.0, abs((window.anchor_sec - window.start_sec) - span / 2) / max(span / 2, 1e-6))
    return {
        "action_score": window.action.action_score,
        "impact_alignment": centered,
        "residual_motion": min(1.0, window.action.residual_motion_peak / 8.0),
        "acceleration": min(1.0, window.action.acceleration_peak / 8.0),
        "subject_visibility": window.technical.subject_visible_ratio,
        "hero_landing": window.action.landing_score,
    }


_ACTION_WEIGHTS = {
    "action_score": 0.25, "impact_alignment": 0.15, "residual_motion": 0.15,
    "acceleration": 0.15, "subject_visibility": 0.15, "hero_landing": 0.15,
}


def _transition_intrinsic(window: ShotWindow, slot: TimelineSlot) -> dict[str, float]:
    stable_landing = (
        window.action.landing_score if window.action.action_score > 0 else window.portrait.temporal_stability
    )
    return {
        "entry_exit_compatibility": window.editability.motion_compatibility,
        "safe_crop": window.subject.safe_crop_ratio,
        "stable_landing": stable_landing,
        "subject_position": _center_fit(window.subject.subject_center, slot.target_subject_center),
    }


_TRANSITION_WEIGHTS = {
    "entry_exit_compatibility": 0.35, "safe_crop": 0.25,
    "stable_landing": 0.25, "subject_position": 0.15,
}


def _hold_intrinsic(window: ShotWindow) -> dict[str, float]:
    composition = (
        window.portrait.composition_score if window.portrait.face_visible_ratio > 0 else window.subject.safe_crop_ratio
    )
    return {
        "portrait_or_hero_bonus": (
            window.portrait.portrait_score if window.kind in {"portrait_hold", "hero_pose"} else 0.5
        ),
        "low_residual_motion": 1.0 - min(1.0, window.action.residual_motion_peak / 8.0),
        "stable_composition": composition,
        "clean_frame": 1.0 - window.technical.bad_frame_ratio,
    }


_HOLD_WEIGHTS = {
    "portrait_or_hero_bonus": 0.30, "low_residual_motion": 0.25,
    "stable_composition": 0.25, "clean_frame": 0.20,
}


def _weighted(values: dict[str, float], weights: dict[str, float]) -> float:
    return sum(values[key] * weight for key, weight in weights.items())


def _intrinsic_for_slot(window: ShotWindow, slot: TimelineSlot) -> tuple[float, dict[str, float]]:
    if slot.slot_kind == "portrait":
        values = _portrait_intrinsic(window, slot)
        return _weighted(values, _PORTRAIT_WEIGHTS), values
    if slot.slot_kind in ("action", "impact"):
        values = _action_intrinsic(window)
        return _weighted(values, _ACTION_WEIGHTS), values
    if slot.slot_kind == "transition":
        values = _transition_intrinsic(window, slot)
        return _weighted(values, _TRANSITION_WEIGHTS), values
    if slot.slot_kind == "hold":
        values = _hold_intrinsic(window)
        return _weighted(values, _HOLD_WEIGHTS), values
    # No REFACTOR.md §14 weight profile for "eye"/"generic": blend portrait
    # and action evenly rather than inventing an unspecified profile.
    portrait_values = _portrait_intrinsic(window, slot)
    action_values = _action_intrinsic(window)
    blended = 0.5 * _weighted(portrait_values, _PORTRAIT_WEIGHTS) + 0.5 * _weighted(
        action_values, _ACTION_WEIGHTS
    )
    return blended, {**portrait_values, **action_values}


def score_window_for_slot(
    window: ShotWindow,
    slot: TimelineSlot,
    *,
    reference_fit: float = 0.5,
    music_fit: float = 0.5,
) -> tuple[float, dict[str, float]]:
    """Returns (total_score, component_breakdown). ``total_score`` is 0.0 if
    the window failed the technical hard gate — no soft score rescues it."""
    if not window.technical.passed:
        return 0.0, {"technical_pass": 0.0}

    intrinsic, intrinsic_components = _intrinsic_for_slot(window, slot)

    identity_fit = (
        1.0
        if slot.required_identity is None
        or window.subject.identity_cluster is None
        or slot.required_identity == window.subject.identity_cluster
        else 0.0
    )
    series_scope_fit = (
        1.0
        if slot.required_series_scope is None
        or window.subject.series_scope is None
        or slot.required_series_scope == window.subject.series_scope
        else 0.0
    )

    components = {
        "intrinsic": intrinsic,
        "reference_fit": reference_fit,
        "music_fit": music_fit,
        "editability": window.editability.editability_score,
        "identity_fit": identity_fit,
        "series_scope_fit": series_scope_fit,
    }
    total = sum(components[key] * weight for key, weight in _ALWAYS_WEIGHTS.items())
    return max(0.0, min(1.0, total)), {**intrinsic_components, **components}


__all__ = ["SEQUENCE_SCORING_VERSION", "score_window_for_slot"]
