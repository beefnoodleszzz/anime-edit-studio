import pytest

from studio.creative.director.plan import (
    DirectorPlan,
    DirectorSection,
    ImpactBudget,
)
from studio.editing.sequence import apply_recipe_plan
from studio.editing.music import MotionAccent, MusicMotionMap
from studio.execution.recipes import RecipeRegistry
from studio.editspec.schema import (
    Canvas,
    Clip,
    CutRelation,
    EditSpec,
    SourceRange,
    SourceSelection,
    Timebase,
    TimelinePlacement,
)


def _spec() -> EditSpec:
    roles = ["opening", "pre_drop", *(["impact"] * 9), "ending"]
    return EditSpec(
        id="p",
        timebase=Timebase(num=24, den=1),
        canvas=Canvas(width=1080, height=1350, aspect="4:5"),
        clips=[
            Clip(
                id=f"c{index}",
                asset_id="a",
                shot_id=f"s{index}",
                source=SourceRange(in_sec=index, out_sec=index + 1),
                timeline=TimelinePlacement(
                    in_sec=index, duration_sec=1, track="V1"
                ),
                role=role,
            )
            for index, role in enumerate(roles)
        ],
    )


def _plan() -> DirectorPlan:
    return DirectorPlan(
        project_id="p",
        revision=1,
        duration_sec=12,
        primary_characters=["hero"],
        tone=["intense"],
        structure=[
            DirectorSection(
                role="impact", start=0, end=12, energy=0.9,
                average_shot_length=1,
            )
        ],
        visual_rules={"prefer": [], "avoid": []},
        sound_strategy="impact",
        impact_budget=ImpactBudget(sfx_max=5, flash_max=2, shake_max=3),
        generation={},
    )


def test_recipe_plan_obeys_budgets_and_never_stacks_fusion_versions():
    result = apply_recipe_plan(_spec(), plan=_plan())
    impacts = [clip for clip in result.clips if clip.role == "impact"]
    effect_ids = [
        ref.recipe for clip in impacts for ref in clip.effects
    ]
    assert effect_ids.count("white_flash_v1") <= 2
    assert effect_ids.count("impact_shake_v1") <= 3
    assert all(len(clip.effects) <= 1 for clip in result.clips)
    assert sum(len(clip.audio.sfx) for clip in result.clips) <= 5
    assert all(clip.color is not None for clip in result.clips)
    assert any(
        clip.color and clip.color.recipe == "red_impact_v1"
        for clip in impacts
    )
    assert "recipe_planner" in result.meta.model_versions
    assert result.meta.recipe_versions
    assert apply_recipe_plan(result, plan=_plan()) == result


def test_recipe_plan_emits_nothing_when_capabilities_are_not_verified():
    result = apply_recipe_plan(
        _spec(), plan=_plan(), capability_check=lambda _name: False
    )
    assert all(not clip.effects for clip in result.clips)
    assert all(clip.color is None for clip in result.clips)
    assert all(not clip.audio.sfx for clip in result.clips)
    assert not result.meta.recipe_versions
    assert all(clip.retime.speed == 1.0 for clip in result.clips)


def test_calm_retime_slows_untouched_clips_by_role_and_never_reads_past_source():
    """Reference edits mostly play source slowed down; native 1:1 playback
    reads as raw moving footage no matter how good the added camera work is.
    """
    result = apply_recipe_plan(_spec(), plan=_plan())
    by_role = {clip.role: clip for clip in result.clips if clip.role in {"opening", "ending"}}
    assert by_role["opening"].retime.type == "constant"
    assert by_role["opening"].retime.speed == pytest.approx(0.80)
    assert by_role["ending"].retime.speed == pytest.approx(0.80)
    for clip in result.clips:
        if clip.retime.type != "constant":
            continue
        # Slowing down must only narrow the already-assigned source window,
        # never ask for footage the shot doesn't have.
        assert clip.source.duration_sec <= clip.timeline.duration_sec + 1e-9
        assert clip.source.duration_sec == pytest.approx(
            clip.timeline.duration_sec * clip.retime.speed
        )
    assert apply_recipe_plan(result, plan=_plan()) == result


def test_calm_retime_keeps_source_selection_anchor_inside_narrowed_range():
    spec = _spec()
    spec.clips[0].source_selection = SourceSelection(
        phase="representative",
        anchor_sec=.95,
        confidence=.8,
        evidence=["scored_keyframe:.950"],
    )

    result = apply_recipe_plan(spec, plan=_plan())
    clip = result.clips[0]

    assert clip.source.in_sec <= clip.source_selection.anchor_sec <= clip.source.out_sec
    EditSpec.model_validate(result.model_dump(mode="python", by_alias=True))


def test_calm_retime_is_gated_behind_the_timespeed_capability():
    result = apply_recipe_plan(
        _spec(),
        plan=_plan(),
        capability_check=lambda name: name != "timespeed_recipe",
    )
    assert all(clip.retime.speed == 1.0 for clip in result.clips)


def test_push_pull_tone_applies_accepted_camera_punch_to_every_clip():
    plan = _plan().model_copy(update={"tone": ["intense", "push_pull"]})
    result = apply_recipe_plan(_spec(), plan=plan)
    assert all(
        [ref.recipe for ref in clip.effects] == ["camera_punch_v1"]
        for clip in result.clips
    )
    assert not any(
        ref.recipe in {"white_flash_v1", "impact_shake_v1", "eye_focus_v1"}
        for clip in result.clips for ref in clip.effects
    )


def test_curated_motion_grammar_emits_admitted_non_stacking_recipes():
    style = _plan().editing_style.model_copy(
        update={
            "source": "curated",
            "hard_cut_ratio": 0.75,
            "speed_ramp_density": 0.2,
        }
    )
    plan = _plan().model_copy(update={"editing_style": style})
    result = apply_recipe_plan(_spec(), plan=plan)

    bridges = [
        (left, right)
        for left, right in zip(result.clips, result.clips[1:])
        if left.transition.out.recipe == "motion_blur_transition_v1"
    ]
    assert bridges
    assert all(
        right.transition.in_.recipe == "motion_blur_transition_v1"
        and left.transition.out.duration_sec == right.transition.in_.duration_sec
        and left.transition.out.params == right.transition.in_.params
        for left, right in bridges
    )
    ramps = [clip for clip in result.clips if clip.retime.type == "speed_ramp"]
    assert ramps
    assert all(not clip.effects for clip in ramps)
    assert all(
        not left.effects and not right.effects
        for left, right in bridges
    )


def test_curated_motion_grammar_upgrades_to_settle_landing_once_v2_is_accepted():
    """Until a human accepts v2, bridges must stay on the accepted v1 recipe."""
    style = _plan().editing_style.model_copy(
        update={
            "source": "curated",
            "hard_cut_ratio": 0.75,
            "speed_ramp_density": 0.2,
        }
    )
    plan = _plan().model_copy(update={"editing_style": style})

    unaccepted = apply_recipe_plan(_spec(), plan=plan)
    assert not any(
        clip.transition.out.recipe == "motion_blur_transition_v2"
        or clip.transition.in_.recipe == "motion_blur_transition_v2"
        for clip in unaccepted.clips
    )

    registry = RecipeRegistry.load()
    accepted_v2 = registry.get("motion_blur_transition_v2").model_copy(
        update={"verified": True}
    )
    patched = RecipeRegistry(
        [
            accepted_v2 if recipe.id == "motion_blur_transition_v2" else recipe
            for recipe in registry._by_id.values()
        ]
    )
    result = apply_recipe_plan(_spec(), plan=plan, registry=patched)
    bridges = [
        (left, right)
        for left, right in zip(result.clips, result.clips[1:])
        if left.transition.out.recipe == "motion_blur_transition_v2"
    ]
    assert bridges
    assert all(
        right.transition.in_.recipe == "motion_blur_transition_v2"
        and left.transition.out.params.get("settle_scale") == 0.05
        and right.transition.in_.params.get("settle_scale") == 0.05
        for left, right in bridges
    )


def test_reference_soft_boundaries_do_not_invent_whip_transitions():
    style = _plan().editing_style.model_copy(
        update={
            "source": "reference",
            "hard_cut_ratio": 0.5,
            "speed_ramp_density": 0.0,
        }
    )
    result = apply_recipe_plan(
        _spec(), plan=_plan().model_copy(update={"editing_style": style})
    )
    assert all(
        clip.transition.in_.recipe == "hard_cut"
        and clip.transition.out.recipe == "hard_cut"
        for clip in result.clips
    )


def test_motion_phrase_planner_carries_velocity_and_uses_reference_direction():
    plan = _plan().model_copy(
        update={
            "editing_style": _plan().editing_style.model_copy(
                update={
                    "source": "curated",
                    "motion_direction_pattern": ["right"] * 12,
                    "motion_intensity_pattern": [0.8] * 12,
                }
            )
        }
    )
    result = apply_recipe_plan(
        _spec(),
        plan=plan,
        capability_check=lambda name: name == "motion_phrase_compositor",
    )
    assert len(result.motion_phrases) >= 2
    assert all(phrase.direction == "right" for phrase in result.motion_phrases)
    moving = [
        beat.clip_id
        for phrase in result.motion_phrases
        for beat in phrase.beats
    ]
    assert len(moving) == len(set(moving))
    assert [beat.stage for beat in result.motion_phrases[0].beats] == [
        "accelerate", "carry", "settle", "reverse",
    ]


def test_editor_driven_reference_tiles_timeline_from_measured_motion_envelope():
    spec = _spec()
    relations = [
        None,
        CutRelation(
            kind="match_action",
            motivation="rightward carry",
            confidence=.82,
            matched_features=["motion_direction:right"],
        ),
        CutRelation(
            kind="match_action",
            motivation="rightward carry",
            confidence=.78,
            matched_features=["motion_direction:right"],
        ),
        CutRelation(
            kind="contrast",
            motivation="impact interruption",
            confidence=.9,
            matched_features=["impact_intent"],
        ),
        CutRelation(
            kind="continuation",
            motivation="unmeasured similarity",
            confidence=.8,
            matched_features=["shot_scale_similarity"],
        ),
        CutRelation(
            kind="match_action",
            motivation="leftward carry",
            confidence=.82,
            matched_features=["motion_direction:left"],
        ),
        CutRelation(
            kind="match_action",
            motivation="leftward carry",
            confidence=.82,
            matched_features=["motion_direction:left"],
        ),
        *([None] * 5),
    ]
    spec.clips = [
        clip.model_copy(update={"incoming_cut": relations[index]})
        for index, clip in enumerate(spec.clips)
    ]
    style = _plan().editing_style.model_copy(
        update={
            "source": "reference",
            "motion_p75_target": 5.0,
            "motion_change_ratio": .9,
        }
    )
    plan = _plan().model_copy(update={"editing_style": style})

    result = apply_recipe_plan(
        spec,
        plan=plan,
        capability_check=lambda name: name == "motion_phrase_compositor",
    )

    assert [
        [beat.clip_id for beat in phrase.beats]
        for phrase in result.motion_phrases
    ] == [
        ["c0", "c1", "c2", "c3"],
        ["c4", "c5", "c6", "c7"],
        ["c8", "c9", "c10", "c11"],
    ]
    assert all(
        beat.direction is not None
        for phrase in result.motion_phrases
        for beat in phrase.beats
    )
    assert len({
        beat.clip_id
        for phrase in result.motion_phrases
        for beat in phrase.beats
    }) == len(spec.clips)


def test_editor_driven_motion_persists_musical_envelope_and_reverses_on_impact():
    style = type(_plan().editing_style).model_validate(
        {
            **_plan().editing_style.model_dump(mode="json"),
            "source": "reference",
            "motion_p75_target": 6.0,
            "motion_change_ratio": 0.9,
            "motion_curve": [
                {"time": 0.0, "vx": 1.0, "vy": 0.0, "magnitude": 2.0,
                 "acceleration": 0.0, "confidence": 1.0},
                {"time": 12.0, "vx": 1.0, "vy": 0.0, "magnitude": 2.0,
                 "acceleration": 0.0, "confidence": 1.0},
            ],
        }
    )
    music_motion = MusicMotionMap(
        source_music_map_version="music-map-1.0.0",
        duration_sec=12.0,
        accents=[
            MotionAccent(
                sec=1.0,
                kind="impact",
                strength=0.95,
                anticipation_sec=0.18,
                release_sec=0.24,
                target_velocity=0.31,
            ),
            MotionAccent(
                sec=2.0,
                kind="beat",
                strength=0.6,
                anticipation_sec=0.12,
                release_sec=0.2,
                target_velocity=0.2,
            ),
        ],
    )

    result = apply_recipe_plan(
        _spec(),
        plan=_plan().model_copy(update={"editing_style": style}),
        music_motion=music_motion,
        capability_check=lambda name: name == "motion_phrase_compositor",
    )

    first, second = result.motion_phrases[0].beats[:2]
    assert first.accent_at_sec == pytest.approx(1.0)
    assert first.anticipation_sec == pytest.approx(0.18)
    assert first.release_sec == pytest.approx(0.24)
    assert second.direction != first.direction
    assert first.zoom_direction == second.zoom_direction == "in"


def test_settle_beat_inherits_music_accent_at_cut_not_clip_tail():
    style = _plan().editing_style.model_copy(
        update={
            "source": "reference",
            "motion_p75_target": 6.0,
            "motion_change_ratio": 0.9,
        }
    )
    boundary = _spec().clips[1].timeline.in_sec
    music_motion = MusicMotionMap(
        source_music_map_version="music-map-1.0.0",
        duration_sec=12.0,
        accents=[
            MotionAccent(
                sec=boundary,
                kind="downbeat",
                strength=0.8,
                anticipation_sec=0.14,
                release_sec=0.2,
                target_velocity=0.27,
            )
        ],
    )
    result = apply_recipe_plan(
        _spec(),
        plan=_plan().model_copy(update={"editing_style": style}),
        music_motion=music_motion,
        capability_check=lambda name: name == "motion_phrase_compositor",
    )
    second = result.motion_phrases[0].beats[1]
    assert second.accent_at_sec == pytest.approx(0.0)
    assert second.release_sec == pytest.approx(0.2)
