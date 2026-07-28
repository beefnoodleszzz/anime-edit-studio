from studio.editing.music import MusicMap
from studio.editing.music.map import MusicSection
from studio.editing.sequence import plan_visual_phrases


def test_visual_phrases_create_varied_eight_beat_grammar():
    beats = [index * .5 for index in range(32)]
    music = MusicMap(
        duration_sec=16, bpm=120, beats=beats, bars=[0, 2, 4, 6],
        downbeats=[0, 2, 4, 6], onsets=beats,
        beat_energy=[.7] * 24 + [.9] * 8,
        sections=[MusicSection(type="build", start=0, end=16, energy=.7)],
        impact_points=[3.5, 7.5, 15], risers=[], breaks=[], silences=[],
        spectral_change_points=[],
    )
    result = plan_visual_phrases(music, duration_sec=16)
    assert [phrase.kind for phrase in result.phrases] == [
        "hook", "drive", "breathe", "climax"
    ]
    assert len(result.phrases[0].cut_times) == 5
    assert len(result.phrases[-1].cut_times) == 8
    assert result.cut_times == sorted(set(result.cut_times))
    assert all(0 < cut < 16 for cut in result.cut_times)


def test_release_section_forces_breathing_room_despite_hot_beats():
    beats = [index * .5 for index in range(32)]
    music = MusicMap(
        duration_sec=16, bpm=120, beats=beats, bars=[0, 2, 4, 6],
        downbeats=[0, 2, 4, 6], onsets=beats,
        beat_energy=[.95] * 32,
        sections=[
            MusicSection(type="intro", start=0, end=9.5, energy=.9),
            MusicSection(type="release", start=9.5, end=11.5, energy=.3),
            MusicSection(type="build", start=11.5, end=16, energy=.8),
        ],
        impact_points=[3.5, 7.5, 15], risers=[], breaks=[], silences=[],
        spectral_change_points=[],
    )

    result = plan_visual_phrases(music, duration_sec=16)

    assert result.phrases[2].kind == "breathe"
    assert len(result.phrases[2].cut_times) == 2


def test_dense_percussive_music_cuts_on_impacts_and_section_changes():
    beats = [index * .5 for index in range(30)]
    impacts = [.42, 1.81, 2.55, 2.76, 5.60, 6.78, 7.24, 7.38, 8.59, 12.35]
    music = MusicMap(
        duration_sec=15, bpm=120, beats=beats, bars=[0, 2, 4, 6],
        downbeats=[0, 2, 4, 6], onsets=beats,
        beat_energy=[.9] * 30,
        sections=[
            MusicSection(type="intro", start=0, end=9.59, energy=.7),
            MusicSection(type="release", start=9.59, end=11.19, energy=.3),
            MusicSection(type="build", start=11.19, end=15, energy=.8),
        ],
        impact_points=impacts, risers=[], breaks=[], silences=[],
        spectral_change_points=[],
    )

    result = plan_visual_phrases(music, duration_sec=15)

    assert set(impacts).issubset(result.cut_times)
    assert 9.59 in result.cut_times
    assert 11.19 in result.cut_times
    assert .5 not in result.cut_times
