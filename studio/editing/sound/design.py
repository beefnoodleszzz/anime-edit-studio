"""Deterministic sound design: a three-piece hit on every drum target.

The coarse SFX pass in ``recipe_plan`` drops one cue on selected impact clips at
the clip's start.  That is not sound *design*: a燃-cut hit is a riser pulling
into the beat, a low impact landing exactly on the frame, and the impact's own
tail carrying out of it — the memo's "被声音吸过去并击中了下一帧".

This module lays that structure precisely against the same musical targets the
Action Sync pass uses.  It is deterministic, budget-constrained by the
DirectorPlan ``impact_budget``, silence-aware (never lands an impact inside a
MusicMap silence), and capability-gated: it only ever references sound Recipes
that are ``verified`` in the registry (AGENTS R3/R4).  It maps absolute target
times back to ``(clip, at_sec)`` so a riser can begin on the *previous* clip and
resolve on the cut.

Volume envelopes are baked offline into the prebaked Recipe wavs (the public
Fairlight automation API is unavailable, capability ``fairlight_automation`` is
false); this pass only decides *what plays when*.
"""
from __future__ import annotations

from collections.abc import Callable

from pydantic import BaseModel, ConfigDict, Field

from studio.core.capabilities import is_verified
from studio.creative.director import DirectorPlan
from studio.editing.music import MusicMap
from studio.editspec.schema import EditSpec, SfxCue
from studio.execution.recipes import RecipeRegistry

SOUND_DESIGN_VERSION = "sound-design-1.0.0"

_MANAGED_SOUNDS = {"impact_low_v1", "sub_impact_v1", "sword_whoosh_v1", "riser_v1"}
# Riser leads into the beat; whoosh approaches it a touch later.
RISER_LEAD_SEC = 0.28
WHOOSH_LEAD_SEC = 0.14
# A target inside a silence window keeps its riser (the silence is the setup)
# but drops the on-beat impact.


class SoundCuePlacement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_sec: float
    clip_id: str
    recipe: str
    at_sec: float
    role: str  # riser | impact | whoosh


class SoundDesignReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str = SOUND_DESIGN_VERSION
    target_count: int
    designed_targets: int
    cue_count: int
    sfx_budget: int
    silence_skipped_impacts: int
    placements: list[SoundCuePlacement] = Field(default_factory=list)


def _admitted(
    registry: RecipeRegistry,
    recipe_id: str,
    *,
    capability_check: Callable[[str], bool],
) -> bool:
    recipe = registry.get(recipe_id)
    return bool(
        recipe
        and recipe.verified
        and recipe.kind == "sound"
        and capability_check("sound_recipe_prebake")
        and not registry.validate(recipe_id, {}, expected_kind="sound")
    )


def _clip_tokens(clip) -> set[str]:
    if clip.source_selection is None:
        return set()
    return {
        value.removeprefix("semantic:")
        for value in clip.source_selection.evidence
        if value.startswith("semantic:")
    }


def _clip_at(clips, absolute_sec: float):
    """Return (clip, at_sec) for the clip whose window contains absolute_sec."""
    for clip in clips:
        start = clip.timeline.in_sec
        end = start + clip.timeline.duration_sec
        if start <= absolute_sec < end:
            return clip, absolute_sec - start
    return None, 0.0


def _in_silence(music: MusicMap, sec: float) -> bool:
    return any(item.start <= sec < item.end for item in music.silences)


def _targets(spec: EditSpec, music: MusicMap) -> list[float]:
    """Drum targets to score: cuts that land on a major musical hit.

    Every clip boundary is a real source change; a boundary that also sits on a
    MusicMap impact point or downbeat is a major hit worth designing sound
    around.  Impacts rank first so, under budget, the biggest hits win.
    """
    cut_starts = [clip.timeline.in_sec for clip in spec.clips if clip.timeline.in_sec > 0]
    impacts = sorted(music.impact_points)
    downbeats = sorted(music.downbeats)
    tolerance = 0.06
    ranked: list[tuple[int, float]] = []
    for cut in cut_starts:
        if any(abs(cut - impact) <= tolerance for impact in impacts):
            ranked.append((0, cut))
        elif any(abs(cut - down) <= tolerance for down in downbeats):
            ranked.append((1, cut))
    # Impacts first, then chronological, so the budget spends on big hits.
    ranked.sort(key=lambda item: (item[0], item[1]))
    return [cut for _, cut in ranked]


def apply_sound_design(
    spec: EditSpec,
    *,
    music: MusicMap,
    plan: DirectorPlan,
    registry: RecipeRegistry | None = None,
    capability_check: Callable[[str], bool] = is_verified,
) -> tuple[EditSpec, SoundDesignReport]:
    """Attach a designed, budget-limited sound layer; return spec + report."""
    registry = registry or RecipeRegistry.load()
    result = spec.model_copy(deep=True)
    clips = result.clips
    # This pass owns the drum SFX layer: clear any coarse managed cues first.
    for clip in clips:
        clip.audio = clip.audio.model_copy(
            update={
                "sfx": [c for c in clip.audio.sfx if c.recipe not in _MANAGED_SOUNDS]
            }
        )

    impact_recipe = next(
        (rid for rid in ("impact_low_v1", "sub_impact_v1") if _admitted(
            registry, rid, capability_check=capability_check)),
        None,
    )
    riser_ok = _admitted(registry, "riser_v1", capability_check=capability_check)
    whoosh_ok = _admitted(registry, "sword_whoosh_v1", capability_check=capability_check)

    targets = _targets(spec, music)
    budget = plan.impact_budget.sfx_max
    placements: list[SoundCuePlacement] = []
    designed = 0
    silence_skipped = 0

    for target in targets:
        if len(placements) >= budget:
            break
        cues_here: list[SoundCuePlacement] = []
        # Riser pulling into the beat (may land on the previous clip).
        if riser_ok:
            clip, at = _clip_at(clips, target - RISER_LEAD_SEC)
            if clip is not None:
                cues_here.append(SoundCuePlacement(
                    target_sec=round(target, 4), clip_id=clip.id,
                    recipe="riser_v1", at_sec=round(max(0.0, at), 4), role="riser",
                ))
        # Sword whoosh on approach when the incoming shot is a blade action.
        impact_clip, impact_at = _clip_at(clips, target)
        if whoosh_ok and impact_clip is not None and (
            _clip_tokens(impact_clip) & {"sword", "sword_swing", "slash", "blade", "katana"}
        ):
            wclip, wat = _clip_at(clips, target - WHOOSH_LEAD_SEC)
            if wclip is not None:
                cues_here.append(SoundCuePlacement(
                    target_sec=round(target, 4), clip_id=wclip.id,
                    recipe="sword_whoosh_v1", at_sec=round(max(0.0, wat), 4),
                    role="whoosh",
                ))
        # Low impact exactly on the beat, unless the beat sits in a silence.
        if impact_recipe and impact_clip is not None:
            if _in_silence(music, target):
                silence_skipped += 1
            else:
                cues_here.append(SoundCuePlacement(
                    target_sec=round(target, 4), clip_id=impact_clip.id,
                    recipe=impact_recipe, at_sec=round(max(0.0, impact_at), 4),
                    role="impact",
                ))
        if not cues_here:
            continue
        # Enforce the total budget atomically: a target's cues are a single
        # designed hit (riser pulling in, optional whoosh, impact landing).
        # Slicing that list to fit leftover budget can keep the riser and drop
        # the impact it was pulling into — a promise with no landing, which is
        # exactly the "orphan riser" sound_qa exists to catch. Skip the whole
        # target instead and let a smaller downstream target use the budget.
        room = budget - len(placements)
        if len(cues_here) > room:
            continue
        placements.extend(cues_here)
        designed += 1

    by_clip: dict[str, list[SoundCuePlacement]] = {}
    for placement in placements:
        by_clip.setdefault(placement.clip_id, []).append(placement)
    for clip in clips:
        additions = by_clip.get(clip.id)
        if not additions:
            continue
        merged = list(clip.audio.sfx) + [
            SfxCue(recipe=p.recipe, at_sec=p.at_sec) for p in additions
        ]
        merged.sort(key=lambda cue: cue.at_sec)
        clip.audio = clip.audio.model_copy(update={"sfx": merged})

    meta = result.meta.model_copy(deep=True)
    meta.model_versions = {**meta.model_versions, "sound_design": SOUND_DESIGN_VERSION}
    for recipe_id in {p.recipe for p in placements}:
        recipe = registry.get(recipe_id)
        if recipe is not None:
            meta.recipe_versions = {**meta.recipe_versions, recipe_id: recipe.version}
    result = result.model_copy(update={"meta": meta})
    result = EditSpec.model_validate(result.model_dump(mode="python", by_alias=True))

    report = SoundDesignReport(
        target_count=len(targets),
        designed_targets=designed,
        cue_count=len(placements),
        sfx_budget=budget,
        silence_skipped_impacts=silence_skipped,
        placements=placements,
    )
    return result, report


__all__ = [
    "SOUND_DESIGN_VERSION",
    "SoundCuePlacement",
    "SoundDesignReport",
    "apply_sound_design",
]
