"""MusicMap: beats, phrases, energy events, silence, and spectral changes."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import librosa
import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from studio.core.cache import JsonCache
from studio.core.hashing import analysis_cache_key, file_sha256

MUSIC_MAP_VERSION = "music-map-1.0.0"
MODEL = "librosa-deterministic"
MODEL_VERSION = f"librosa-{librosa.__version__}"
SAMPLE_RATE = 22050
HOP_LENGTH = 512


class TimeRange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start: float = Field(..., ge=0)
    end: float = Field(..., gt=0)


class MusicSection(TimeRange):
    type: str
    energy: float = Field(..., ge=0, le=1)


class MusicMap(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str = MUSIC_MAP_VERSION
    asset_id: str | None = None
    duration_sec: float
    bpm: float
    beats: list[float]
    bars: list[float]
    downbeats: list[float]
    onsets: list[float]
    beat_energy: list[float]
    sections: list[MusicSection]
    impact_points: list[float]
    risers: list[TimeRange]
    breaks: list[TimeRange]
    silences: list[TimeRange]
    spectral_change_points: list[float]


def _normalise(values: np.ndarray) -> np.ndarray:
    if values.size == 0:
        return values
    low, high = np.percentile(values, [5, 95])
    return np.clip((values - low) / max(float(high - low), 1e-8), 0, 1)


def _ranges(mask: np.ndarray, times: np.ndarray, *, minimum_sec: float) -> list[TimeRange]:
    ranges = []
    start = None
    for index, active in enumerate(np.r_[mask, False]):
        if active and start is None:
            start = index
        elif not active and start is not None:
            end = min(index, len(times) - 1)
            if times[end] - times[start] >= minimum_sec:
                ranges.append(TimeRange(start=float(times[start]), end=float(times[end])))
            start = None
    return ranges


def _section_boundaries(features: np.ndarray, duration: float) -> list[float]:
    frame_count = features.shape[1]
    if frame_count < 8:
        return [0.0, duration]
    count = max(2, min(8, round(duration / 4)))
    try:
        recurrence = librosa.segment.recurrence_matrix(
            features, mode="affinity", sym=True
        )
        boundaries = librosa.segment.agglomerative(recurrence, count)
        seconds = librosa.frames_to_time(
            boundaries, sr=SAMPLE_RATE, hop_length=HOP_LENGTH
        )
        values = sorted({0.0, *map(float, seconds), float(duration)})
    except (ValueError, np.linalg.LinAlgError):
        values = np.linspace(0, duration, count + 1).tolist()
    return [
        value for index, value in enumerate(values)
        if index == 0 or value - values[index - 1] >= 0.35
    ]


def _compute(path: Path, asset_id: str | None) -> MusicMap:
    audio, sample_rate = librosa.load(path, sr=SAMPLE_RATE, mono=True)
    duration = float(librosa.get_duration(y=audio, sr=sample_rate))
    if audio.size == 0 or duration <= 0:
        raise ValueError(f"音乐为空: {path}")
    onset_envelope = librosa.onset.onset_strength(
        y=audio, sr=sample_rate, hop_length=HOP_LENGTH
    )
    tempo, beat_frames = librosa.beat.beat_track(
        onset_envelope=onset_envelope,
        sr=sample_rate,
        hop_length=HOP_LENGTH,
    )
    bpm = float(np.asarray(tempo).reshape(-1)[0]) if np.size(tempo) else 0.0
    beats = librosa.frames_to_time(
        beat_frames, sr=sample_rate, hop_length=HOP_LENGTH
    )
    onset_frames = librosa.onset.onset_detect(
        onset_envelope=onset_envelope,
        sr=sample_rate,
        hop_length=HOP_LENGTH,
        backtrack=True,
    )
    onsets = librosa.frames_to_time(
        onset_frames, sr=sample_rate, hop_length=HOP_LENGTH
    )
    rms = librosa.feature.rms(y=audio, hop_length=HOP_LENGTH)[0]
    energy = _normalise(rms)
    times = librosa.frames_to_time(
        np.arange(len(rms)), sr=sample_rate, hop_length=HOP_LENGTH
    )
    chroma = librosa.feature.chroma_cqt(
        y=audio, sr=sample_rate, hop_length=HOP_LENGTH
    )
    boundaries = _section_boundaries(np.vstack([chroma, energy[None, :]]), duration)
    sections = []
    for index, (start, end) in enumerate(zip(boundaries, boundaries[1:])):
        mask = (times >= start) & (times < end)
        section_energy = float(np.mean(energy[mask])) if mask.any() else 0.0
        prior = sections[-1].energy if sections else section_energy
        if index == 0:
            kind = "intro"
        elif section_energy >= 0.72 and section_energy - prior >= 0.12:
            kind = "drop"
        elif section_energy <= 0.2:
            kind = "break"
        elif section_energy - prior >= 0.08:
            kind = "build"
        elif index == len(boundaries) - 2:
            kind = "outro"
        else:
            kind = "release" if prior - section_energy >= 0.1 else "verse"
        sections.append(
            MusicSection(type=kind, start=start, end=end, energy=section_energy)
        )
    beat_energy = [
        float(energy[min(int(frame), len(energy) - 1)]) for frame in beat_frames
    ]
    onset_strengths = onset_envelope[onset_frames] if len(onset_frames) else np.array([])
    impact_threshold = (
        float(np.percentile(onset_strengths, 82)) if onset_strengths.size else float("inf")
    )
    impacts = [
        float(time) for time, strength in zip(onsets, onset_strengths, strict=True)
        if strength >= impact_threshold
    ]
    slope = np.convolve(energy, np.ones(12) / 12, mode="same")
    slope = np.gradient(slope)
    risers = _ranges(slope > np.percentile(slope, 78), times, minimum_sec=0.25)
    silence_threshold = max(0.01, float(np.percentile(rms, 12)) * 0.7)
    silences = _ranges(rms <= silence_threshold, times, minimum_sec=0.07)
    break_mask = energy <= 0.18
    breaks = _ranges(break_mask, times, minimum_sec=0.35)
    centroid = librosa.feature.spectral_centroid(
        y=audio, sr=sample_rate, hop_length=HOP_LENGTH
    )[0]
    spectral_delta = np.abs(np.gradient(_normalise(centroid)))
    spectral_threshold = np.percentile(spectral_delta, 96)
    spectral_changes = times[
        librosa.util.peak_pick(
            spectral_delta, pre_max=3, post_max=3, pre_avg=6, post_avg=6,
            delta=float(spectral_threshold), wait=5,
        )
    ]
    bars = [float(value) for value in beats[::4]]
    return MusicMap(
        asset_id=asset_id,
        duration_sec=duration,
        bpm=bpm,
        beats=[float(value) for value in beats],
        bars=bars,
        downbeats=bars,
        onsets=[float(value) for value in onsets],
        beat_energy=beat_energy,
        sections=sections,
        impact_points=impacts,
        risers=risers,
        breaks=breaks,
        silences=silences,
        spectral_change_points=[float(value) for value in spectral_changes],
    )


def analyze_music(
    path: Path,
    *,
    cache_root: Path,
    asset_id: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> MusicMap:
    digest = file_sha256(path)
    key = analysis_cache_key(
        asset_hash=digest,
        model=MODEL,
        model_version=MODEL_VERSION,
        pipeline_version=MUSIC_MAP_VERSION,
        parameters={"sample_rate": SAMPLE_RATE, "hop_length": HOP_LENGTH},
    )
    cache = JsonCache(cache_root)
    cached = cache.get("music-map-v2", key)
    result = MusicMap.model_validate(cached) if cached else _compute(path, asset_id)
    if cached is None:
        cache.put("music-map-v2", key, result.model_dump(mode="json"))
    if conn is not None and asset_id is not None:
        with conn:
            conn.execute(
                """
                INSERT INTO music_tracks(id,music_map_json,analysis_version,updated_at)
                VALUES (?,?,?,strftime('%Y-%m-%dT%H:%M:%fZ','now'))
                ON CONFLICT(id) DO UPDATE SET
                  music_map_json=excluded.music_map_json,
                  analysis_version=excluded.analysis_version,
                  updated_at=excluded.updated_at
                """,
                (
                    asset_id,
                    json.dumps(result.model_dump(mode="json"), ensure_ascii=False),
                    MUSIC_MAP_VERSION,
                ),
            )
    return result


__all__ = ["MUSIC_MAP_VERSION", "MusicMap", "analyze_music"]
