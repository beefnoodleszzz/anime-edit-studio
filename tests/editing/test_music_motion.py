from studio.editing.music import MusicMap, build_music_motion_map
from studio.editing.music.map import MusicSection


def _music() -> MusicMap:
    return MusicMap(
        duration_sec=3.0,
        bpm=120.0,
        beats=[0.5, 1.0, 1.5, 2.0, 2.5],
        bars=[0.5, 2.5],
        downbeats=[0.5, 2.5],
        onsets=[0.5, 1.02, 1.5, 2.0, 2.5],
        beat_energy=[0.2, 0.1, 0.65, 0.8, 0.5],
        sections=[MusicSection(start=0, end=3, type="drop", energy=0.8)],
        impact_points=[1.02, 2.0],
        risers=[],
        breaks=[],
        silences=[],
        spectral_change_points=[],
    )


def test_music_motion_retains_structural_accents_and_filters_weak_beats():
    result = build_music_motion_map(_music())

    assert [accent.sec for accent in result.accents] == [0.5, 1.0, 1.5, 2.0, 2.5]
    assert result.accents[1].kind == "impact"
    assert result.accents[1].strength > result.accents[2].strength
    assert all(0.14 <= item.anticipation_sec <= 0.24 for item in result.accents)
    assert all(0.14 <= item.release_sec <= 0.4 for item in result.accents)


def test_music_motion_is_duration_bounded_and_deterministic():
    first = build_music_motion_map(_music(), duration_sec=1.6)
    second = build_music_motion_map(_music(), duration_sec=1.6)

    assert first == second
    assert all(accent.sec <= 1.6 for accent in first.accents)
