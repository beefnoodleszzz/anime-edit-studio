from studio.creative.director import DirectorBrief, generate_director_plan
from studio.editing.music.map import MusicMap, MusicSection
from studio.core.database import connect


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
