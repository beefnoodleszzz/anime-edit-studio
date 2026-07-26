import numpy as np

from studio.asset_intelligence.audio.analyzer import _music_likelihood, _rms


def test_rms_energy_orders_silence_and_signal():
    silence = np.zeros(8000, dtype=np.float32)
    signal = np.full(8000, 0.5, dtype=np.float32)
    assert _rms(silence) < _rms(signal)


def test_music_estimate_is_bounded():
    time = np.arange(16000) / 8000
    tone = np.sin(2 * np.pi * 440 * time).astype(np.float32)
    value = _music_likelihood(tone)
    assert 0 <= value <= 1
    assert value > 0.4
