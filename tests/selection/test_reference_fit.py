"""Reference Fit replaces the always-0.5 placeholder (REFACTOR.md §15)."""
from __future__ import annotations

from studio.planning.slots import TimelineSlot
from studio.selection.reference_fit import compute_reference_fit


def _slot(**overrides) -> TimelineSlot:
    fields = dict(index=0, start_sec=0.0, duration_sec=1.0, target_energy=0.5)
    fields.update(overrides)
    return TimelineSlot(**fields)


def test_matching_shot_scores_higher_than_mismatched():
    slot = _slot(target_energy=0.8, target_brightness=0.8, target_subject_scale=0.6)
    matching_row = {"visual_energy": 0.8, "brightness": 0.8, "shot_scale": 0.6}
    mismatched_row = {"visual_energy": 0.1, "brightness": 0.1, "shot_scale": 0.05}
    assert compute_reference_fit(slot, matching_row) > compute_reference_fit(slot, mismatched_row)


def test_no_targets_set_returns_neutral_when_row_also_empty():
    slot = _slot(target_energy=0.5)
    # Only the always-present target_energy is compared; a shot at the same
    # energy should score at (or very near) the ceiling for that one axis.
    fit = compute_reference_fit(slot, {"visual_energy": 0.5})
    assert fit == 1.0


def test_motion_direction_mismatch_penalized():
    slot = _slot(source_motion_preference="left")
    matching = compute_reference_fit(slot, {"visual_energy": 0.5, "motion_dir": "left"})
    mismatched = compute_reference_fit(slot, {"visual_energy": 0.5, "motion_dir": "right"})
    assert matching > mismatched


def test_dominant_color_similarity():
    slot = _slot(target_dominant_color="#ff0000")
    close = compute_reference_fit(slot, {"visual_energy": 0.5, "color_palette": '["#fe0101"]'})
    far = compute_reference_fit(slot, {"visual_energy": 0.5, "color_palette": '["#0000ff"]'})
    assert close > far
