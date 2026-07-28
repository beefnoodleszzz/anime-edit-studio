"""Deterministic creative QA for a planned edit's rhythm.

This is intentionally separate from delivery QA: a render can be technically
valid while still missing the reference's cutting cadence.
"""
from __future__ import annotations

from statistics import median
from math import ceil

from pydantic import BaseModel, ConfigDict, Field

from studio.creative.reference import EditingStyleProfile
from studio.editing.music import MusicMap
from studio.editspec.schema import EditSpec

RHYTHM_QA_VERSION = "rhythm-qa-1.1.0"


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
    onset_sync_ratio: float = 0.0
    max_sync_error_sec: float = 0.0
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
    anchors = sorted(set([
        *music.beats,
        *music.onsets,
        *music.impact_points,
        *[
            section.start
            for section in music.sections[1:]
            if 0 < section.start < music.duration_sec
        ],
    ]))
    errors = [
        min(abs(cut - anchor) for anchor in anchors)
        for cut in cuts
    ] if anchors else [1.0 for _ in cuts]
    synced = sum(error <= profile.beat_tolerance_sec for error in errors)
    tight_synced = sum(error <= 0.04 for error in errors)
    sync_ratio = synced / len(cuts) if cuts else 0.0
    median_duration = median(durations) if durations else 0.0
    required_sync_error = (
        sorted(errors)[
            min(
                len(errors) - 1,
                max(0, ceil(profile.beat_sync_target * len(errors)) - 1),
            )
        ]
        if errors else 0.0
    )
    tight_target = max(0.0, profile.beat_sync_target - 0.15)

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
            tolerance=0.03,
            passed=sync_ratio + 0.03 >= profile.beat_sync_target,
        ),
        RhythmCheck(
            metric="tight_sync_ratio",
            actual=tight_synced / len(cuts) if cuts else 0.0,
            target=tight_target,
            tolerance=0.05,
            passed=(tight_synced / len(cuts) if cuts else 0.0) + 0.05
            >= tight_target,
        ),
        RhythmCheck(
            metric="required_sync_error_sec",
            actual=required_sync_error,
            target=profile.beat_tolerance_sec,
            tolerance=0.0,
            passed=required_sync_error <= profile.beat_tolerance_sec,
        ),
    ]
    return RhythmQAResult(
        style_id=profile.id,
        shot_count=len(ordered),
        cut_density=density,
        median_shot_length=median_duration,
        beat_sync_ratio=sync_ratio,
        onset_sync_ratio=tight_synced / len(cuts) if cuts else 0.0,
        max_sync_error_sec=max(errors, default=0.0),
        checks=checks,
        passed=all(check.passed for check in checks),
    )


__all__ = [
    "RHYTHM_QA_VERSION",
    "RhythmCheck",
    "RhythmQAResult",
    "evaluate_rhythm",
]
