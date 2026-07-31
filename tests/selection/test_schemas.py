"""ShotWindow schema round-trip and invariants (REFACTOR.md §5.1)."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from studio.selection.schemas import ShotWindow, TechnicalProfile


def _window(**overrides) -> ShotWindow:
    fields = dict(
        id="w1",
        shot_id="s1",
        asset_id="a1",
        start_sec=1.0,
        end_sec=2.0,
        anchor_sec=1.5,
        technical=TechnicalProfile(passed=True),
    )
    fields.update(overrides)
    return ShotWindow(**fields)


def test_round_trip_via_json():
    window = _window(kind="direct_gaze")
    restored = ShotWindow.model_validate_json(window.model_dump_json())
    assert restored == window
    assert restored.duration_sec == pytest.approx(1.0)


def test_end_sec_must_exceed_start_sec():
    with pytest.raises(ValidationError):
        _window(start_sec=2.0, end_sec=1.0, anchor_sec=1.5)


def test_anchor_sec_must_lie_within_window():
    with pytest.raises(ValidationError):
        _window(start_sec=1.0, end_sec=2.0, anchor_sec=5.0)


def test_extra_fields_forbidden():
    with pytest.raises(ValidationError):
        _window(unknown_field=1)
