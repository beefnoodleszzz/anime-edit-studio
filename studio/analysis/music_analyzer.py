"""MusicAnalyzer: target track -> MusicTimeline (REFACTOR.md §5.2, §6.5).

Reuses ``studio.editing.music.map.analyze_music`` — the existing
librosa-based beat/section/onset/silence/riser/break detector — rather than
reimplementing proven signal-processing. This module's job is narrower:
select a tempo with a confidence, and curate a bounded set of typed
``Accent`` events from MusicMap's raw output instead of promoting every
onset to a visual instruction (§7.1 "不得把全部 onset 都变成视觉指令").
"""
from __future__ import annotations

from pathlib import Path

from studio.core.hashing import file_sha256
from studio.editing.music.map import MusicMap, analyze_music
from studio.spec.music_timeline import Accent, MusicTimeline, Section

IMPACT_STRENGTH = 0.85
BEAT_STRENGTH = 0.4
DOWNBEAT_STRENGTH = 0.7


def _tempo_confidence(music_map: MusicMap) -> float:
    if music_map.bpm <= 0 or len(music_map.beats) < 4:
        return 0.2
    intervals = [b - a for a, b in zip(music_map.beats, music_map.beats[1:])]
    if not intervals:
        return 0.3
    mean_interval = sum(intervals) / len(intervals)
    if mean_interval <= 0:
        return 0.3
    variance = sum((i - mean_interval) ** 2 for i in intervals) / len(intervals)
    stability = 1.0 - min(1.0, (variance ** 0.5) / mean_interval)
    return max(0.2, min(0.95, stability))


def _build_accents(music_map: MusicMap) -> list[Accent]:
    accents: list[Accent] = []
    downbeats = set(music_map.downbeats)
    for beat in music_map.beats:
        is_downbeat = beat in downbeats
        accents.append(
            Accent(
                sec=beat,
                kind="downbeat" if is_downbeat else "beat",
                strength=DOWNBEAT_STRENGTH if is_downbeat else BEAT_STRENGTH,
                confidence=0.85,
            )
        )
    for sec in music_map.impact_points:
        accents.append(
            Accent(sec=sec, kind="impact", strength=IMPACT_STRENGTH, confidence=0.8)
        )
    boundaries = sorted({s.start for s in music_map.sections} | {s.end for s in music_map.sections})
    for sec in boundaries:
        accents.append(
            Accent(
                sec=sec, kind="section_boundary", strength=0.9, confidence=0.75,
                anticipation_sec=0.15, release_sec=0.2,
            )
        )
    for interval in music_map.breaks:
        accents.append(Accent(sec=interval.start, kind="break_entry", strength=0.7, confidence=0.6))
        accents.append(Accent(sec=interval.end, kind="break_exit", strength=0.8, confidence=0.6))
    for interval in music_map.risers:
        accents.append(
            Accent(sec=interval.end, kind="riser_peak", strength=0.85, confidence=0.65, anticipation_sec=0.1)
        )
    for interval in music_map.silences:
        accents.append(Accent(sec=interval.end, kind="silence_hit", strength=0.6, confidence=0.55))
    accents.sort(key=lambda accent: accent.sec)
    return accents


def analyze_music_timeline(path: Path, *, cache_root: Path) -> MusicTimeline:
    """Analyze a target track into a MusicTimeline, reusing the verified MusicMap pipeline."""
    music_map = analyze_music(path, cache_root=cache_root)
    return MusicTimeline(
        source_hash=file_sha256(path),
        duration_sec=music_map.duration_sec,
        tempo_candidates=[],
        selected_tempo=music_map.bpm,
        tempo_confidence=_tempo_confidence(music_map),
        beats=music_map.beats,
        downbeats=music_map.downbeats,
        bars=music_map.bars,
        onsets=music_map.onsets,
        sections=[
            Section(start_sec=s.start, end_sec=s.end, label=s.type)
            for s in music_map.sections
        ],
        energy_curve=[],
        spectral_novelty=[(sec, 1.0) for sec in music_map.spectral_change_points],
        silences=[{"start_sec": s.start, "end_sec": s.end} for s in music_map.silences],
        breaks=[{"start_sec": s.start, "end_sec": s.end} for s in music_map.breaks],
        risers=[{"start_sec": s.start, "end_sec": s.end} for s in music_map.risers],
        phrases=[],
        accents=_build_accents(music_map),
    )


__all__ = ["analyze_music_timeline"]
