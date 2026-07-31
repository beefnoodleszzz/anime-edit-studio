from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf

from studio.analysis.music_analyzer import analyze_music_timeline
from studio.spec.music_timeline import MusicTimeline


def _click_track(tmp_path: Path, *, sample_rate=22050, duration=6.0) -> Path:
    audio = np.zeros(int(sample_rate * duration), np.float32)
    for second in np.arange(0, duration, 0.5):
        start = round(second * sample_rate)
        length = min(800, len(audio) - start)
        audio[start:start + length] += np.hanning(length).astype(np.float32)
    audio[2 * sample_rate:3 * sample_rate] = 0  # silence gap
    path = tmp_path / "target.wav"
    sf.write(path, audio, sample_rate)
    return path


def test_music_timeline_has_beats_sections_and_accents(tmp_path):
    audio = _click_track(tmp_path)
    timeline = analyze_music_timeline(audio, cache_root=tmp_path / "cache")

    assert isinstance(timeline, MusicTimeline)
    assert timeline.duration_sec == 6.0
    assert timeline.selected_tempo > 0
    assert 0.0 <= timeline.tempo_confidence <= 1.0
    assert timeline.beats
    assert timeline.sections
    assert timeline.accents
    # Accents are curated from beats/downbeats/sections/breaks/risers/
    # silences/impacts only; "onset" itself is not an accent kind, so raw
    # onsets are never blindly promoted 1:1 (REFACTOR.md §7.1).
    allowed_kinds = {
        "beat", "downbeat", "impact", "section_boundary",
        "break_entry", "break_exit", "riser_peak", "silence_hit",
    }
    assert {a.kind for a in timeline.accents} <= allowed_kinds


def test_music_timeline_round_trips_and_is_deterministic(tmp_path):
    audio = _click_track(tmp_path)
    first = analyze_music_timeline(audio, cache_root=tmp_path / "cache")
    second = analyze_music_timeline(audio, cache_root=tmp_path / "cache")
    assert first == second
    restored = MusicTimeline.model_validate_json(first.model_dump_json())
    assert restored == first


def test_different_music_yields_different_accent_timings(tmp_path):
    audio_a = _click_track(tmp_path, duration=6.0)
    (tmp_path / "cache_b").mkdir()
    audio_b_path = tmp_path / "target_b.wav"
    sample_rate = 22050
    audio_b = np.zeros(int(sample_rate * 4.0), np.float32)
    for second in np.arange(0, 4.0, 0.3):
        start = round(second * sample_rate)
        length = min(600, len(audio_b) - start)
        audio_b[start:start + length] += np.hanning(length).astype(np.float32)
    sf.write(audio_b_path, audio_b, sample_rate)

    timeline_a = analyze_music_timeline(audio_a, cache_root=tmp_path / "cache")
    timeline_b = analyze_music_timeline(audio_b_path, cache_root=tmp_path / "cache")
    assert timeline_a.beats != timeline_b.beats
    assert timeline_a.duration_sec != timeline_b.duration_sec
