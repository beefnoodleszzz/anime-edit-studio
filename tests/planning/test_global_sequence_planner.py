from __future__ import annotations

from studio.core.database import connect
from studio.planning.amv_spec_builder import build_amv_spec
from studio.planning.global_sequence_planner import plan_sequence
from studio.planning.slots import TimelineSlot
from studio.spec.amv import AMVSpec, Canvas, Timebase
from studio.spec.music_timeline import MusicTimeline


def _seed_shots(conn, *, count=6, duration=2.0):
    conn.execute(
        "INSERT INTO assets(id,path,sha256,width,height,fps_num,fps_den,duration_sec) "
        "VALUES ('a0','/m/a0.mp4','sha0',1920,1080,24000,1001,60.0)"
    )
    for i in range(count):
        conn.execute(
            """
            INSERT INTO shots(
              id,asset_id,idx,start_sec,end_sec,image_quality,face_visibility,
              visual_energy,shot_scale,cutability
            ) VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            (
                f"s{i}", "a0", i, i * duration, (i + 1) * duration,
                0.8, 0.7, 0.4 + (i % 3) * 0.1, 0.5, 0.7,
            ),
        )
    conn.commit()


def test_plan_sequence_fills_every_slot_and_avoids_immediate_reuse(tmp_path):
    conn = connect(tmp_path / "engine.v2.sqlite")
    _seed_shots(conn, count=8)
    slots = [
        TimelineSlot(index=i, start_sec=i * 2.0, duration_sec=2.0, target_energy=0.5, entry_motion="carry")
        for i in range(5)
    ]
    choices = plan_sequence(conn, slots, project_id="proj-1", asset_ids=["a0"])
    assert len(choices) == len(slots)
    assert all(c.shot_id for c in choices)
    shot_ids = [c.shot_id for c in choices]
    for a, b in zip(shot_ids, shot_ids[1:]):
        assert a != b


def test_plan_sequence_is_deterministic(tmp_path):
    conn = connect(tmp_path / "engine.v2.sqlite")
    _seed_shots(conn, count=8)
    slots = [
        TimelineSlot(index=i, start_sec=i * 2.0, duration_sec=2.0, target_energy=0.5, entry_motion="carry")
        for i in range(4)
    ]
    first = plan_sequence(conn, slots, project_id="proj-1", asset_ids=["a0"])
    second = plan_sequence(conn, slots, project_id="proj-1", asset_ids=["a0"])
    assert [c.shot_id for c in first] == [c.shot_id for c in second]


def test_plan_sequence_reports_empty_slot_when_no_candidate_clears_hard_gates(tmp_path):
    conn = connect(tmp_path / "engine.v2.sqlite")
    _seed_shots(conn, count=2, duration=2.0)
    slots = [TimelineSlot(index=0, start_sec=0.0, duration_sec=2.0, target_energy=0.5)]
    choices = plan_sequence(conn, slots, project_id="proj-1", asset_ids=["does-not-exist"])
    assert len(choices) == 1
    assert choices[0].shot_id == ""


def test_build_amv_spec_end_to_end_from_planner_output(tmp_path):
    conn = connect(tmp_path / "engine.v2.sqlite")
    _seed_shots(conn, count=4, duration=2.0)
    slots = [
        TimelineSlot(index=i, start_sec=i * 2.0, duration_sec=2.0, target_energy=0.5,
                      entry_motion="carry" if i > 0 else "none")
        for i in range(3)
    ]
    choices = plan_sequence(conn, slots, project_id="proj-1", asset_ids=["a0"])

    music = MusicTimeline(
        source_hash="music-hash", duration_sec=6.0, selected_tempo=120.0, tempo_confidence=0.8,
    )
    spec = build_amv_spec(
        conn, project_id="proj-1", slots=slots, choices=choices,
        canvas=Canvas(width=1080, height=1350, aspect="4:5"),
        timebase=Timebase(num=24000, den=1001),
        music=music, music_path=tmp_path / "music.wav",
        demo_hash="demo-hash", materials_index_hash="materials-hash",
        output_path=tmp_path / "preview.mov",
    )

    assert isinstance(spec, AMVSpec)
    assert len(spec.clips) == 3
    assert len(spec.transition_pairs) == 2
    restored = AMVSpec.model_validate_json(spec.model_dump_json())
    assert restored == spec
