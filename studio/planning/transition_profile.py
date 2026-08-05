"""Turn the Demo's *measured* per-relation transition envelopes into the
parameters motion_planner needs, instead of motion_planner using fixed
constants (0.333s anticipation/release, 10% translation, 4% overshoot)
regardless of what the Demo actually did.

One profile is built per relation (carry/reverse/reset) by averaging every
``TransitionPairObservation`` of that relation in the ``ReferenceBlueprint``
— not per individual cut, since ``TimelineSlot``s in style-transfer mode
don't correspond 1:1 to a specific Demo cut (only exact-replica mode's
slots do), but every mode's slots do carry a relation label that this can
key off of.
"""
from __future__ import annotations

from dataclasses import dataclass

from studio.planning.slots import EntryMotion
from studio.spec.reference_blueprint import ReferenceBlueprint, TransitionPairObservation

# Below this confidence, the measured envelope is not trustworthy enough to
# drive geometry — callers should fall back to a plain hard cut rather than
# invent a transition from noise.
MIN_PROFILE_CONFIDENCE = 0.35

_DEFAULT_ANTICIPATION_SEC = 8 / 24
_DEFAULT_RELEASE_SEC = 8 / 24
_DEFAULT_TRANSLATION_UNIT = 0.10
_DEFAULT_OVERSHOOT = 0.04

# Pixel motion magnitudes are measured at the Demo's own resolution;
# dividing by frame width turns them into a canvas-relative fraction any
# target Canvas can scale, and clamping keeps a noisy/extreme measurement
# from producing an unusable transform.
_MIN_TRANSLATION_UNIT, _MAX_TRANSLATION_UNIT = 0.02, 0.28
_MAX_OVERSHOOT = 0.15


@dataclass(frozen=True)
class TransitionProfile:
    anticipation_sec: float
    release_sec: float
    translation_unit: float
    overshoot: float
    confidence: float
    # Normalized [0, 1] progress through the anticipation window at which
    # the Demo's own motion peaked — where the "attack" keyframe should sit,
    # rather than assuming it's always the midpoint.
    attack_fraction: float
    # Majority vote across this relation's measured pairs (see
    # reference_analyzer._classify_effect) — only "flash" has a real Fusion
    # node graph behind it so far.
    effect_kind: str = "none"

    @property
    def usable(self) -> bool:
        return self.confidence >= MIN_PROFILE_CONFIDENCE


def _attack_fraction(envelope: list[float]) -> float:
    if len(envelope) < 2:
        return 0.5
    peak_index = max(range(len(envelope)), key=lambda i: envelope[i])
    return peak_index / (len(envelope) - 1)


def _profile_for(pairs: list[TransitionPairObservation], demo_width: int) -> TransitionProfile:
    if not pairs or demo_width <= 0:
        return TransitionProfile(
            anticipation_sec=_DEFAULT_ANTICIPATION_SEC, release_sec=_DEFAULT_RELEASE_SEC,
            translation_unit=_DEFAULT_TRANSLATION_UNIT, overshoot=_DEFAULT_OVERSHOOT,
            confidence=0.0, attack_fraction=0.5,
        )
    peak_magnitudes = [
        max([*pair.outgoing_envelope, *pair.incoming_envelope], default=0.0) for pair in pairs
    ]
    attack_fractions = [
        _attack_fraction([*pair.outgoing_envelope, *pair.incoming_envelope]) for pair in pairs
    ]
    translation_unit = sum(peak_magnitudes) / len(peak_magnitudes) / demo_width
    overshoot = sum(pair.overshoot for pair in pairs) / len(pairs) / demo_width
    flash_votes = sum(1 for pair in pairs if pair.effect_kind == "flash")
    return TransitionProfile(
        anticipation_sec=sum(pair.anticipation_sec for pair in pairs) / len(pairs),
        release_sec=sum(pair.release_sec for pair in pairs) / len(pairs),
        translation_unit=max(_MIN_TRANSLATION_UNIT, min(_MAX_TRANSLATION_UNIT, translation_unit)),
        overshoot=max(0.0, min(_MAX_OVERSHOOT, overshoot)),
        confidence=sum(pair.confidence for pair in pairs) / len(pairs),
        attack_fraction=sum(attack_fractions) / len(attack_fractions),
        effect_kind="flash" if flash_votes * 2 > len(pairs) else "none",
    )


def build_transition_profiles(blueprint: ReferenceBlueprint) -> dict[EntryMotion, TransitionProfile]:
    by_relation: dict[EntryMotion, list[TransitionPairObservation]] = {
        "carry": [], "reverse": [], "reset": [],
    }
    for pair in blueprint.transition_pairs:
        if pair.relation in by_relation:
            by_relation[pair.relation].append(pair)
    demo_width = blueprint.technical.width
    return {relation: _profile_for(pairs, demo_width) for relation, pairs in by_relation.items()}


__all__ = ["MIN_PROFILE_CONFIDENCE", "TransitionProfile", "build_transition_profiles"]
