"""Deterministic QA for the designed sound layer.

Separate from Technical QA (delivery integrity) and Rhythm QA (cut timing): this
gate asks whether the *sound* actually lands on the drums.  For every musical
impact that coincides with a cut it checks an on-beat impact cue exists within
one delivery frame, that risers precede the impacts they set up, and reports the
overall SFX density so an under-designed (or over-stuffed) cut is visible before
the owner listens.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from studio.core.timecode import Timebase
from studio.editing.music import MusicMap
from studio.editspec.schema import EditSpec

SOUND_QA_VERSION = "sound-qa-1.0.0"

_IMPACT_RECIPES = {"impact_low_v1", "sub_impact_v1"}
_RISER_RECIPES = {"riser_v1"}


class SoundCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")
    metric: str
    actual: float
    target: float
    passed: bool


class SoundQAResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: str = SOUND_QA_VERSION
    impact_targets: int
    covered_targets: int
    coverage_ratio: float = Field(..., ge=0, le=1)
    max_impact_error_frames: int
    orphan_risers: int
    sfx_density_per_10s: float
    checks: list[SoundCheck]
    passed: bool


def _absolute_cues(spec: EditSpec) -> list[tuple[float, str]]:
    cues: list[tuple[float, str]] = []
    for clip in spec.clips:
        for cue in clip.audio.sfx:
            cues.append((clip.timeline.in_sec + cue.at_sec, cue.recipe))
    return cues


def evaluate_sound_design(
    spec: EditSpec,
    music: MusicMap,
    *,
    coverage_target: float = 0.8,
) -> SoundQAResult:
    timebase = Timebase.from_fps(spec.timebase.fps, drop_frame=spec.timebase.drop_frame)
    frame_sec = 1.0 / max(timebase.fps_float, 1e-6)
    cues = _absolute_cues(spec)
    impact_cues = sorted(sec for sec, recipe in cues if recipe in _IMPACT_RECIPES)
    riser_cues = sorted(sec for sec, recipe in cues if recipe in _RISER_RECIPES)

    cut_starts = [c.timeline.in_sec for c in spec.clips if c.timeline.in_sec > 0]
    impacts = sorted(music.impact_points)
    targets = [
        cut for cut in cut_starts
        if any(abs(cut - impact) <= 0.06 for impact in impacts)
    ]

    covered = 0
    max_error = 0
    for target in targets:
        if not impact_cues:
            break
        nearest = min(impact_cues, key=lambda sec: abs(sec - target))
        error_frames = round(abs(nearest - target) / frame_sec)
        if error_frames <= 1:
            covered += 1
            max_error = max(max_error, error_frames)
    coverage = covered / len(targets) if targets else 1.0

    # A riser with no impact within a musically plausible follow window is an
    # orphan: it promises a hit that never lands.
    orphans = 0
    for riser in riser_cues:
        if not any(0 < impact - riser <= 0.6 for impact in impact_cues):
            orphans += 1

    density = len(cues) / max(spec.duration_sec / 10.0, 1e-6)

    checks = [
        SoundCheck(
            metric="impact_coverage",
            actual=round(coverage, 4),
            target=coverage_target,
            passed=coverage >= coverage_target,
        ),
        SoundCheck(
            metric="max_impact_error_frames",
            actual=float(max_error),
            target=1.0,
            passed=max_error <= 1,
        ),
        SoundCheck(
            metric="orphan_risers",
            actual=float(orphans),
            target=0.0,
            passed=orphans == 0,
        ),
    ]
    return SoundQAResult(
        version=SOUND_QA_VERSION,
        impact_targets=len(targets),
        covered_targets=covered,
        coverage_ratio=round(coverage, 4),
        max_impact_error_frames=max_error,
        orphan_risers=orphans,
        sfx_density_per_10s=round(density, 4),
        checks=checks,
        passed=all(check.passed for check in checks),
    )


__all__ = ["SOUND_QA_VERSION", "SoundCheck", "SoundQAResult", "evaluate_sound_design"]
