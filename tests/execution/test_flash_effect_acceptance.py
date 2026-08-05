"""Real-machine acceptance for the "flash" TransitionPair effect_kind.

Needs a running local DaVinci Resolve, skipped otherwise:
    .venv/bin/python -m pytest -m requires_resolve tests/execution/test_flash_effect_acceptance.py -v

Self-contained (seeds its own tiny synthetic asset into a temp DB) rather
than depending on real production assets already being indexed, unlike
tests/execution/test_amv_acceptance.py.
"""
from __future__ import annotations

import cv2
import numpy as np
import pytest
import soundfile as sf

from studio.core.database import connect
from studio.execution.amv_compiler import compile_amv_spec
from studio.execution.resolve.amv_render import build_amv_timeline
from studio.execution.resolve.fusion_program import comp_name_for
from studio.planning.motion_planner import build_clip_motion
from studio.planning.slots import TimelineSlot
from studio.spec.amv import (
    AMVSpec, Canvas, Clip, InputHashes, MusicRef, RenderSettings,
    SourceRange, Timebase, TimelinePlacement, TransitionPair,
)

pytestmark = pytest.mark.requires_resolve

PROJECT_NAME = "_aes_flash_effect_acceptance"
CANVAS = Canvas(width=640, height=360, aspect="16:9")
TIMEBASE = Timebase(num=24, den=1)
CLIP_DURATION = 2.0


@pytest.fixture
def adapter():
    from studio.execution.resolve import ResolveAdapter, ResolveUnavailable

    try:
        return ResolveAdapter.open()
    except ResolveUnavailable as exc:
        pytest.skip(f"Resolve 不可用: {str(exc).splitlines()[0]}")


@pytest.fixture
def asset_id(tmp_path) -> str:
    video = tmp_path / "asset.mp4"
    writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"mp4v"), 24.0, (640, 360))
    rng = np.random.default_rng(1)
    for _ in range(24 * 6):
        frame = np.clip(rng.normal(120, 20, (360, 640, 3)), 0, 255).astype(np.uint8)
        writer.write(frame)
    writer.release()

    conn = connect(tmp_path / "engine.sqlite")
    with conn:
        conn.execute(
            "INSERT INTO assets(id,path,sha256,width,height,fps_num,fps_den,duration_sec) "
            "VALUES ('flash-a0',?, 'sha0', 640, 360, 24, 1, 6.0)",
            (str(video),),
        )
    conn.close()
    return "flash-a0"


@pytest.fixture
def conn(tmp_path):
    connection = connect(tmp_path / "engine.sqlite")
    yield connection
    connection.close()


def make_flash_spec(asset_id: str, output_path, music_path) -> AMVSpec:
    slot0 = TimelineSlot(index=0, start_sec=0.0, duration_sec=CLIP_DURATION, target_energy=0.7, hold=True)
    slot1 = TimelineSlot(index=1, start_sec=CLIP_DURATION, duration_sec=CLIP_DURATION, target_energy=0.7, hold=True)
    clip0 = Clip(
        id="c0", asset_id=asset_id,
        source=SourceRange(in_sec=0.0, out_sec=CLIP_DURATION),
        timeline=TimelinePlacement(in_sec=0.0, duration_sec=CLIP_DURATION),
        motion=build_clip_motion(slot0, CANVAS),
    )
    clip1 = Clip(
        id="c1", asset_id=asset_id,
        source=SourceRange(in_sec=2.0, out_sec=2.0 + CLIP_DURATION),
        timeline=TimelinePlacement(in_sec=CLIP_DURATION, duration_sec=CLIP_DURATION),
        motion=build_clip_motion(slot1, CANVAS),
    )
    pair = TransitionPair(
        id="t0", cut_sec=CLIP_DURATION, outgoing_clip_id="c0", incoming_clip_id="c1",
        direction="none", safe_scale=1.0, confidence=0.8, effect_kind="flash",
    )
    return AMVSpec(
        id=PROJECT_NAME,
        input_hashes=InputHashes(demo="flash-acceptance", materials_index="flash-acceptance"),
        timebase=TIMEBASE, canvas=CANVAS, duration_sec=2 * CLIP_DURATION,
        music=MusicRef(path=str(music_path), timeline_hash="flash-acceptance"),
        clips=[clip0, clip1], transition_pairs=[pair],
        render=RenderSettings(output_path=str(output_path)),
    )


def test_flash_effect_produces_a_real_gain_spike_on_both_sides_of_the_cut(
    adapter, conn, asset_id, tmp_path,
):
    music_path = tmp_path / "music.wav"
    sf.write(music_path, np.zeros(48000 * 4, np.float32), 48000)
    spec = make_flash_spec(asset_id, tmp_path / "preview.mov", music_path)
    items = build_amv_timeline(adapter, spec, conn, project_name=PROJECT_NAME, reset=True)
    programs = compile_amv_spec(adapter, spec, items)

    assert {"c0", "c1"} == set(programs)
    for clip_id in ("c0", "c1"):
        comp = programs[clip_id].comp
        assert comp.GetToolList(False)
        tools = comp.GetToolList(False) or {}
        color = next(
            t for t in tools.values() if (t.GetAttrs() or {}).get("TOOLS_Name") == "PostColor"
        )
        gain_spline = next(
            t for t in tools.values() if (t.GetAttrs() or {}).get("TOOLS_Name") == "PostColorGain"
        )
        keyframes = gain_spline.GetKeyFrames() or {}
        assert keyframes
        peak_frame = max(keyframes, key=lambda frame: keyframes[frame][1])
        assert keyframes[peak_frame][1] > 1.0
        # Regression for the codex review finding: the comp's end frame is
        # exclusive, so a peak keyframe placed exactly at the clip's own
        # duration is never actually rendered. c0 is the outgoing clip here
        # (its own duration is CLIP_DURATION); its peak must land strictly
        # inside the rendered frame range.
        if clip_id == "c0":
            duration_frames = round(CLIP_DURATION * TIMEBASE.fps)
            assert peak_frame < duration_frames
        # Read the actual connected input at the cut frame — not just that a
        # spline with the right numbers exists somewhere unconnected.
        assert color.GetInput("Gain", float(peak_frame)) == pytest.approx(keyframes[peak_frame][1])
