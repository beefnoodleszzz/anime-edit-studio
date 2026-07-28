"""Deterministic ranking of musically editable soundtrack windows."""
from __future__ import annotations

from statistics import median

from pydantic import BaseModel, ConfigDict, Field

from .map import MusicMap

MUSIC_SEGMENT_RANKING_VERSION = "music-segment-ranking-1.0.0"


class MusicSegmentCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str = MUSIC_SEGMENT_RANKING_VERSION
    start_sec: float = Field(..., ge=0)
    end_sec: float = Field(..., gt=0)
    local_bpm: float = Field(..., ge=0)
    beat_count: int = Field(..., ge=0)
    impact_count: int = Field(..., ge=0)
    beat_clarity: float = Field(..., ge=0, le=1)
    phrase_clarity: float = Field(..., ge=0, le=1)
    onset_variety: float = Field(..., ge=0, le=1)
    energy: float = Field(..., ge=0, le=1)
    dynamic_arc: float = Field(..., ge=0, le=1)
    silence_penalty: float = Field(..., ge=0, le=1)
    score: float = Field(..., ge=0, le=1)


def _overlap(start: float, end: float, left: float, right: float) -> float:
    return max(0.0, min(end, right) - max(start, left))


def rank_music_segments(
    music: MusicMap,
    *,
    duration_sec: float,
    limit: int = 3,
) -> list[MusicSegmentCandidate]:
    """Rank bar-aligned windows by editability, never by genre semantics."""
    if duration_sec <= 0 or duration_sec > music.duration_sec:
        raise ValueError("候选音乐片段时长必须位于 (0, music.duration_sec]")
    if not 1 <= limit <= 20:
        raise ValueError("音乐片段输出数量必须在 1..20")

    starts = sorted(
        {
            0.0,
            *(
                value
                for value in (music.downbeats or music.bars or music.beats)
                if 0 <= value <= music.duration_sec - duration_sec
            ),
        }
    )
    candidates: list[MusicSegmentCandidate] = []
    for start in starts:
        end = start + duration_sec
        beats = [value for value in music.beats if start <= value < end]
        intervals = [
            right - left for left, right in zip(beats, beats[1:])
            if right > left
        ]
        if intervals:
            center = median(intervals)
            mean_error = sum(abs(value - center) for value in intervals) / len(intervals)
            beat_clarity = max(0.0, min(1.0, 1.0 - mean_error / max(center, 1e-6)))
            local_bpm = 60.0 / center
        else:
            beat_clarity = 0.0
            local_bpm = 0.0

        nearest_end = min(
            (abs(end - value) for value in (music.downbeats or music.bars or beats)),
            default=duration_sec,
        )
        beat_period = median(intervals) if intervals else 0.5
        boundary_fit = max(0.0, 1.0 - nearest_end / max(beat_period, 1e-6))
        phrase_multiple = (
            1.0 - min(abs((len(beats) % 8) - value) for value in (0, 8))
            / 8.0
            if beats else 0.0
        )
        phrase_clarity = max(0.0, min(1.0, 0.65 * boundary_fit + 0.35 * phrase_multiple))

        onsets = [value for value in music.onsets if start <= value < end]
        onset_density = len(onsets) / duration_sec
        onset_variety = max(0.0, 1.0 - abs(onset_density - 3.2) / 3.2)
        impacts = [value for value in music.impact_points if start <= value < end]

        section_weights = [
            (
                _overlap(start, end, section.start, section.end),
                section.energy,
            )
            for section in music.sections
        ]
        covered = sum(weight for weight, _ in section_weights)
        energy = (
            sum(weight * value for weight, value in section_weights) / covered
            if covered else 0.0
        )
        section_values = [
            value for weight, value in section_weights if weight > 0
        ]
        dynamic_arc = (
            min(1.0, max(section_values) - min(section_values) + 0.35)
            if section_values else 0.0
        )
        silence_sec = sum(
            _overlap(start, end, item.start, item.end)
            for item in music.silences
        )
        silence_penalty = min(1.0, silence_sec / max(duration_sec * 0.12, 1e-6))
        impact_score = min(1.0, len(impacts) / max(2.0, duration_sec / 4.0))
        score = (
            0.25 * beat_clarity
            + 0.22 * phrase_clarity
            + 0.14 * onset_variety
            + 0.16 * energy
            + 0.10 * dynamic_arc
            + 0.13 * impact_score
            - 0.18 * silence_penalty
        )
        candidates.append(
            MusicSegmentCandidate(
                start_sec=start,
                end_sec=end,
                local_bpm=local_bpm,
                beat_count=len(beats),
                impact_count=len(impacts),
                beat_clarity=beat_clarity,
                phrase_clarity=phrase_clarity,
                onset_variety=onset_variety,
                energy=energy,
                dynamic_arc=dynamic_arc,
                silence_penalty=silence_penalty,
                score=max(0.0, min(1.0, score)),
            )
        )

    candidates.sort(key=lambda item: (-item.score, item.start_sec))
    selected: list[MusicSegmentCandidate] = []
    for candidate in candidates:
        if any(
            _overlap(candidate.start_sec, candidate.end_sec, item.start_sec, item.end_sec)
            > duration_sec * 0.65
            for item in selected
        ):
            continue
        selected.append(candidate)
        if len(selected) >= limit:
            break
    return selected


__all__ = [
    "MUSIC_SEGMENT_RANKING_VERSION",
    "MusicSegmentCandidate",
    "rank_music_segments",
]
