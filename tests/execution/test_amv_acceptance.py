"""AMV chain real-machine acceptance (REFACTOR.md §16.6).

Needs a running local DaVinci Resolve, skipped otherwise:
    .venv/bin/python -m pytest -m requires_resolve tests/execution/test_amv_acceptance.py -v

Builds a real two-clip AMVSpec (a carry TransitionPair spanning the cut, so
outgoing/incoming motion share one program) from real proxy assets already in
the v2 library, places it on an actual Resolve timeline, compiles Fusion,
renders a real window, and checks the rendered file with the same
`run_technical_qa` hard gates the release path uses.
"""
from __future__ import annotations

from fractions import Fraction
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from studio.core.database import DEFAULT_V2_DB, connect
from studio.critic.technical.qa import run_technical_qa
from studio.execution.resolve.amv_render import (
    build_amv_timeline,
    render_amv,
    verify_fusion_graph_consistency,
)
from studio.execution.resolve.fusion_program import comp_name_for
from studio.planning.motion_planner import build_clip_motion, build_transition_pair, direction_vector_for
from studio.planning.slots import TimelineSlot
from studio.spec.amv import (
    AMVSpec, Canvas, Clip, InputHashes, MusicRef, RenderSettings,
    SourceRange, Timebase, TimelinePlacement,
)

pytestmark = pytest.mark.requires_resolve

PROJECT_NAME = "_aes_amv_acceptance"
CANVAS = Canvas(width=1080, height=1350, aspect="4:5")
TIMEBASE = Timebase(num=24, den=1)
CLIP_DURATION = 2.0


@pytest.fixture(scope="module")
def asset_ids() -> list[str]:
    conn = connect(DEFAULT_V2_DB)
    rows = conn.execute(
        "SELECT id FROM assets WHERE duration_sec > 20 ORDER BY duration_sec DESC LIMIT 2"
    ).fetchall()
    conn.close()
    if len(rows) < 2:
        pytest.skip("需要至少 2 个时长 >20s 的已入库素材")
    return [row[0] for row in rows]


@pytest.fixture
def adapter():
    from studio.execution.resolve import ResolveAdapter, ResolveUnavailable

    try:
        return ResolveAdapter.open()
    except ResolveUnavailable as exc:
        pytest.skip(f"Resolve 不可用: {str(exc).splitlines()[0]}")


@pytest.fixture
def conn():
    connection = connect(DEFAULT_V2_DB)
    yield connection
    connection.close()


@pytest.fixture(scope="module")
def music_path(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("amv_acceptance") / "music.wav"
    sample_rate = 48000
    duration = 4
    audio = np.zeros(sample_rate * duration, np.float32)
    sf.write(path, audio, sample_rate)
    return path


def make_spec(asset_ids: list[str], music_path: Path, output_path: Path) -> AMVSpec:
    """A hard cut at 2.0s with a real carry TransitionPair spanning it —
    outgoing accelerates left, incoming continues left and settles (§9)."""
    a, b = asset_ids
    slot0 = TimelineSlot(index=0, start_sec=0.0, duration_sec=CLIP_DURATION, target_energy=0.7, hold=False)
    slot1 = TimelineSlot(
        index=1, start_sec=CLIP_DURATION, duration_sec=CLIP_DURATION, target_energy=0.7,
        hold=False, entry_motion="carry",
    )
    motion0 = build_clip_motion(slot0, CANVAS)
    motion1 = build_clip_motion(slot1, CANVAS, direction=direction_vector_for("carry"))

    clip0 = Clip(
        id="c0", asset_id=a,
        source=SourceRange(in_sec=10.0, out_sec=10.0 + CLIP_DURATION),
        timeline=TimelinePlacement(in_sec=0.0, duration_sec=CLIP_DURATION),
        motion=motion0,
    )
    clip1 = Clip(
        id="c1", asset_id=b,
        source=SourceRange(in_sec=10.0, out_sec=10.0 + CLIP_DURATION),
        timeline=TimelinePlacement(in_sec=CLIP_DURATION, duration_sec=CLIP_DURATION),
        motion=motion1,
    )
    pair = build_transition_pair(
        pair_id="t0", cut_sec=CLIP_DURATION, outgoing_clip_id="c0", incoming_clip_id="c1",
        entry_motion="carry", canvas=CANVAS, confidence=0.8,
    )
    return AMVSpec(
        id=PROJECT_NAME,
        input_hashes=InputHashes(demo="acceptance", materials_index="acceptance"),
        timebase=TIMEBASE, canvas=CANVAS, duration_sec=2 * CLIP_DURATION,
        music=MusicRef(path=str(music_path), timeline_hash="acceptance"),
        clips=[clip0, clip1], transition_pairs=[pair],
        render=RenderSettings(output_path=str(output_path)),
    )


class TestAMVTimelinePlacement:
    def test_build_places_both_clips_and_the_music_track(self, adapter, conn, asset_ids, music_path, tmp_path):
        spec = make_spec(asset_ids, music_path, tmp_path / "preview.mov")
        items = build_amv_timeline(adapter, spec, conn, project_name=PROJECT_NAME, reset=True)

        assert set(items) == {"c0", "c1"}
        assert len(adapter.timeline_items(1)) == 2
        assert len(adapter.audio_items(1)) == 1

    def test_clips_land_back_to_back_with_no_gap(self, adapter, conn, asset_ids, music_path, tmp_path):
        spec = make_spec(asset_ids, music_path, tmp_path / "preview.mov")
        build_amv_timeline(adapter, spec, conn, project_name=PROJECT_NAME, reset=True)
        items = sorted(adapter.timeline_items(1), key=lambda item: item.GetStart())

        assert items[1].GetStart() == items[0].GetEnd()
        for item in items:
            assert item.GetDuration() == round(CLIP_DURATION * TIMEBASE.fps)

    def test_rebuild_is_idempotent(self, adapter, conn, asset_ids, music_path, tmp_path):
        spec = make_spec(asset_ids, music_path, tmp_path / "preview.mov")
        build_amv_timeline(adapter, spec, conn, project_name=PROJECT_NAME, reset=True)
        first = [(i.GetStart(), i.GetDuration()) for i in adapter.timeline_items(1)]

        build_amv_timeline(adapter, spec, conn, project_name=PROJECT_NAME, reset=True)
        second = [(i.GetStart(), i.GetDuration()) for i in adapter.timeline_items(1)]

        assert first == second
        assert len(second) == 2


class TestFusionCompilation:
    def test_each_clip_gets_its_own_correctly_named_comp_with_the_fixed_chain(
        self, adapter, conn, asset_ids, music_path, tmp_path,
    ):
        from studio.execution.amv_compiler import compile_amv_spec

        spec = make_spec(asset_ids, music_path, tmp_path / "preview.mov")
        items = build_amv_timeline(adapter, spec, conn, project_name=PROJECT_NAME, reset=True)

        programs = compile_amv_spec(adapter, spec, items)

        assert verify_fusion_graph_consistency(programs)
        for clip_id, program in programs.items():
            assert program.comp_name == comp_name_for(clip_id)
            assert program.comp is not None
        # The carry pair spans c0 (outgoing) and c1 (incoming): both must have
        # compiled — motion is not skipped on either side of the cut (§9).
        assert {"c0", "c1"} == set(programs)


class TestRenderedOutput:
    def test_rendered_window_passes_technical_hard_gates(self, adapter, conn, asset_ids, music_path, tmp_path):
        output_dir = tmp_path / "renders"
        spec = make_spec(asset_ids, music_path, output_dir / "preview.mov")
        items = build_amv_timeline(adapter, spec, conn, project_name=PROJECT_NAME, reset=True)

        result, consistent = render_amv(
            adapter, spec, items, output_dir=output_dir, name="amv-acceptance-window",
        )
        assert consistent, "Fusion graph readback was inconsistent after a real render"
        assert result.output.is_file()

        qa = run_technical_qa(
            result.output,
            expected_duration=spec.duration_sec,
            expected_width=spec.canvas.width,
            expected_height=spec.canvas.height,
            expected_fps=Fraction(spec.timebase.num, spec.timebase.den),
        )
        # The picture-side hard gates (§12.1) are what this acceptance test is
        # for — placement, frame count, black/freeze frames, corruption. The
        # fixture's music track is a silent placeholder (see `music_path`
        # above), so the loudness/silence gates are expected to fail here and
        # are exercised for real elsewhere against actual delivery audio.
        picture_checks = [c for c in qa.checks if c.name not in {"loudness", "unexpected_silence"}]
        assert all(c.passed for c in picture_checks), [c for c in picture_checks if not c.passed]


class TestSelectionSourceWindows:
    """REFACTOR.md §22.8: the new ShotWindow selector, not a hand-built
    SourceRange, drives a real render — verifying the §22.4 regression
    (source.in_sec must not default to shot.start_sec) against actually
    indexed footage rather than a synthetic clip."""

    def test_planner_uses_precise_windows_and_renders(self, adapter, conn, asset_ids, music_path, tmp_path):
        from studio.planning.amv_spec_builder import build_amv_spec
        from studio.planning.global_sequence_planner import plan_sequence
        from studio.spec.music_timeline import MusicTimeline

        slots = [
            TimelineSlot(index=0, start_sec=0.0, duration_sec=CLIP_DURATION, target_energy=0.7),
            TimelineSlot(
                index=1, start_sec=CLIP_DURATION, duration_sec=CLIP_DURATION,
                target_energy=0.7, entry_motion="carry",
            ),
        ]
        choices = plan_sequence(conn, slots, project_id=PROJECT_NAME, asset_ids=asset_ids)
        if not all(choice.shot_id for choice in choices):
            pytest.skip("材料库中没有能通过技术门禁的候选窗口")

        music = MusicTimeline(
            source_hash="acceptance", duration_sec=2 * CLIP_DURATION,
            selected_tempo=120.0, tempo_confidence=0.8,
        )
        spec = build_amv_spec(
            conn, project_id=PROJECT_NAME, slots=slots, choices=choices,
            canvas=CANVAS, timebase=TIMEBASE, music=music, music_path=music_path,
            demo_hash="acceptance", materials_index_hash="acceptance",
            output_path=tmp_path / "preview.mov",
        )

        shot_ids = [choice.shot_id for choice in choices if choice.shot_id]
        placeholders = ",".join("?" for _ in shot_ids)
        shot_starts = {
            row["id"]: row["start_sec"]
            for row in conn.execute(
                f"SELECT id,start_sec FROM shots WHERE id IN ({placeholders})", shot_ids
            )
        }
        assert any(
            abs(clip.source.in_sec - shot_starts.get(clip.shot_id, clip.source.in_sec)) > 0.05
            for clip in spec.clips
        ), "no clip's source range differs from its shot's own start — the exact §17 regression"

        items = build_amv_timeline(adapter, spec, conn, project_name=PROJECT_NAME, reset=True)
        result, consistent = render_amv(
            adapter, spec, items, output_dir=tmp_path / "renders", name="amv-selection-acceptance",
        )
        assert consistent, "Fusion graph readback was inconsistent after a real render"
        assert result.output.is_file()
