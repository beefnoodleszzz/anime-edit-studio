from __future__ import annotations

import pytest

from studio.planning.transition_profile import MIN_PROFILE_CONFIDENCE, build_transition_profiles
from studio.spec.reference_blueprint import (
    ReferenceBlueprint,
    StyleSummary,
    TechnicalProfile,
    TransitionPairObservation,
)

_TECHNICAL = TechnicalProfile(width=640, height=360, fps_num=24, fps_den=1, duration_sec=10.0, aspect="16:9")
_STYLE = StyleSummary(
    cut_density=1.0, motion_coverage=0.5, hold_ratio=0.2, reversal_ratio=0.1,
    scale_motion_ratio=0.3, blur_usage=0.0,
)


def _blueprint(pairs: list[TransitionPairObservation]) -> ReferenceBlueprint:
    return ReferenceBlueprint(
        source_hash="h", technical=_TECHNICAL, transition_pairs=pairs, style_summary=_STYLE,
    )


def test_profile_averages_measured_envelopes_per_relation():
    pairs = [
        TransitionPairObservation(
            cut_sec=1.0, relation="carry", direction="left",
            anticipation_sec=0.2, release_sec=0.2,
            outgoing_envelope=[10.0, 40.0, 20.0], incoming_envelope=[15.0, 25.0],
            overshoot=8.0, blur_envelope=[], confidence=0.9,
        ),
        TransitionPairObservation(
            cut_sec=3.0, relation="carry", direction="right",
            anticipation_sec=0.4, release_sec=0.4,
            outgoing_envelope=[20.0], incoming_envelope=[60.0],
            overshoot=16.0, blur_envelope=[], confidence=0.7,
        ),
    ]
    profiles = build_transition_profiles(_blueprint(pairs))
    carry = profiles["carry"]

    assert carry.usable
    assert carry.anticipation_sec == pytest.approx(0.3)
    assert carry.release_sec == pytest.approx(0.3)
    assert carry.confidence == pytest.approx(0.8)
    # peaks: 40 and 60, width 640 -> mean 50/640
    assert carry.translation_unit == pytest.approx(50.0 / 640)
    # overshoot: mean(8,16)/640
    assert carry.overshoot == pytest.approx((8.0 + 16.0) / 2 / 640)


def test_profile_falls_back_to_defaults_when_no_pairs_for_a_relation():
    profiles = build_transition_profiles(_blueprint([]))
    assert profiles["carry"].confidence == 0.0
    assert not profiles["carry"].usable
    assert profiles["reverse"].confidence == 0.0
    assert profiles["reset"].confidence == 0.0


def test_profile_takes_the_majority_effect_kind_vote():
    flashy = [
        TransitionPairObservation(
            cut_sec=sec, relation="carry", direction="left",
            anticipation_sec=0.2, release_sec=0.2,
            outgoing_envelope=[10.0], incoming_envelope=[10.0],
            overshoot=0.0, blur_envelope=[], confidence=0.9, effect_kind=kind,
        )
        for sec, kind in [(1.0, "flash"), (2.0, "flash"), (3.0, "none")]
    ]
    profiles = build_transition_profiles(_blueprint(flashy))
    assert profiles["carry"].effect_kind == "flash"

    mostly_plain = [
        TransitionPairObservation(
            cut_sec=sec, relation="reverse", direction="left",
            anticipation_sec=0.2, release_sec=0.2,
            outgoing_envelope=[10.0], incoming_envelope=[10.0],
            overshoot=0.0, blur_envelope=[], confidence=0.9, effect_kind=kind,
        )
        for sec, kind in [(1.0, "flash"), (2.0, "none"), (3.0, "none")]
    ]
    profiles = build_transition_profiles(_blueprint(mostly_plain))
    assert profiles["reverse"].effect_kind == "none"


def test_low_confidence_pairs_stay_below_the_usable_threshold():
    pairs = [
        TransitionPairObservation(
            cut_sec=1.0, relation="reverse", direction="left",
            anticipation_sec=0.2, release_sec=0.2,
            outgoing_envelope=[5.0], incoming_envelope=[5.0],
            overshoot=0.0, blur_envelope=[], confidence=0.1,
        ),
    ]
    profiles = build_transition_profiles(_blueprint(pairs))
    assert profiles["reverse"].confidence < MIN_PROFILE_CONFIDENCE
    assert not profiles["reverse"].usable
