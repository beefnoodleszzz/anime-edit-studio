"""Beam Search beats per-slot greedy (beam_width=1 vs >1 on the same
candidate pools): a locally-best first pick can force a later slot into
reusing an overlapping source range; keeping more than one beam alive can
avoid that reuse and reach a higher total score. Uses a monkeypatched
candidate provider so the scenario is fully controlled and fast — the
pipeline that actually produces ScoredWindow objects is exercised
end-to-end by tests/planning/test_global_sequence_planner.py."""
from __future__ import annotations

import sqlite3

import studio.planning.global_sequence_planner as planner
from studio.planning.candidates import ScoredWindow
from studio.planning.schemas import ShotWindow, TechnicalProfile


def _window(window_id, *, asset_id, start, end):
    return ShotWindow(
        id=window_id, shot_id=window_id, asset_id=asset_id,
        start_sec=start, end_sec=end, anchor_sec=(start + end) / 2,
        technical=TechnicalProfile(passed=True),
    )


def _scored(window_id, *, asset_id, start, end, score):
    return ScoredWindow(
        window=_window(window_id, asset_id=asset_id, start=start, end=end),
        score=score, components={},
    )


# Slot 0: W1 (asset A) is the best pick; W2 (asset B) is a close second.
_SLOT0_CANDIDATES = [
    _scored("w1", asset_id="A", start=0.0, end=1.0, score=0.9),
    _scored("w2", asset_id="B", start=0.0, end=1.0, score=0.85),
]
# Slot 1: only one candidate exists, and it heavily overlaps W1's source
# range (same asset A) but not W2's (asset B).
_SLOT1_CANDIDATES = [
    _scored("w3", asset_id="A", start=0.3, end=1.3, score=0.7),
]


def test_wider_beam_avoids_forced_source_overlap_and_scores_higher(monkeypatch):
    def fake_candidates_for_slot(conn, slot, shot_ids, *, limit=30):
        return _SLOT0_CANDIDATES if slot.index == 0 else _SLOT1_CANDIDATES

    monkeypatch.setattr(planner, "candidates_for_slot", fake_candidates_for_slot)

    from studio.planning.slots import TimelineSlot

    slots = [
        TimelineSlot(index=0, start_sec=0.0, duration_sec=1.0, target_energy=0.5),
        TimelineSlot(index=1, start_sec=1.0, duration_sec=1.0, target_energy=0.5),
    ]

    conn = sqlite3.connect(":memory:")
    ignored_ids = ["ignored"]
    greedy = planner.plan_sequence(conn, slots, project_id="p", available_shot_ids=ignored_ids, beam_width=1)
    wide = planner.plan_sequence(conn, slots, project_id="p", available_shot_ids=ignored_ids, beam_width=4)

    # Greedy commits to W1 first, then has no non-overlapping option left for
    # slot 1 and is forced to reuse asset A's overlapping range.
    assert greedy[0].asset_id == "A"
    assert greedy[1].asset_id == "A"
    greedy_total = sum(c.score for c in greedy)

    # A wider beam keeps the (slightly worse) W2 path alive and reaches slot 1
    # with a genuinely non-overlapping candidate available, avoiding reuse
    # entirely and ending with a higher total score.
    assert wide[0].asset_id == "B"
    assert wide[1].asset_id == "A"
    wide_total = sum(c.score for c in wide)

    assert wide_total > greedy_total
