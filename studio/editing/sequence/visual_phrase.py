"""Deterministic music-to-visual phrase grammar."""
from __future__ import annotations

from statistics import median
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from studio.editing.music import MusicMap

VISUAL_PHRASE_VERSION = "visual-phrase-plan-1.2.0"


class VisualPhrase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    index: int = Field(..., ge=0)
    start: float = Field(..., ge=0)
    end: float = Field(..., gt=0)
    kind: Literal["hook", "lift", "drive", "breathe", "climax", "resolve"]
    beat_times: list[float]
    cut_times: list[float]
    shot_intents: list[
        Literal["hold", "establish", "carry", "reverse", "impact", "settle"]
    ]
    energy: float = Field(..., ge=0, le=1)
    impact_count: int = Field(..., ge=0)


class VisualPhrasePlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: str = VISUAL_PHRASE_VERSION
    duration_sec: float = Field(..., gt=0)
    beat_interval_sec: float = Field(..., gt=0)
    phrases: list[VisualPhrase]
    cut_times: list[float]


def _complete_grid(music: MusicMap, duration: float) -> list[float]:
    beats = sorted(value for value in music.beats if 0 <= value <= duration)
    if len(beats) < 2:
        step = 60.0 / music.bpm if music.bpm > 0 else 0.5
        return [index * step for index in range(int(duration / step) + 1)]
    step = median(right - left for left, right in zip(beats, beats[1:]))
    cursor = beats[0] - step
    while cursor >= -step * 0.2:
        beats.insert(0, max(0.0, cursor))
        cursor -= step
    cursor = beats[-1] + step
    while cursor < duration + step * 0.2:
        beats.append(min(duration, cursor))
        cursor += step
    return sorted({round(value, 6) for value in beats if 0 <= value <= duration})


def plan_visual_phrases(
    music: MusicMap, *, duration_sec: float, beats_per_phrase: int = 8
) -> VisualPhrasePlan:
    """Compile eight-beat units into varied visual cutting obligations."""
    if beats_per_phrase < 4:
        raise ValueError("beats_per_phrase 必须 >= 4")
    duration = min(duration_sec, music.duration_sec)
    grid = _complete_grid(music, duration)
    step = median(right - left for left, right in zip(grid, grid[1:]))
    starts = list(range(0, max(1, len(grid) - 1), beats_per_phrase))
    phrases, all_cuts = [], []
    previous_energy = None
    unique_impacts = sorted({
        round(value, 6)
        for value in music.impact_points
        if 0 < value < duration
    })
    accent_driven = len(unique_impacts) / duration >= 0.65
    section_changes = sorted({
        round(section.start, 6)
        for section in music.sections[1:]
        if 0 < section.start < duration
    })
    accent_cuts = sorted(set([*unique_impacts, *section_changes]))
    if accent_driven:
        # Impacts can be separated by a melodic run. Add the nearest measured
        # onset only when a gap would otherwise exceed the maximum useful hold.
        changed = True
        while changed:
            changed = False
            boundaries = [0.0, *accent_cuts, duration]
            for left, right in zip(boundaries, boundaries[1:]):
                if right - left <= 1.45:
                    continue
                options = [
                    onset for onset in music.onsets
                    if left + 0.35 < onset < right - 0.35
                ]
                if options:
                    accent_cuts.append(
                        min(options, key=lambda value: abs(value - (left + right) / 2))
                    )
                    accent_cuts = sorted(set(accent_cuts))
                    changed = True
                    break
    for phrase_index, offset in enumerate(starts):
        end_index = min(offset + beats_per_phrase, len(grid) - 1)
        start = grid[offset]
        end = duration if phrase_index == len(starts) - 1 else grid[end_index]
        beat_times = [value for value in grid[offset:end_index] if value < end]
        energy_values = music.beat_energy[offset:min(end_index, len(music.beat_energy))]
        energy = sum(energy_values) / len(energy_values) if energy_values else 0.5
        impact_count = sum(start <= value < end for value in music.impact_points)
        phrase_duration = max(end - start, 1e-6)
        release_overlap = sum(
            max(0.0, min(end, section.end) - max(start, section.start))
            for section in music.sections
            if section.type.lower() in {"release", "break", "silence"}
        ) / phrase_duration
        if phrase_index == 0:
            kind = "hook"
        elif phrase_index == len(starts) - 1:
            kind = "climax" if energy >= 0.48 or impact_count else "resolve"
        elif release_overlap >= 0.35:
            # Section boundaries are a stronger musical signal than beat RMS.
            # A release must create visible breathing room instead of inheriting
            # the surrounding high-energy beat grid.
            kind = "breathe"
        elif previous_energy is not None and energy < previous_energy - 0.08:
            kind = "breathe"
        elif previous_energy is not None and energy > previous_energy + 0.08:
            kind = "lift"
        else:
            kind = "drive" if impact_count else "breathe"
        previous_energy = energy
        grammar = {
            "hook": ([0, 1, 2, 3, 5, 7],
                     ["establish", "carry", "reverse", "impact", "hold", "carry"]),
            "lift": ([0, 2, 4, 6], ["carry", "reverse", "carry", "impact"]),
            "drive": ([0, 2, 4, 6], ["carry", "reverse", "carry", "impact"]),
            "breathe": ([0, 4], ["hold", "settle"]),
            "climax": (
                list(range(8)),
                ["carry", "reverse", "carry", "impact",
                 "carry", "reverse", "impact", "settle"],
            ),
            "resolve": ([0, 4], ["hold", "settle"]),
        }
        indices, intents = grammar[kind]
        if accent_driven:
            # Dense percussive music is perceived through accents, not through
            # an evenly spaced tempo grid. Preserve every detected impact and
            # structural section change; the beat grid remains metadata only.
            selected = [
                value for value in accent_cuts
                if start <= value < end
            ]
            intent_cycle = {
                "hook": ["establish", "impact", "carry", "impact"],
                "lift": ["carry", "impact", "reverse", "impact"],
                "drive": ["carry", "impact", "reverse", "impact"],
                "breathe": ["hold", "settle"],
                "climax": ["carry", "impact", "reverse", "impact"],
                "resolve": ["hold", "settle"],
            }[kind]
            selected_pairs = [
                (value, intent_cycle[index % len(intent_cycle)])
                for index, value in enumerate(sorted(set(selected)))
            ]
        else:
            selected_pairs = [
                (beat_times[index], intent)
                for index, intent in zip(indices, intents, strict=True)
                if index < len(beat_times) and 0 < beat_times[index] < duration
            ]
        selected = [value for value, _ in selected_pairs]
        all_cuts.extend(selected)
        phrases.append(
            VisualPhrase(
                index=phrase_index, start=start, end=end, kind=kind,
                beat_times=beat_times, cut_times=selected,
                shot_intents=[intent for _, intent in selected_pairs],
                energy=max(0.0, min(1.0, energy)),
                impact_count=impact_count,
            )
        )
    return VisualPhrasePlan(
        duration_sec=duration, beat_interval_sec=step,
        phrases=phrases, cut_times=sorted(set(all_cuts)),
    )


__all__ = [
    "VISUAL_PHRASE_VERSION", "VisualPhrase", "VisualPhrasePlan",
    "plan_visual_phrases",
]
