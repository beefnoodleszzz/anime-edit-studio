"""Lock picture and add bounded Color/Sound Recipe decisions.

This module never changes clip timing, source ranges, ordering, framing, effects,
or MotionPhrases.  It is intentionally deterministic so a locked cut can be
finished again without accumulating duplicate cues.
"""
from __future__ import annotations

from collections.abc import Callable

from studio.core.capabilities import is_verified
from studio.editspec.schema import EditSpec, RecipeRef, SfxCue
from studio.execution.recipes import RecipeRegistry

FINISHING_VERSION = "locked-finishing-1.0.0"
_FINISHING_SOUNDS = {
    "impact_low_v1",
    "sub_impact_v1",
    "sword_whoosh_v1",
    "riser_v1",
}


def _admitted(
    registry: RecipeRegistry,
    recipe_id: str,
    *,
    kind: str,
    capability: str,
    capability_check: Callable[[str], bool],
) -> bool:
    recipe = registry.get(recipe_id)
    return bool(
        recipe
        and recipe.verified
        and recipe.kind == kind
        and capability_check(capability)
        and not registry.validate(recipe_id, {}, expected_kind=kind)
    )


def _clip_at(spec: EditSpec, sec: float):
    return min(
        spec.clips,
        key=lambda clip: abs(clip.timeline.in_sec - sec),
    )


def finish_locked_picture(
    spec: EditSpec,
    *,
    drop_sec: float,
    registry: RecipeRegistry | None = None,
    capability_check: Callable[[str], bool] = is_verified,
) -> EditSpec:
    """Return a new revision with picture lock plus final grade/sound intent."""
    registry = registry or RecipeRegistry.load()
    required = {
        "anime_clean_v1": ("color", "color_recipe"),
        "anime_high_contrast_v1": ("color", "color_recipe"),
        "anime_fire_v1": ("color", "color_recipe"),
        "red_impact_v1": ("color", "color_recipe"),
        "sword_whoosh_v1": ("sound", "sound_recipe_prebake"),
        "impact_low_v1": ("sound", "sound_recipe_prebake"),
        "sub_impact_v1": ("sound", "sound_recipe_prebake"),
        "riser_v1": ("sound", "sound_recipe_prebake"),
    }
    missing = [
        recipe_id
        for recipe_id, (kind, capability) in required.items()
        if not _admitted(
            registry,
            recipe_id,
            kind=kind,
            capability=capability,
            capability_check=capability_check,
        )
    ]
    if missing:
        raise ValueError("锁画精修所需 Recipe 尚未验收: " + ", ".join(missing))

    clips = [clip.model_copy(deep=True) for clip in spec.clips]
    by_id = {clip.id: clip for clip in clips}
    motion_phrases = [phrase.model_copy(deep=True) for phrase in spec.motion_phrases]
    used_versions = dict(spec.meta.recipe_versions)

    def use(recipe_id: str) -> RecipeRef:
        recipe = registry.get(recipe_id)
        if recipe is None:
            raise ValueError(f"Recipe 不存在: {recipe_id}")
        used_versions[recipe_id] = recipe.version
        return RecipeRef(recipe=recipe_id)

    # Lock every picture decision.  The grade arc changes Recipe choice only.
    for clip in clips:
        clip.decision = clip.decision.model_copy(update={"locked": True})
        clip.audio = clip.audio.model_copy(
            update={
                "sfx": [
                    cue
                    for cue in clip.audio.sfx
                    if cue.recipe not in _FINISHING_SOUNDS
                ]
            }
        )
        sec = clip.timeline.in_sec
        if drop_sec - 2.2 <= sec < drop_sec:
            clip.color = use("anime_fire_v1")
        elif drop_sec <= sec < drop_sec + 1.7:
            clip.color = use("anime_high_contrast_v1")
        else:
            clip.color = use("anime_clean_v1")

    peak = _clip_at(spec, drop_sec)
    by_id[peak.id].color = use("red_impact_v1")

    def cue(at_sec: float, recipe: str, gain_db: float) -> None:
        clip = _clip_at(spec, at_sec)
        local = max(
            0.0,
            min(
                clip.timeline.duration_sec - 1e-6,
                at_sec - clip.timeline.in_sec,
            ),
        )
        clip = by_id[clip.id]
        clip.audio = clip.audio.model_copy(
            update={
                "sfx": [
                    *clip.audio.sfx,
                    SfxCue(recipe=recipe, at_sec=local, gain_db=gain_db),
                ]
            }
        )
        use(recipe)

    # One riser into the musical drop, a two-layer impact, sparse phrase
    # whooshes, and restrained section punctuation.
    cue(max(0.0, drop_sec - 2.0), "riser_v1", -14.0)
    cue(drop_sec, "impact_low_v1", -7.0)
    cue(drop_sec, "sub_impact_v1", -12.0)

    phrase_cuts = sorted(
        {
            by_id[phrase.beats[1].clip_id].timeline.in_sec
            for phrase in motion_phrases
            if len(phrase.beats) > 1
        }
    )
    for sec in phrase_cuts:
        if abs(sec - drop_sec) > 0.65:
            cue(sec, "sword_whoosh_v1", -14.0)

    for sec in (18.065, 20.155, 25.565):
        if sec < spec.duration_sec:
            cue(sec, "impact_low_v1", -13.0)

    for phrase in motion_phrases:
        phrase.decision = phrase.decision.model_copy(update={"locked": True})

    meta = spec.meta.model_copy(deep=True)
    meta.recipe_versions = used_versions
    meta.model_versions = {
        **meta.model_versions,
        "locked_finishing": FINISHING_VERSION,
    }
    return spec.model_copy(
        update={
            "revision": spec.revision + 1,
            "clips": clips,
            "motion_phrases": motion_phrases,
            "meta": meta,
        }
    )


__all__ = ["FINISHING_VERSION", "finish_locked_picture"]
