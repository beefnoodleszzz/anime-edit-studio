from pathlib import Path

import numpy as np
import soundfile as sf

from studio.editing.music import analyze_music


def test_music_map_detects_beats_sections_and_silence(tmp_path: Path):
    sample_rate = 22050
    duration = 6
    audio = np.zeros(sample_rate * duration, np.float32)
    for second in np.arange(0, duration, 0.5):
        start = round(second * sample_rate)
        length = min(800, len(audio) - start)
        audio[start : start + length] += np.hanning(length).astype(np.float32)
    audio[2 * sample_rate : 3 * sample_rate] = 0
    path = tmp_path / "music.wav"
    sf.write(path, audio, sample_rate)
    result = analyze_music(path, cache_root=tmp_path / "cache")
    assert result.duration_sec == 6
    assert result.bpm > 0
    assert result.beats
    assert result.sections
    assert result.silences
    assert analyze_music(path, cache_root=tmp_path / "cache") == result
