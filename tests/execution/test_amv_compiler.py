from __future__ import annotations

import cv2
import numpy as np

from studio.core.database import connect
from studio.execution.amv_compiler import compile_amv_spec
from studio.execution.resolve.fusion_program import DIRECTIONAL_BLUR_NAME, comp_name_for
from studio.planning.amv_spec_builder import build_amv_spec
from studio.planning.global_sequence_planner import plan_sequence
from studio.planning.slots import TimelineSlot
from studio.spec.amv import Canvas, Timebase
from studio.spec.music_timeline import MusicTimeline
from tests.execution.test_fusion_program import _Item

_SIZE = (320, 240)


def _write_asset_video(path, *, total_duration_sec, fps=12.0):
    rng = np.random.default_rng(5)
    frame_count = int(total_duration_sec * fps) + 2
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, _SIZE)
    for i in range(frame_count):
        frame = np.clip(rng.normal(130, 20, (_SIZE[1], _SIZE[0], 3)), 0, 255).astype(np.uint8)
        cv2.circle(frame, (60 + (i * 3) % 200, 120), 26, (200, 180, 90), -1)
        writer.write(frame)
    writer.release()


def _seed_shots(conn, *, tmp_path, count=4, duration=2.0):
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
            (f"s{i}", "a0", i, i * duration, (i + 1) * duration, 0.8, 0.7, 0.5, 0.5, 0.7),
        )
    conn.commit()


def test_compile_amv_spec_builds_one_owned_comp_per_clip_with_transitions(tmp_path):
    conn = connect(tmp_path / "engine.v2.sqlite")
    _seed_shots(conn, tmp_path=tmp_path, count=3)
    slots = [
        TimelineSlot(index=i, start_sec=i * 2.0, duration_sec=2.0, target_energy=0.5,
                      entry_motion="carry" if i > 0 else "none")
        for i in range(3)
    ]
    choices = plan_sequence(conn, slots, project_id="proj-1", asset_ids=["a0"])
    music = MusicTimeline(source_hash="m0", duration_sec=6.0, selected_tempo=120.0, tempo_confidence=0.8)
    spec = build_amv_spec(
        conn, project_id="proj-1", slots=slots, choices=choices,
        canvas=Canvas(width=1080, height=1350, aspect="4:5"), timebase=Timebase(num=24000, den=1001),
        music=music, music_path=tmp_path / "music.wav",
        demo_hash="d0", materials_index_hash="mi0", output_path=tmp_path / "preview.mov",
    )

    items = {clip.id: _Item() for clip in spec.clips}
    programs = compile_amv_spec(object(), spec, items)

    assert set(programs) == {clip.id for clip in spec.clips}
    for clip in spec.clips:
        item = items[clip.id]
        assert item.GetFusionCompNameList() == [comp_name_for(clip.id)]
    # Both sides of a carry cut share directional blur; clip 0 has an
    # outgoing pair (it precedes the first cut) and clip 2 an incoming one.
    assert DIRECTIONAL_BLUR_NAME in programs[spec.clips[0].id].node_names
    assert DIRECTIONAL_BLUR_NAME in programs[spec.clips[-1].id].node_names
