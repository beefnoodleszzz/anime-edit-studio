"""REFACTOR.md §22.4's core regression test: a 3-second shot with

    0.0-1.0  ordinary
    1.0-1.6  a genuine high-quality action burst
    1.6-3.0  ordinary

must produce an AMVSpec clip whose ``source.in_sec`` sits near the action
burst (~1.0), never at ``shot.start_sec`` (0.0) — the exact bug
amv_spec_builder.py used to have (REFACTOR.md §17)."""
from __future__ import annotations

import cv2
import numpy as np

from studio.core.database import connect
from studio.planning.amv_spec_builder import build_amv_spec
from studio.planning.global_sequence_planner import plan_sequence
from studio.planning.slots import TimelineSlot
from studio.spec.amv import Canvas, Timebase
from studio.spec.music_timeline import MusicTimeline

_SIZE = (320, 240)
_FPS = 20.0


def _ordinary_frame(rng, i):
    frame = np.clip(rng.normal(120, 15, (_SIZE[1], _SIZE[0], 3)), 0, 255).astype(np.uint8)
    cv2.circle(frame, (160, 120), 24, (150, 140, 90), -1)
    return frame


def _burst_frame(rng, i, offset):
    """A textured frame with an independently, rapidly moving subject —
    real residual motion after camera-motion compensation, not just noise."""
    frame = np.clip(rng.normal(120, 15, (_SIZE[1], _SIZE[0], 3)), 0, 255).astype(np.uint8)
    cx = 40 + (offset * 45) % 240
    cv2.circle(frame, (cx, 120), 30, (20, 20, 230), -1)
    return frame


def _write_three_second_shot(path):
    rng = np.random.default_rng(11)
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), _FPS, _SIZE)
    total_frames = int(3.0 * _FPS)
    for i in range(total_frames):
        sec = i / _FPS
        if 1.0 <= sec < 1.6:
            frame = _burst_frame(rng, i, offset=i - int(1.0 * _FPS))
        else:
            frame = _ordinary_frame(rng, i)
        writer.write(frame)
    writer.release()


def test_amv_spec_uses_the_action_window_not_the_shot_start(tmp_path):
    conn = connect(tmp_path / "engine.v2.sqlite")
    video = tmp_path / "shot.mp4"
    _write_three_second_shot(video)
    conn.execute(
        "INSERT INTO assets(id,path,sha256,width,height,fps_num,fps_den,duration_sec) "
        "VALUES ('a0',?,'sha0',320,240,20,1,3.0)",
        (str(video),),
    )
    conn.execute(
        "INSERT INTO shots(id,asset_id,idx,start_sec,end_sec,image_quality,visual_energy,shot_scale) "
        "VALUES ('s0','a0',0,0.0,3.0,0.8,0.6,0.5)"
    )
    conn.commit()

    slots = [TimelineSlot(index=0, start_sec=0.0, duration_sec=0.6, target_energy=0.8, slot_kind="action")]
    choices = plan_sequence(conn, slots, project_id="proj-1", asset_ids=["a0"])
    assert choices[0].shot_id == "s0"
    # Must not default to the shot's own start — the exact regression this
    # test guards against (REFACTOR.md §22.4).
    assert choices[0].source_in_sec > 0.5

    music = MusicTimeline(source_hash="m0", duration_sec=0.6, selected_tempo=120.0, tempo_confidence=0.8)
    spec = build_amv_spec(
        conn, project_id="proj-1", slots=slots, choices=choices,
        canvas=Canvas(width=1080, height=1350, aspect="4:5"), timebase=Timebase(num=20, den=1),
        music=music, music_path=tmp_path / "music.wav",
        demo_hash="d0", materials_index_hash="mi0", output_path=tmp_path / "preview.mov",
    )
    assert len(spec.clips) == 1
    clip = spec.clips[0]
    assert clip.source.in_sec > 0.5
    assert 0.0 <= clip.source.in_sec <= 3.0
    assert 0.0 <= clip.source.out_sec <= 3.0
