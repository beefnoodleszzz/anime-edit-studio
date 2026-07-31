from __future__ import annotations

import cv2
import numpy as np

from studio.core.database import connect
from studio.planning.amv_spec_builder import build_amv_spec
from studio.planning.global_sequence_planner import plan_sequence
from studio.planning.slots import TimelineSlot
from studio.spec.amv import AMVSpec, Canvas, Timebase
from studio.spec.music_timeline import MusicTimeline

_SIZE = (320, 240)


def _write_asset_video(path, *, total_duration_sec, fps=12.0):
    """A textured, moderately bright synthetic video long enough to back
    every seeded shot — real media so ShotWindow generation (technical gate,
    portrait/action analysis) actually runs, not metadata-only columns."""
    rng = np.random.default_rng(3)
    frame_count = int(total_duration_sec * fps) + 2
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, _SIZE)
    for i in range(frame_count):
        frame = np.clip(rng.normal(130, 20, (_SIZE[1], _SIZE[0], 3)), 0, 255).astype(np.uint8)
        cv2.circle(frame, (60 + (i * 3) % 200, 120), 26, (200, 180, 90), -1)
        writer.write(frame)
    writer.release()


def _seed_shots(conn, *, tmp_path, count=6, duration=2.0):
    video = tmp_path / "a0.mp4"
    _write_asset_video(video, total_duration_sec=count * duration)
    conn.execute(
        "INSERT INTO assets(id,path,sha256,width,height,fps_num,fps_den,duration_sec) "
        "VALUES ('a0',?,'sha0',320,240,12,1,?)",
        (str(video), count * duration),
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
    _seed_shots(conn, tmp_path=tmp_path, count=8)
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
    _seed_shots(conn, tmp_path=tmp_path, count=8)
    slots = [
        TimelineSlot(index=i, start_sec=i * 2.0, duration_sec=2.0, target_energy=0.5, entry_motion="carry")
        for i in range(4)
    ]
    first = plan_sequence(conn, slots, project_id="proj-1", asset_ids=["a0"])
    second = plan_sequence(conn, slots, project_id="proj-1", asset_ids=["a0"])
    assert [c.shot_id for c in first] == [c.shot_id for c in second]
    assert [c.source_in_sec for c in first] == [c.source_in_sec for c in second]


def test_plan_sequence_reports_empty_slot_when_no_candidate_clears_hard_gates(tmp_path):
    conn = connect(tmp_path / "engine.v2.sqlite")
    _seed_shots(conn, tmp_path=tmp_path, count=2, duration=2.0)
    slots = [TimelineSlot(index=0, start_sec=0.0, duration_sec=2.0, target_energy=0.5)]
    choices = plan_sequence(conn, slots, project_id="proj-1", asset_ids=["does-not-exist"])
    assert len(choices) == 1
    assert choices[0].shot_id == ""


def test_plan_sequence_choices_stay_inside_their_own_shot(tmp_path):
    conn = connect(tmp_path / "engine.v2.sqlite")
    _seed_shots(conn, tmp_path=tmp_path, count=4, duration=2.0)
    slots = [
        TimelineSlot(index=i, start_sec=i * 2.0, duration_sec=2.0, target_energy=0.5)
        for i in range(3)
    ]
    choices = plan_sequence(conn, slots, project_id="proj-1", asset_ids=["a0"])
    rows = {
        row["id"]: (row["start_sec"], row["end_sec"])
        for row in conn.execute("SELECT id,start_sec,end_sec FROM shots")
    }
    for choice in choices:
        assert choice.shot_id
        shot_start, shot_end = rows[choice.shot_id]
        assert shot_start - 1e-6 <= choice.source_in_sec < choice.source_out_sec <= shot_end + 1e-6


def test_build_amv_spec_end_to_end_from_planner_output(tmp_path):
    conn = connect(tmp_path / "engine.v2.sqlite")
    _seed_shots(conn, tmp_path=tmp_path, count=4, duration=2.0)
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
