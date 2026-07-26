from pathlib import Path

import cv2
import numpy as np

from studio.creative.reference import fingerprint as module
from studio.editing.music.map import MusicMap, MusicSection, TimeRange


def test_style_fingerprint_extracts_editing_grammar(tmp_path: Path, monkeypatch):
    video = tmp_path / "reference.mp4"
    writer = cv2.VideoWriter(
        str(video), cv2.VideoWriter_fourcc(*"mp4v"), 12, (96, 54)
    )
    for color in ((10, 20, 30), (230, 200, 180), (20, 180, 240)):
        for _ in range(18):
            writer.write(np.full((54, 96, 3), color, np.uint8))
    writer.release()
    music = MusicMap(
        duration_sec=4.5,
        bpm=120,
        beats=[0.5, 1, 1.5, 2, 2.5, 3, 3.5, 4],
        bars=[0.5, 2.5],
        downbeats=[0.5, 2.5],
        onsets=[0.5, 1.5, 3],
        beat_energy=[0.2, 0.3, 0.5, 0.8, 0.9, 0.7, 0.5, 0.3],
        sections=[
            MusicSection(type="intro", start=0, end=1.5, energy=0.2),
            MusicSection(type="drop", start=1.5, end=4.5, energy=0.8),
        ],
        impact_points=[1.5],
        risers=[TimeRange(start=1, end=1.5)],
        breaks=[],
        silences=[TimeRange(start=1.45, end=1.5)],
        spectral_change_points=[1.5],
    )
    monkeypatch.setattr(module, "analyze_music", lambda *args, **kwargs: music)
    result = module.analyze_reference(video, cache_root=tmp_path / "cache")
    assert result.shot_count == 3
    assert len(result.shot_scale_sequence) == 3
    assert len(result.motion_direction_sequence) == 3
    assert result.music_structure[1]["type"] == "drop"
    assert "slow_motion_locations" in result.confidence
