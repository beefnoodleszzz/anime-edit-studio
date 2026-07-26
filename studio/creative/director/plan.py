"""Typed DirectorPlan generation aligned to measured music structure."""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from studio.creative.reference import StyleFingerprint
from studio.editing.music import MusicMap

DIRECTOR_PLAN_VERSION = "1"


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
    fallback_used = len(structure) < 3 or len({item.role for item in structure}) < 3
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
        generation={
            "method": "deterministic_music_aligned",
            "music_map_version": music.version,
            "style_fingerprint_version": style.version if style else None,
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
