"""Deterministic musical accents for virtual-camera choreography."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .map import MusicMap

MUSIC_MOTION_MAP_VERSION = "music-motion-map-1.1.0"


class MotionAccent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sec: float = Field(..., ge=0)
    kind: Literal["impact", "downbeat", "beat"]
    strength: float = Field(..., ge=0, le=1)
    anticipation_sec: float = Field(..., gt=0)
    release_sec: float = Field(..., gt=0)
    target_velocity: float = Field(..., ge=0)


class MusicMotionMap(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str = MUSIC_MOTION_MAP_VERSION
    source_music_map_version: str
    duration_sec: float = Field(..., gt=0)
    accents: list[MotionAccent]

    def nearest(self, sec: float, *, tolerance_sec: float) -> MotionAccent | None:
        candidates = [
            accent for accent in self.accents
            if abs(accent.sec - sec) <= tolerance_sec
        ]
        return min(candidates, key=lambda value: abs(value.sec - sec)) if candidates else None


def _near(values: list[float], sec: float, tolerance: float) -> bool:
    return any(abs(value - sec) <= tolerance for value in values)


def build_music_motion_map(
    music: MusicMap,
    *,
    duration_sec: float | None = None,
) -> MusicMotionMap:
    """Retain repeated, meaningful pulses and discard incidental transients."""
    duration = min(duration_sec or music.duration_sec, music.duration_sec)
    beat_period = 60.0 / music.bpm if music.bpm > 0 else 0.5
    energies = music.beat_energy[: len(music.beats)]
    ordered = sorted(energies)
    median = ordered[len(ordered) // 2] if ordered else 0.5
    candidates: list[MotionAccent] = []
    for index, sec in enumerate(music.beats):
        if sec > duration:
            continue
        energy = energies[index] if index < len(energies) else median
        impact = _near(music.impact_points, sec, beat_period * 0.24)
        downbeat = _near(music.downbeats, sec, beat_period * 0.12)
        if not impact and not downbeat and energy < max(0.3, median):
            continue
        kind: Literal["impact", "downbeat", "beat"] = (
            "impact" if impact else "downbeat" if downbeat else "beat"
        )
        strength = min(
            1.0,
            max(
                0.72 if impact else 0.55 if downbeat else 0.0,
                max(0.0, energy) * 0.7
                + (0.3 if impact else 0.18 if downbeat else 0.0),
            ),
        )
        candidates.append(
            MotionAccent(
                sec=sec,
                kind=kind,
                strength=strength,
                anticipation_sec=min(
                    0.24,
                    max(0.14, beat_period * (0.28 + 0.18 * strength)),
                ),
                release_sec=min(0.4, max(0.14, beat_period * (0.55 - 0.2 * strength))),
                target_velocity=0.08 + 0.24 * strength,
            )
        )
    # Nearby detections represent one musical instruction. Keep the stronger,
    # higher-priority event rather than making the camera jitter twice.
    merged: list[MotionAccent] = []
    priority = {"beat": 0, "downbeat": 1, "impact": 2}
    minimum_gap = max(0.12, beat_period * 0.35)
    for accent in candidates:
        if merged and accent.sec - merged[-1].sec < minimum_gap:
            previous = merged[-1]
            if (priority[accent.kind], accent.strength) > (
                priority[previous.kind],
                previous.strength,
            ):
                merged[-1] = accent
        else:
            merged.append(accent)
    return MusicMotionMap(
        source_music_map_version=music.version,
        duration_sec=duration,
        accents=merged,
    )


__all__ = [
    "MUSIC_MOTION_MAP_VERSION",
    "MotionAccent",
    "MusicMotionMap",
    "build_music_motion_map",
]
