from studio.creative.director import DirectorBrief, generate_director_plan
from studio.creative.reference import StyleFingerprint
from studio.editing.music.map import MusicMap, MusicSection
from studio.core.database import connect


def _fingerprint(*, beat_sync_ratio: float) -> StyleFingerprint:
    return StyleFingerprint(
        duration_sec=10, shot_count=5,
        shot_length_distribution={"p10": .4, "p25": .5, "p75": 1.1, "p90": 1.5},
        mean_shot_length=2, median_shot_length=.6, cut_density=1.4,
        hard_cut_ratio=.9, transition_types={"hard_cut": 4},
        beat_sync_ratio=beat_sync_ratio, music_structure=[], energy_curve=[],
        brightness_curve=[], color_progression=[], impact_points=[],
        silence_usage=0, sound_effect_density=.3, slow_motion_locations=[],
        shot_scale_sequence=[.2, .7, .3],
        motion_direction_sequence=["left", "right", "right"],
        motion_magnitude_sequence=[.5, 2.5, 1.2], camera_motion=[],
        cut_timestamps=[.5, 1.2, 3, 7], shot_durations=[.5, .7, 1.8, 4, 3],
        confidence={},
    )


def test_director_plan_aligns_to_music_and_persists(tmp_path):
    music = MusicMap(
        duration_sec=10,
        bpm=120,
        beats=[0, 0.5],
        bars=[0],
        downbeats=[0],
        onsets=[3],
        beat_energy=[0.2, 0.9],
        sections=[
            MusicSection(type="intro", start=0, end=3, energy=0.2),
            MusicSection(type="drop", start=3, end=8, energy=0.9),
            MusicSection(type="outro", start=8, end=10, energy=0.4),
        ],
        impact_points=[3, 4, 5],
        risers=[],
        breaks=[],
        silences=[],
        spectral_change_points=[3],
    )
    conn = connect(tmp_path / "v2.sqlite")
    path = tmp_path / "director_plan.yaml"
    plan = generate_director_plan(
        DirectorBrief(
            project_id="p",
            duration_sec=10,
            primary_characters=["tanjiro"],
            tone=["aggressive"],
        ),
        music,
        None,
        conn=conn,
        output_path=path,
    )
    assert [section.role for section in plan.structure] == [
        "opening", "impact", "ending"
    ]
    assert plan.structure[1].average_shot_length < plan.structure[0].average_shot_length
    assert plan.generation["llm_used"] is False
    assert path.is_file()
    assert conn.execute("SELECT count(*) FROM director_plans").fetchone()[0] == 1


def test_single_long_music_section_gets_complete_narrative_arc():
    music = MusicMap(
        duration_sec=180,
        bpm=123,
        beats=[0, 0.5],
        bars=[0],
        downbeats=[0],
        onsets=[6, 11],
        beat_energy=[0.6, 0.8],
        sections=[
            MusicSection(type="intro", start=0, end=84, energy=0.7),
            MusicSection(type="verse", start=84, end=180, energy=0.6),
        ],
        impact_points=[5.9, 10.8, 12.5],
        risers=[],
        breaks=[],
        silences=[],
        spectral_change_points=[],
    )
    plan = generate_director_plan(
        DirectorBrief(project_id="p", duration_sec=25),
        music,
        None,
    )
    roles = [section.role for section in plan.structure]
    assert roles == [
        "opening", "buildup", "pre_drop", "impact", "release", "ending"
    ]
    assert plan.structure[0].start == 0
    assert plan.structure[-1].end == 25
    assert all(
        left.end == right.start
        for left, right in zip(plan.structure, plan.structure[1:])
    )
    assert plan.generation["fallback_arc_used"] is True


def _basic_music() -> MusicMap:
    return MusicMap(
        duration_sec=10, bpm=120, beats=[0, 0.5], bars=[0], downbeats=[0],
        onsets=[3], beat_energy=[0.2, 0.9],
        sections=[
            MusicSection(type="intro", start=0, end=3, energy=0.2),
            MusicSection(type="drop", start=3, end=8, energy=0.9),
            MusicSection(type="outro", start=8, end=10, energy=0.4),
        ],
        impact_points=[3, 4, 5], risers=[], breaks=[], silences=[],
        spectral_change_points=[3],
    )


def test_strongly_beat_synced_reference_gets_the_beat_grid_even_without_vibe_tone():
    """House format: "everything after the hook is beat-locked cutting" only
    actually happens in _slots() when beat_grid_subdivision=="section_1_2_4".
    That was previously gated on tone=="vibe" alone, so a hype/villain-tone
    edit whose *reference* itself measured strong beat affinity (here: 0.65,
    above the 0.55 default) fell back to the looser "adaptive" pattern-cut
    path — which is what let the "5s后的重鼓" beat get skipped entirely in
    the akaza cut despite a real reference beat_sync_target of 0.65.
    """
    plan = generate_director_plan(
        DirectorBrief(
            project_id="p", duration_sec=10,
            primary_characters=["akaza"], tone=["menacing", "ferocious"],
        ),
        _basic_music(),
        _fingerprint(beat_sync_ratio=0.65),
    )
    assert plan.editing_style.beat_grid_subdivision == "section_1_2_4"


def test_weakly_beat_synced_reference_keeps_the_adaptive_pattern():
    plan = generate_director_plan(
        DirectorBrief(
            project_id="p", duration_sec=10,
            primary_characters=["akaza"], tone=["menacing", "ferocious"],
        ),
        _basic_music(),
        _fingerprint(beat_sync_ratio=0.5),
    )
    assert plan.editing_style.beat_grid_subdivision == "adaptive"


def test_vibe_tone_still_forces_the_beat_grid_regardless_of_measured_sync():
    plan = generate_director_plan(
        DirectorBrief(
            project_id="p", duration_sec=10,
            primary_characters=[], tone=["vibe"],
        ),
        _basic_music(),
        _fingerprint(beat_sync_ratio=0.3),
    )
    assert plan.editing_style.beat_grid_subdivision == "section_1_2_4"
