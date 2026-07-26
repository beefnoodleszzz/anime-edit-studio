from studio.creative.director.plan import (
    DirectorPlan,
    DirectorSection,
    ImpactBudget,
)
from studio.editing.sequence import apply_recipe_plan
from studio.editspec.schema import (
    Canvas,
    Clip,
    EditSpec,
    SourceRange,
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


def test_reference_motion_grammar_emits_admitted_non_stacking_recipes():
    style = _plan().editing_style.model_copy(
        update={"hard_cut_ratio": 0.75, "speed_ramp_density": 0.2}
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


def test_motion_phrase_planner_carries_velocity_and_breaks_periodicity():
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
    moving = {
        beat.clip_id
        for phrase in result.motion_phrases
        for beat in phrase.beats
    }
    assert result.clips[3].id not in moving
    assert [beat.stage for beat in result.motion_phrases[0].beats] == [
        "accelerate", "carry", "settle",
    ]
    starts = [
        next(i for i, clip in enumerate(result.clips) if clip.id == phrase.beats[0].clip_id)
        for phrase in result.motion_phrases
    ]
    assert len(set(b - a for a, b in zip(starts, starts[1:]))) > 1
