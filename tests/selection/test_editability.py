"""Editability scoring (REFACTOR.md §12): a hot action window with no located
landing must still be marked unfit for a normal slot."""
from __future__ import annotations

from studio.selection.editability import compute_editability
from studio.selection.schemas import ActionProfile


def test_action_without_landing_scores_low_editability():
    """REFACTOR.md §12: a window can be action_score=0.95 and
    editability_score low at once — fit only for a short insert, not a
    normal slot. That combination needs *both* signals: real action with no
    located landing, and (as a short insert naturally has) little runway."""
    action = ActionProfile(action_score=0.95, landing_sec=None)
    profile = compute_editability(
        shot_start_sec=1.0, shot_end_sec=1.5,
        window_start_sec=1.0, window_end_sec=1.5,
        target_duration_sec=0.5,
        safe_crop_ratio=0.9, action=action,
    )
    assert profile.editability_score < 0.6


def test_action_with_clean_landing_scores_high_editability():
    action = ActionProfile(
        action_score=0.9, landing_sec=1.4, anticipation_sec=1.0, landing_score=0.95,
    )
    profile = compute_editability(
        shot_start_sec=0.0, shot_end_sec=5.0,
        window_start_sec=1.0, window_end_sec=1.6,
        target_duration_sec=0.6,
        safe_crop_ratio=0.95, action=action,
    )
    assert profile.editability_score > 0.7


def test_held_portrait_window_with_no_handles_scores_lower_than_one_with_handles():
    no_handles = compute_editability(
        shot_start_sec=1.0, shot_end_sec=1.4,
        window_start_sec=1.0, window_end_sec=1.4,
        target_duration_sec=0.4, safe_crop_ratio=0.9,
    )
    with_handles = compute_editability(
        shot_start_sec=0.0, shot_end_sec=3.0,
        window_start_sec=1.0, window_end_sec=1.4,
        target_duration_sec=0.4, safe_crop_ratio=0.9,
    )
    assert with_handles.editability_score > no_handles.editability_score


def test_opposing_motion_direction_penalized():
    compatible = compute_editability(
        shot_start_sec=0.0, shot_end_sec=3.0,
        window_start_sec=1.0, window_end_sec=1.4, target_duration_sec=0.4,
        entry_motion="left", preferred_entry="left",
    )
    opposing = compute_editability(
        shot_start_sec=0.0, shot_end_sec=3.0,
        window_start_sec=1.0, window_end_sec=1.4, target_duration_sec=0.4,
        entry_motion="left", preferred_entry="right",
    )
    assert compatible.motion_compatibility > opposing.motion_compatibility
