from studio.editing.music import MusicMap, rank_music_segments
from studio.editing.music.map import MusicSection, TimeRange


def test_segment_ranker_prefers_clear_energetic_bar_aligned_window():
    beats = [value * 0.5 for value in range(48)]
    music = MusicMap(
        duration_sec=24,
        bpm=120,
        beats=beats,
        bars=beats[::4],
        downbeats=beats[::4],
        onsets=[8 + value * 0.25 for value in range(60)],
        beat_energy=[0.2] * 16 + [0.9] * 32,
        sections=[
            MusicSection(type="intro", start=0, end=8, energy=0.2),
            MusicSection(type="drop", start=8, end=24, energy=0.9),
        ],
        impact_points=[8, 10, 12, 14, 16, 18, 20, 22],
        risers=[],
        breaks=[],
        silences=[TimeRange(start=1, end=3)],
        spectral_change_points=[8],
    )

    ranked = rank_music_segments(music, duration_sec=8, limit=2)

    assert ranked[0].start_sec == 6
    assert ranked[0].local_bpm == 120
    assert ranked[0].beat_count == 16
    assert ranked[0].score > ranked[1].score


def test_segment_ranker_rejects_invalid_duration():
    music = MusicMap(
        duration_sec=2, bpm=120, beats=[0, .5, 1, 1.5],
        bars=[0], downbeats=[0], onsets=[], beat_energy=[],
        sections=[MusicSection(type="intro", start=0, end=2, energy=.5)],
        impact_points=[], risers=[], breaks=[], silences=[],
        spectral_change_points=[],
    )
    try:
        rank_music_segments(music, duration_sec=3)
    except ValueError as exc:
        assert "时长" in str(exc)
    else:
        raise AssertionError("expected ValueError")
