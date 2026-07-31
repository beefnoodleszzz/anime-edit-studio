"""Typed DirectorPlan generation aligned to measured music structure."""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from studio.creative.reference import (
    EditingStyleProfile,
    StyleFingerprint,
    compile_editing_style,
    default_editing_style,
)
from studio.editing.music import MusicMap

DIRECTOR_PLAN_VERSION = "1"

# ── House cut format ────────────────────────────────────────────────────────
# The standing delivery shape for this studio: a 16–20s piece that opens with a
# 3–5s hook and spends everything after it on beat-locked cutting.  This is a
# format, not a rule about content — which shots, which character, which tone
# still come from the brief and the footage.  It lives here rather than in the
# CLI default because every entry point (CLI, review API, scheduled runs) must
# land on the same shape; a default that only one caller honours is not a house
# format.
HOUSE_DURATION_SEC = 18.0
HOUSE_DURATION_RANGE = (16.0, 20.0)
HOOK_RANGE_SEC = (3.0, 5.0)


class DirectorBrief(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str
    duration_sec: float = Field(..., gt=0)
    primary_characters: list[str] = Field(default_factory=list)
    tone: list[str] = Field(default_factory=list)
    prefer: list[str] = Field(default_factory=list)
    avoid: list[str] = Field(
        default_factory=lambda: ["static_dialogue", "subtitles", "bad_composition"]
    )
    target_platform: str | None = None


class DirectorSection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: str
    start: float = Field(..., ge=0)
    end: float = Field(..., gt=0)
    energy: float = Field(..., ge=0, le=1)
    average_shot_length: float = Field(..., gt=0)


class ImpactBudget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sfx_max: int = Field(..., ge=0)
    flash_max: int = Field(..., ge=0)
    shake_max: int = Field(..., ge=0)


class DirectorPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str = DIRECTOR_PLAN_VERSION
    project_id: str
    revision: int = Field(..., ge=1)
    duration_sec: float
    primary_characters: list[str]
    tone: list[str]
    structure: list[DirectorSection]
    visual_rules: dict[str, list[str]]
    sound_strategy: str
    impact_budget: ImpactBudget
    editing_style: EditingStyleProfile = Field(default_factory=default_editing_style)
    generation: dict


_ROLE = {
    "intro": "opening",
    "build": "buildup",
    "verse": "buildup",
    "break": "pre_drop",
    "drop": "impact",
    "release": "release",
    "outro": "ending",
}


def _fallback_arc(
    duration: float,
    music: MusicMap,
    reference_asl: float,
) -> list[DirectorSection]:
    """Create a complete arc when the requested window sits in one source section."""
    usable_impacts = [
        point
        for point in music.impact_points
        if duration * 0.25 <= point <= duration * 0.65
    ]
    impact = (
        min(usable_impacts, key=lambda point: abs(point - duration * 0.42))
        if usable_impacts else duration * 0.42
    )
    opening_end = min(duration * 0.14, max(1.5, impact * 0.32))
    pre_drop_start = max(opening_end + 0.5, impact - min(1.2, duration * 0.05))
    impact_end = min(duration * 0.72, impact + duration * 0.26)
    release_end = min(duration * 0.88, max(impact_end + 0.5, duration * 0.84))
    bounds = [
        ("opening", 0.0, opening_end, 0.35, 1.45),
        ("buildup", opening_end, pre_drop_start, 0.58, 1.05),
        ("pre_drop", pre_drop_start, impact, 0.42, 1.25),
        ("impact", impact, impact_end, 0.95, 0.55),
        ("release", impact_end, release_end, 0.68, 0.9),
        ("ending", release_end, duration, 0.38, 1.5),
    ]
    return [
        DirectorSection(
            role=role,
            start=start,
            end=end,
            energy=energy,
            average_shot_length=max(0.4, min(2.2, reference_asl * factor)),
        )
        for role, start, end, energy, factor in bounds
        if end - start >= 0.2
    ]


def _musical_boundary(music: MusicMap, low: float, high: float) -> float | None:
    """Best musical event to end the hook on, inside ``[low, high]``.

    The hook must end *on* something the ear already hears — a section change,
    an impact, a downbeat — otherwise the handover into the beat-locked body is
    an arbitrary timestamp and reads as a stumble.  Preference order follows how
    strongly each event is felt.
    """
    for candidates in (
        [section.start for section in music.sections],
        list(music.impact_points),
        list(music.downbeats),
        list(music.beats),
    ):
        inside = [value for value in candidates if low <= value <= high]
        if inside:
            return min(inside, key=lambda value: abs(value - sum((low, high)) / 2))
    return None


def _shape_hook(
    structure: list[DirectorSection],
    music: MusicMap,
    duration: float,
    reference_asl: float,
) -> list[DirectorSection]:
    """Force the opening to be a 3–5s hook, then hand off to the beat-locked body.

    Music-section boundaries do not care about our delivery format: on an 18s
    window they routinely put the first change at 2.1s or 7.4s, which is either
    too short to establish anything or long enough to bore.  So the head is
    reshaped to land inside the hook window, snapped to a musical event, and
    whatever section it displaced keeps the remainder of its span.
    """
    if not structure or duration <= HOOK_RANGE_SEC[1] + 1.0:
        return structure
    low, high = HOOK_RANGE_SEC
    high = min(high, duration * 0.35)
    low = min(low, high)
    head = structure[0]
    if low <= head.end <= high:
        return structure
    hook_end = _musical_boundary(music, low, high) or (low + high) / 2
    reshaped = [
        head.model_copy(
            update={
                "end": hook_end,
                # The hook is the one place a longer shot earns its keep: it has
                # to establish who and where before the body starts cutting.
                "average_shot_length": max(
                    head.average_shot_length, min(2.2, reference_asl * 1.35)
                ),
            }
        )
    ]
    for section in structure[1:]:
        if section.end <= hook_end:
            continue
        reshaped.append(
            section.model_copy(update={"start": max(section.start, hook_end)})
            if section.start < hook_end else section
        )
    if len(reshaped) == 1:
        reshaped.append(
            DirectorSection(
                role="impact", start=hook_end, end=duration,
                energy=max(0.6, head.energy),
                average_shot_length=max(0.4, min(2.2, reference_asl)),
            )
        )
    return reshaped


def _write_yaml_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            yaml.safe_dump(payload, stream, allow_unicode=True, sort_keys=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except Exception:
        Path(temporary).unlink(missing_ok=True)
        raise


def generate_director_plan(
    brief: DirectorBrief,
    music: MusicMap,
    style: StyleFingerprint | None,
    *,
    conn: sqlite3.Connection | None = None,
    output_path: Path | None = None,
) -> DirectorPlan:
    duration = min(brief.duration_sec, music.duration_sec)
    reference_asl = style.median_shot_length if style else 0.9
    editing_style = (
        compile_editing_style(style)
        if style is not None
        else default_editing_style()
    )
    if (
        style is not None
        and abs(style.duration_sec - duration) / max(duration, 1e-6) > 0.25
    ):
        # Normalized timestamps preserve a reference's exact macro edit only
        # when both works have comparable durations. For a substantially
        # longer/shorter target, reuse the measured duration grammar and cut
        # density instead of stretching every cut into slow, unrelated timing.
        prefix_cuts = [
            value for value in style.cut_timestamps if 0 < value < duration
        ]
        tail_cuts = [
            value for value in style.cut_timestamps
            if duration <= value < style.duration_sec
        ]
        prefix_density = len(prefix_cuts) / max(duration, 1e-6)
        tail_density = len(tail_cuts) / max(style.duration_sec - duration, 1e-6)
        # Social references commonly append a low-cut end card.  When the
        # requested duration cleanly ends at the dense editorial body, preserve
        # that prefix's exact cut skeleton instead of treating the end card as
        # evidence that the two works have incompatible durations.
        reference_body_prefix = (
            duration / max(style.duration_sec, 1e-6) >= 0.60
            and len(prefix_cuts) >= 3
            and prefix_density >= max(0.5, tail_density * 2.0)
        )
        editing_style = editing_style.model_copy(
            update={
                "normalized_cut_positions": (
                    [value / duration for value in prefix_cuts]
                    if reference_body_prefix else []
                )
            }
        )
    # House format: "spends everything after [the hook] on beat-locked
    # cutting" (see module docstring). That only actually happens in
    # _slots() when beat_grid_subdivision == "section_1_2_4" — the
    # "adaptive" default follows the reference's own cut *pattern* instead,
    # which snaps loosely to nearby beats but does not guarantee every beat
    # in a high-energy section gets its own cut. That gap was previously
    # only closed for tone == "vibe". A reference whose own measured
    # beat_sync_target is already high (this project: 0.65, comfortably
    # above the 0.55 default) has demonstrated the same beat-locked
    # character the "vibe" case exists for, so it earns the same grid —
    # not just chill/ambient content asked for it by name.
    if (
        "vibe" in {item.strip().lower() for item in brief.tone}
        or editing_style.beat_sync_target >= 0.6
    ):
        editing_style = editing_style.model_copy(
            update={"beat_grid_subdivision": "section_1_2_4"}
        )
    structure = []
    for section in music.sections:
        start, end = section.start, min(section.end, duration)
        if start >= duration or end <= start:
            continue
        role = _ROLE.get(section.type, "buildup")
        # Preserve the reference grammar while still tightening high-energy
        # sections.  The earlier linear factor over-compressed a 0.5s median
        # reference to ~0.34s and produced mechanically dense first cuts.
        asl = max(
            0.4,
            min(2.2, reference_asl * (1.45 - 0.35 * section.energy)),
        )
        structure.append(
            DirectorSection(
                role=role,
                start=start,
                end=end,
                energy=section.energy,
                average_shot_length=asl,
            )
        )
    roles = {item.role for item in structure}
    fallback_used = (
        len(structure) < 3
        or len(roles) < 3
        or not {"opening", "impact", "ending"}.issubset(roles)
    )
    if fallback_used:
        structure = _fallback_arc(duration, music, reference_asl)
    if structure[-1].end < duration:
        structure.append(
            DirectorSection(
                role="ending",
                start=structure[-1].end,
                end=duration,
                energy=max(0.25, structure[-1].energy * 0.7),
                average_shot_length=min(2.2, reference_asl * 1.4),
            )
        )
    structure = _shape_hook(structure, music, duration, reference_asl)
    if conn is not None:
        revision = (
            conn.execute(
                "SELECT coalesce(max(version),0)+1 FROM director_plans WHERE project_id=?",
                (brief.project_id,),
            ).fetchone()[0]
        )
    else:
        revision = 1
    impact_count = sum(
        1 for point in music.impact_points if point <= duration
    )
    density_cap = max(1, round(duration / 2.5))
    plan = DirectorPlan(
        project_id=brief.project_id,
        revision=revision,
        duration_sec=duration,
        primary_characters=brief.primary_characters,
        tone=brief.tone,
        structure=structure,
        visual_rules={"prefer": brief.prefer, "avoid": brief.avoid},
        sound_strategy="保留叙事原音；build 渐进，impact 点释放音乐与重点音效",
        impact_budget=ImpactBudget(
            sfx_max=min(density_cap, max(1, impact_count)),
            flash_max=min(3, max(1, impact_count // 3)),
            shake_max=min(4, max(1, impact_count // 2)),
        ),
        editing_style=editing_style,
        generation={
            "method": "deterministic_music_aligned",
            "music_map_version": music.version,
            "style_fingerprint_version": style.version if style else None,
            "editing_style_profile_version": editing_style.version,
            "editing_style_profile_id": editing_style.id,
            "llm_used": False,
            "fallback_arc_used": fallback_used,
        },
    )
    payload = plan.model_dump(mode="json")
    if output_path is not None:
        _write_yaml_atomic(output_path, payload)
    if conn is not None:
        with conn:
            conn.execute(
                """
                INSERT INTO director_plans(id,project_id,version,plan_yaml,generation_json)
                VALUES (?,?,?,?,?)
                """,
                (
                    brief.project_id,
                    brief.project_id,
                    revision,
                    yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
                    json.dumps(plan.generation, ensure_ascii=False, sort_keys=True),
                ),
            )
    return plan


__all__ = [
    "DIRECTOR_PLAN_VERSION",
    "DirectorBrief",
    "DirectorPlan",
    "generate_director_plan",
]
