"""Real construction/validation tests for the new v3.0.0 AMV chain schemas."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from studio.spec import SPEC_VERSION
from studio.spec.amv import (
    AMVSpec,
    Canvas,
    Clip,
    InputHashes,
    MusicRef,
    RenderSettings,
    SourceRange,
    Timebase,
    TimelinePlacement,
    TransformKeyframe,
    TransitionPair,
)
from studio.spec.music_timeline import Accent, MusicTimeline, Section
from studio.spec.reference_blueprint import (
    CutObservation,
    Estimate,
    ReferenceBlueprint,
    ShotObservation,
    StyleSummary,
    TechnicalProfile,
)


def _blueprint() -> ReferenceBlueprint:
    return ReferenceBlueprint(
        source_hash="deadbeef",
        technical=TechnicalProfile(
            width=1080, height=1350, fps_num=24000, fps_den=1001,
            duration_sec=18.0, aspect="4:5",
        ),
        shots=[
            ShotObservation(
                index=0, start_sec=0.0, end_sec=1.5, duration_sec=1.5,
                visual_energy=0.4, brightness=0.5,
                native_motion_estimate=Estimate(value=0.1, confidence=0.6, evidence=["lk_ransac"]),
                global_motion_estimate=Estimate(value=0.2, confidence=0.7, evidence=["ecc_fallback"]),
                motion_confidence=0.65,
            ),
            ShotObservation(
                index=1, start_sec=1.5, end_sec=3.0, duration_sec=1.5,
                visual_energy=0.6, brightness=0.4,
                native_motion_estimate=Estimate(value=0.3, confidence=0.5, evidence=[]),
                global_motion_estimate=Estimate(value=0.4, confidence=0.5, evidence=[]),
                motion_confidence=0.5,
            ),
        ],
        cuts=[
            CutObservation(sec=1.5, type="hard_cut", confidence=0.9, relation="carry"),
        ],
        style_summary=StyleSummary(
            cut_density=0.5,
            motion_coverage=0.7,
            hold_ratio=0.2,
            reversal_ratio=0.1,
            scale_motion_ratio=0.3,
            blur_usage=0.4,
        ),
    )


def test_reference_blueprint_round_trips_through_json():
    blueprint = _blueprint()
    restored = ReferenceBlueprint.model_validate_json(blueprint.model_dump_json())
    assert restored == blueprint
    assert restored.version == SPEC_VERSION


def test_reference_blueprint_rejects_unknown_fields():
    payload = _blueprint().model_dump()
    payload["totally_made_up_field"] = 1
    with pytest.raises(ValidationError):
        ReferenceBlueprint.model_validate(payload)


def test_reference_blueprint_rejects_non_contiguous_shot_index():
    payload = _blueprint().model_dump()
    payload["shots"][1]["index"] = 5
    with pytest.raises(ValidationError):
        ReferenceBlueprint.model_validate(payload)


def test_estimate_requires_confidence_and_evidence_fields():
    estimate = Estimate(value=1.0, confidence=0.2, evidence=["low_texture_fallback"])
    assert estimate.confidence == pytest.approx(0.2)
    assert estimate.evidence == ["low_texture_fallback"]
    with pytest.raises(ValidationError):
        Estimate(value=1.0, confidence=1.5, evidence=[])


def _music_timeline() -> MusicTimeline:
    return MusicTimeline(
        source_hash="cafef00d",
        duration_sec=18.0,
        selected_tempo=128.0,
        tempo_confidence=0.8,
        beats=[0.0, 0.47, 0.94],
        sections=[Section(start_sec=0.0, end_sec=9.0, label="intro"),
                   Section(start_sec=9.0, end_sec=18.0, label="drop")],
        accents=[
            Accent(sec=0.0, kind="downbeat", strength=0.9, confidence=0.9),
            Accent(sec=9.0, kind="section_boundary", strength=1.0, confidence=0.95,
                    anticipation_sec=0.1, release_sec=0.2),
        ],
    )


def test_music_timeline_round_trips_and_rejects_unsorted_beats():
    timeline = _music_timeline()
    restored = MusicTimeline.model_validate_json(timeline.model_dump_json())
    assert restored == timeline

    payload = timeline.model_dump()
    payload["beats"] = [0.5, 0.1]
    with pytest.raises(ValidationError):
        MusicTimeline.model_validate(payload)


def test_music_timeline_accent_kind_is_constrained():
    with pytest.raises(ValidationError):
        Accent(sec=1.0, kind="not_a_real_kind", strength=0.5, confidence=0.5)


def _amv_spec() -> AMVSpec:
    clip_a = Clip(
        id="c0", asset_id="a0",
        source=SourceRange(in_sec=0.0, out_sec=1.5),
        timeline=TimelinePlacement(in_sec=0.0, duration_sec=1.5),
    )
    clip_b = Clip(
        id="c1", asset_id="a1",
        source=SourceRange(in_sec=2.0, out_sec=3.5),
        timeline=TimelinePlacement(in_sec=1.5, duration_sec=1.5),
    )
    pair = TransitionPair(
        id="t0", cut_sec=1.5, outgoing_clip_id="c0", incoming_clip_id="c1",
        direction="left", safe_scale=1.1, confidence=0.8,
        outgoing_keyframes=[TransformKeyframe(sec=1.4, center_x=0.5, center_y=0.5, scale=1.05)],
    )
    return AMVSpec(
        id="proj-1",
        input_hashes=InputHashes(demo="d0", materials_index="m0"),
        timebase=Timebase(num=24000, den=1001),
        canvas=Canvas(width=1080, height=1350, aspect="4:5"),
        duration_sec=3.0,
        music=MusicRef(path="/tmp/music.wav", timeline_hash="cafef00d"),
        clips=[clip_a, clip_b],
        transition_pairs=[pair],
        render=RenderSettings(output_path="/tmp/out.mov"),
    )


def test_amv_spec_round_trips():
    spec = _amv_spec()
    restored = AMVSpec.model_validate_json(spec.model_dump_json())
    assert restored == spec
    assert restored.clip_by_id("c0") is not None


def test_amv_spec_rejects_duplicate_clip_ids():
    payload = _amv_spec().model_dump()
    payload["clips"][1]["id"] = "c0"
    with pytest.raises(ValidationError):
        AMVSpec.model_validate(payload)


def test_amv_spec_rejects_transition_pair_referencing_missing_clip():
    payload = _amv_spec().model_dump()
    payload["transition_pairs"][0]["incoming_clip_id"] = "does-not-exist"
    with pytest.raises(ValidationError):
        AMVSpec.model_validate(payload)


def test_amv_spec_has_no_legacy_fields():
    """REFACTOR.md §5.3: no candidates/preference/caption/recipe/migration fields."""
    fields = set(AMVSpec.model_fields)
    forbidden = {
        "candidates", "preference", "captions", "recipe", "revision",
        "created_from", "narrative_role", "recipe_ref", "growth",
    }
    assert fields.isdisjoint(forbidden)
