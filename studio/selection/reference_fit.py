"""Reference Fit: does this candidate actually match what the Demo showed here? (REFACTOR.md §15)

Replaces the ranking engine's ``reference_fit_by_shot.get(shot_id, 0.5)``
placeholder — no caller ever filled that dict, so every candidate silently
scored a neutral 0.5 regardless of the slot's actual TimelineSlot targets.
This module compares only dimensions the slot actually has a target for
(``target_subject_scale`` etc. are optional — a Style Transfer slot that
never resolved a reference shot degrades gracefully rather than penalizing
every candidate for a target that was never measured).

Goal is "same visual role" (REFACTOR.md §15: "视觉职责相似，而不是内容完全一样"),
not pixel-identical framing.
"""
from __future__ import annotations

import json
import math
from typing import Mapping

from studio.planning.slots import TimelineSlot
from studio.selection.schemas import ShotWindow

REFERENCE_FIT_VERSION = "reference-fit-1.0.0"

_DIRECTION_TO_ROW_MOTION = {"none": "static"}
_SLOT_KIND_TO_WINDOW_KINDS = {
    "portrait": {"portrait_hold", "direct_gaze", "turn_to_camera", "hero_pose"},
    "eye": {"eye_reveal"},
    "action": {"anticipation", "action_peak", "impact", "transformation"},
    "impact": {"impact", "action_peak"},
    "hold": {"portrait_hold", "generic"},
}


def _hex_to_rgb(value: str) -> tuple[float, float, float] | None:
    text = value.lstrip("#")
    if len(text) != 6:
        return None
    try:
        return tuple(int(text[i : i + 2], 16) / 255.0 for i in (0, 2, 4))  # type: ignore[return-value]
    except ValueError:
        return None


def _color_similarity(a: str | None, b: str | None) -> float | None:
    if not a or not b:
        return None
    rgb_a, rgb_b = _hex_to_rgb(a), _hex_to_rgb(b)
    if rgb_a is None or rgb_b is None:
        return None
    distance = math.sqrt(sum((x - y) ** 2 for x, y in zip(rgb_a, rgb_b)))
    return max(0.0, 1.0 - distance / math.sqrt(3))


def _motion_match(target: str, actual: str | None) -> float | None:
    if target == "none":
        return None  # no preference recorded; do not penalize/reward
    actual_bucket = "none" if actual in (None, "static") else actual
    return 1.0 if actual_bucket == target else 0.0


def compute_reference_fit(
    slot: TimelineSlot,
    shot_row: Mapping,
    *,
    window: ShotWindow | None = None,
) -> float:
    components: dict[str, float] = {}

    components["visual_energy"] = 1.0 - abs(
        _as_float(shot_row.get("visual_energy"), 0.5) - slot.target_energy
    )

    if slot.target_subject_scale is not None:
        components["subject_scale"] = 1.0 - abs(
            _as_float(shot_row.get("shot_scale"), slot.target_subject_scale) - slot.target_subject_scale
        )
    if slot.target_shot_scale is not None:
        components["shot_scale"] = 1.0 - abs(
            _as_float(shot_row.get("shot_scale"), slot.target_shot_scale) - slot.target_shot_scale
        )
    if slot.target_brightness is not None:
        components["brightness"] = 1.0 - abs(
            _as_float(shot_row.get("brightness"), slot.target_brightness) - slot.target_brightness
        )
    if slot.target_dominant_color is not None:
        palette = _top_palette_color(shot_row.get("color_palette"))
        similarity = _color_similarity(slot.target_dominant_color, palette)
        if similarity is not None:
            components["dominant_color"] = similarity
    if slot.source_motion_preference != "none":
        match = _motion_match(slot.source_motion_preference, shot_row.get("motion_dir"))
        if match is not None:
            components["motion_direction"] = match
    if window is not None and slot.slot_kind in _SLOT_KIND_TO_WINDOW_KINDS:
        components["window_kind"] = float(
            window.kind in _SLOT_KIND_TO_WINDOW_KINDS[slot.slot_kind]
        )
    if window is not None:
        target_stable = 1.0 if slot.hold or slot.required_stable_frames > 0 else 0.5
        components["stable_ratio"] = 1.0 - abs(_stable_proxy(window) - target_stable)

    if not components:
        return 0.5
    return max(0.0, min(1.0, sum(components.values()) / len(components)))


def _stable_proxy(window: ShotWindow) -> float:
    if window.action.action_score > 0:
        return window.action.landing_score
    return window.portrait.temporal_stability if window.portrait.face_visible_ratio > 0 else 0.5


def _as_float(value: object, default: float) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _top_palette_color(raw: object) -> str | None:
    if not raw:
        return None
    try:
        palette = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return None
    return palette[0] if isinstance(palette, list) and palette else None


__all__ = ["REFERENCE_FIT_VERSION", "compute_reference_fit"]
