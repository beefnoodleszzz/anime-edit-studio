"""Deterministic creative QA for a planned edit's rhythm.

This is intentionally separate from delivery QA: a render can be technically
valid while still missing the reference's cutting cadence.
"""
from __future__ import annotations

from statistics import median

from pydantic import BaseModel, ConfigDict, Field

from studio.creative.reference import EditingStyleProfile
from studio.editing.music import MusicMap
from studio.editspec.schema import EditSpec

RHYTHM_QA_VERSION = "rhythm-qa-1.0.0"


class RhythmCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metric: str
    actual: float
    target: float
    tolerance: float = Field(..., ge=0)
    passed: bool


class RhythmQAResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str = RHYTHM_QA_VERSION
    style_id: str
    shot_count: int
    cut_density: float
    median_shot_length: float
    beat_sync_ratio: float
    checks: list[RhythmCheck]
    passed: bool


def evaluate_rhythm(
    spec: EditSpec,
    music: MusicMap,
    profile: EditingStyleProfile,
) -> RhythmQAResult:
    """Compare measurable output rhythm with its versioned style contract."""
    ordered = sorted(spec.clips, key=lambda clip: clip.timeline.in_sec)
    durations = [clip.timeline.duration_sec for clip in ordered]
    cuts = [clip.timeline.in_sec for clip in ordered[1:]]
    density = len(cuts) / max(spec.duration_sec, 1e-6)
    synced = (
        sum(
            min(abs(cut - beat) for beat in music.beats)
            <= profile.beat_tolerance_sec
            for cut in cuts
        )
        if music.beats
        else 0
    )
    sync_ratio = synced / len(cuts) if cuts else 0.0
    median_duration = median(durations) if durations else 0.0

    density_tolerance = max(0.12, profile.target_cut_density * 0.18)
    median_tolerance = max(0.08, profile.median_shot_length * 0.25)
    checks = [
        RhythmCheck(
            metric="cut_density",
            actual=density,
            target=profile.target_cut_density,
            tolerance=density_tolerance,
            passed=abs(density - profile.target_cut_density) <= density_tolerance,
        ),
        RhythmCheck(
            metric="median_shot_length",
            actual=median_duration,
            target=profile.median_shot_length,
            tolerance=median_tolerance,
            passed=abs(median_duration - profile.median_shot_length)
            <= median_tolerance,
        ),
        RhythmCheck(
            metric="beat_sync_ratio",
            actual=sync_ratio,
            target=profile.beat_sync_target,
            tolerance=0.08,
            passed=sync_ratio + 0.08 >= profile.beat_sync_target,
        ),
    ]
    return RhythmQAResult(
        style_id=profile.id,
        shot_count=len(ordered),
        cut_density=density,
        median_shot_length=median_duration,
        beat_sync_ratio=sync_ratio,
        checks=checks,
        passed=all(check.passed for check in checks),
    )


__all__ = [
    "RHYTHM_QA_VERSION",
    "RhythmCheck",
    "RhythmQAResult",
    "evaluate_rhythm",
]
