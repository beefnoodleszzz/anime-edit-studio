from studio.core.database import connect
from studio.creative.director.plan import (
    DirectorPlan,
    DirectorSection,
    ImpactBudget,
)
from studio.creative.reference import EditingStyleProfile
from studio.editing.music.map import MusicMap, MusicSection
from studio.editing.ranking import RankedCandidate
from studio.editing.sequence import (
    plan_sequence,
    planned_rhythm_metrics,
    role_source_duration_requirements,
)


def test_sequence_planner_uses_beam_and_emits_versioned_spec(tmp_path):
    conn = connect(tmp_path / "v2.sqlite")
    with conn:
        conn.execute(
            "INSERT INTO assets(id,path,sha256,fps_num,fps_den,duration_sec) "
            "VALUES ('a','x','hash',24,1,20)"
        )
        conn.executemany(
            """
            INSERT INTO shots(
              id,asset_id,idx,start_sec,end_sec,character,motion_dir,
              shot_scale,visual_energy
            ) VALUES (?,?,?,?,?,?,?,?,?)
            """,
            [
                (
                    f"s{i}", "a", i, i * 3, i * 3 + 2.5,
                    "tanjiro", "right" if i % 2 else "left",
                    0.2 + i * 0.1, 0.3 + i * 0.1,
                )
                for i in range(5)
            ],
        )
    plan = DirectorPlan(
        project_id="p",
        revision=1,
        duration_sec=3,
        primary_characters=["tanjiro"],
        tone=["cinematic"],
        structure=[
            DirectorSection(
                role="buildup", start=0, end=3, energy=0.6,
                average_shot_length=1,
            )
        ],
        visual_rules={"prefer": [], "avoid": []},
        sound_strategy="test",
        impact_budget=ImpactBudget(sfx_max=1, flash_max=1, shake_max=1),
        generation={"llm_used": False},
    )
    music = MusicMap(
        duration_sec=3,
        bpm=120,
        beats=[0, 0.5, 1, 1.5, 2, 2.5],
        bars=[0],
        downbeats=[0],
        onsets=[],
        beat_energy=[],
        sections=[MusicSection(type="build", start=0, end=3, energy=0.6)],
        impact_points=[],
        risers=[],
        breaks=[],
        silences=[],
        spectral_change_points=[],
    )
    candidates = [
        RankedCandidate(
            shot_id=f"s{i}",
            intrinsic=0.7,
            contextual=0.8 - i * 0.02,
            total=0.75 - i * 0.01,
            intrinsic_components={},
            contextual_components={},
        )
        for i in range(5)
    ]
    spec = plan_sequence(
        conn, plan=plan, music=music, candidates_by_role={"build": candidates}
    )
    assert len(spec.clips) == 3
    assert len({clip.shot_id for clip in spec.clips}) == 3
    assert spec.duration_sec == 3
    assert all(clip.role == "build" for clip in spec.clips)
    assert all(clip.decision.alternatives for clip in spec.clips)
    assert conn.execute("SELECT count(*) FROM edit_specs").fetchone()[0] == 1
    requirements = role_source_duration_requirements(plan, music)
    assert requirements["build"] > 1


def test_forced_role_shot_is_not_consumed_by_an_earlier_role(tmp_path):
    conn = connect(tmp_path / "v2.sqlite")
    with conn:
        conn.execute(
            "INSERT INTO assets(id,path,sha256,fps_num,fps_den,duration_sec) "
            "VALUES ('a','x','hash',24,1,20)"
        )
        conn.executemany(
            "INSERT INTO shots(id,asset_id,idx,start_sec,end_sec,character,"
            "motion_dir,shot_scale,visual_energy) VALUES (?,?,?,?,?,?,?,?,?)",
            [
                (f"s{i}", "a", i, i * 3, i * 3 + 2.5, "tanjiro", "right", .5, .5)
                for i in range(3)
            ],
        )
    plan = DirectorPlan(
        project_id="reserved",
        revision=1,
        duration_sec=2,
        primary_characters=["tanjiro"],
        tone=["clean"],
        structure=[
            DirectorSection(role="opening", start=0, end=1, energy=.5, average_shot_length=1),
            DirectorSection(role="ending", start=1, end=2, energy=.5, average_shot_length=1),
        ],
        visual_rules={"prefer": [], "avoid": []},
        sound_strategy="test",
        impact_budget=ImpactBudget(sfx_max=1, flash_max=1, shake_max=1),
        generation={"llm_used": False},
    )
    music = MusicMap(
        duration_sec=2, bpm=120, beats=[0, .5, 1, 1.5], bars=[0],
        downbeats=[0], onsets=[], beat_energy=[],
        sections=[MusicSection(type="build", start=0, end=2, energy=.5)],
        impact_points=[], risers=[], breaks=[], silences=[],
        spectral_change_points=[],
    )
    ranked = [
        RankedCandidate(
            shot_id=f"s{i}", intrinsic=.8, contextual=.8,
            total=.9 - i * .1, intrinsic_components={}, contextual_components={},
        )
        for i in range(3)
    ]
    spec = plan_sequence(
        conn,
        plan=plan,
        music=music,
        candidates_by_role={"opening": ranked, "ending": ranked},
        selected_by_role={"ending": "s0"},
    )
    assert spec.clips[0].shot_id != "s0"
    assert spec.clips[1].shot_id == "s0"


def test_source_window_is_centered_on_scored_keyframe(tmp_path):
    conn = connect(tmp_path / "v2.sqlite")
    with conn:
        conn.execute(
            "INSERT INTO assets(id,path,sha256,fps_num,fps_den,duration_sec) "
            "VALUES ('a','x','hash',24,1,20)"
        )
        conn.execute(
            "INSERT INTO shots(id,asset_id,idx,start_sec,end_sec,keyframe,"
            "character,motion_dir,shot_scale,visual_energy) "
            "VALUES ('s','a',0,10,20,'shot_0000_c4.jpg','tanjiro','right',.5,.5)"
        )
    plan = DirectorPlan(
        project_id="window", revision=1, duration_sec=2,
        primary_characters=["tanjiro"], tone=["clean"],
        structure=[
            DirectorSection(role="opening", start=0, end=2, energy=.5, average_shot_length=2)
        ],
        visual_rules={"prefer": [], "avoid": []}, sound_strategy="test",
        impact_budget=ImpactBudget(sfx_max=1, flash_max=1, shake_max=1),
        generation={"llm_used": False},
    )
    music = MusicMap(
        duration_sec=2, bpm=120, beats=[0, .5, 1, 1.5], bars=[0],
        downbeats=[0], onsets=[], beat_energy=[],
        sections=[MusicSection(type="build", start=0, end=2, energy=.5)],
        impact_points=[], risers=[], breaks=[], silences=[],
        spectral_change_points=[],
    )
    candidate = RankedCandidate(
        shot_id="s", intrinsic=.8, contextual=.8, total=.8,
        intrinsic_components={}, contextual_components={},
    )
    spec = plan_sequence(
        conn, plan=plan, music=music, candidates_by_role={"opening": [candidate]}
    )
    assert spec.clips[0].source.in_sec == 17.5
    assert spec.clips[0].source.out_sec == 19.5


def test_editing_styles_create_distinct_reusable_rhythm_grammars():
    music = MusicMap(
        duration_sec=10, bpm=120,
        beats=[value / 2 for value in range(20)], bars=[0],
        downbeats=[0], onsets=[], beat_energy=[],
        sections=[MusicSection(type="build", start=0, end=10, energy=.5)],
        impact_points=[2, 6], risers=[], breaks=[], silences=[],
        spectral_change_points=[],
    )
    base = dict(
        project_id="styles", revision=1, duration_sec=10,
        primary_characters=[], tone=[],
        structure=[
            DirectorSection(
                role="buildup", start=0, end=10, energy=.5,
                average_shot_length=1,
            )
        ],
        visual_rules={"prefer": [], "avoid": []}, sound_strategy="test",
        impact_budget=ImpactBudget(sfx_max=1, flash_max=1, shake_max=1),
        generation={"llm_used": False},
    )
    calm = DirectorPlan(
        **base,
        editing_style=EditingStyleProfile(
            id="calm", name="Calm", source="curated",
            target_cut_density=.5, median_shot_length=2,
            min_shot_length=1, max_shot_length=3,
            duration_pattern=[1, 1], beat_sync_target=.2,
        ),
    )
    punchy = DirectorPlan(
        **base,
        editing_style=EditingStyleProfile(
            id="punchy", name="Punchy", source="curated",
            target_cut_density=2, median_shot_length=.5,
            min_shot_length=.25, max_shot_length=1,
            duration_pattern=[1, .5, 1.5, .5], beat_sync_target=.7,
        ),
    )
    calm_metrics = planned_rhythm_metrics(calm, music)
    punchy_metrics = planned_rhythm_metrics(punchy, music)
    assert punchy_metrics["shot_count"] > calm_metrics["shot_count"]
    assert punchy_metrics["median_shot_length"] < calm_metrics["median_shot_length"]
    assert punchy_metrics["beat_sync_ratio"] >= .6
