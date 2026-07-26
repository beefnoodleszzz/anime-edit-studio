from hashlib import sha256
from pathlib import Path
import wave

from studio.execution.audio import build_sound_recipe_library


def test_sound_recipe_library_is_deterministic_and_48khz(tmp_path: Path):
    first = build_sound_recipe_library(tmp_path / "a")
    second = build_sound_recipe_library(tmp_path / "b")
    assert len(first) == 4
    assert [sha256(p.read_bytes()).hexdigest() for p in first] == [
        sha256(p.read_bytes()).hexdigest() for p in second
    ]
    for path in first:
        with wave.open(str(path), "rb") as wav:
            assert wav.getframerate() == 48_000
            assert wav.getnchannels() == 1
            assert wav.getsampwidth() == 2
