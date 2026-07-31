"""Slot-conditioned scoring (REFACTOR.md §14, §22.5): the same candidate pool
must rank differently depending on what the slot actually needs."""
from __future__ import annotations

from studio.planning.slots import TimelineSlot
from studio.selection.schemas import ActionProfile, PortraitProfile, ShotWindow, TechnicalProfile
from studio.selection.sequence_scoring import score_window_for_slot


def _window(window_id, *, kind="generic", portrait=None, action=None, passed=True):
    return ShotWindow(
        id=window_id, shot_id="s1", asset_id="a1",
        start_sec=0.0, end_sec=1.0, anchor_sec=0.5, kind=kind,
        technical=TechnicalProfile(passed=passed),
        portrait=portrait or PortraitProfile(),
        action=action or ActionProfile(),
    )


def _slot(kind):
    return TimelineSlot(index=0, start_sec=0.0, duration_sec=1.0, target_energy=0.5, slot_kind=kind)


def test_technical_failure_scores_zero_regardless_of_content():
    window = _window("w", kind="direct_gaze", portrait=PortraitProfile(portrait_score=0.99), passed=False)
    score, _ = score_window_for_slot(window, _slot("portrait"))
    assert score == 0.0


def test_portrait_slot_prefers_high_portrait_score():
    strong_face = _window("face", kind="direct_gaze", portrait=PortraitProfile(
        portrait_score=0.9, eye_visible_ratio=0.9, temporal_stability=0.9,
    ))
    strong_action = _window("action", kind="action_peak", action=ActionProfile(
        action_score=0.9, residual_motion_peak=6.0, landing_score=0.9,
    ))
    portrait_slot = _slot("portrait")
    face_score, _ = score_window_for_slot(strong_face, portrait_slot)
    action_score, _ = score_window_for_slot(strong_action, portrait_slot)
    assert face_score > action_score


def test_impact_slot_prefers_high_action_score():
    strong_face = _window("face", kind="direct_gaze", portrait=PortraitProfile(
        portrait_score=0.9, eye_visible_ratio=0.9, temporal_stability=0.9,
    ))
    strong_action = _window("action", kind="action_peak", action=ActionProfile(
        action_score=0.9, residual_motion_peak=6.0, landing_score=0.9,
    ))
    impact_slot = _slot("impact")
    face_score, _ = score_window_for_slot(strong_face, impact_slot)
    action_score, _ = score_window_for_slot(strong_action, impact_slot)
    assert action_score > face_score


def test_global_score_does_not_override_slot_conditioning():
    """A single fixed weight vector could not produce both rankings above for
    the same two candidates; slot-conditioning must actually flip the order."""
    strong_face = _window("face", kind="direct_gaze", portrait=PortraitProfile(portrait_score=0.9))
    strong_action = _window("action", kind="action_peak", action=ActionProfile(action_score=0.9))
    portrait_order = score_window_for_slot(strong_face, _slot("portrait"))[0] > score_window_for_slot(
        strong_action, _slot("portrait")
    )[0]
    impact_order = score_window_for_slot(strong_action, _slot("impact"))[0] > score_window_for_slot(
        strong_face, _slot("impact")
    )[0]
    assert portrait_order and impact_order
