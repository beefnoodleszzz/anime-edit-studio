from __future__ import annotations

import pytest

from studio.editspec.diff import (
    DiffError,
    EditSpecDiff,
    PatchClip,
    ShiftAfter,
    apply_diff,
    diff_specs,
)
from studio.editspec.schema import (
    AudioLayer,
    Canvas,
    Clip,
    Decision,
    EditSpec,
    MotionBeat,
    MotionPhrase,
    SourceRange,
    Timebase,
    TimelinePlacement,
)


def clip(cid: str, at: float, *, locked: bool = False) -> Clip:
    return Clip(
        id=cid,
        asset_id="asset",
        source=SourceRange(in_sec=at, out_sec=at + 1),
        timeline=TimelinePlacement(in_sec=at, duration_sec=1),
        decision=Decision(locked=locked),
    )


def spec(*clips: Clip, revision: int = 1) -> EditSpec:
    return EditSpec(
        id="project",
        revision=revision,
        timebase=Timebase(num=24),
        canvas=Canvas(width=1080, height=1350),
        clips=list(clips),
    )


def test_generated_diff_round_trips():
    before = spec(clip("a", 0), clip("b", 1))
    after = spec(clip("a", 0), clip("b", 2), clip("c", 3), revision=2)
    patch = diff_specs(before, after)
    assert apply_diff(before, patch) == after


def test_generated_diff_includes_top_level_audio():
    before = spec(clip("a", 0))
    after = before.model_copy(deep=True)
    after.revision = 2
    after.audio.append(
        AudioLayer(id="music", path="/tmp/music.wav", duration_sec=1, gain_db=-7)
    )
    patch = diff_specs(before, after)
    assert [op.op for op in patch.ops] == ["patch_spec"]
    assert apply_diff(before, patch) == after


def test_generated_diff_includes_top_level_motion_phrases():
    before = spec(clip("a", 0), clip("b", 1))
    after = before.model_copy(deep=True)
    after.revision = 2
    after.motion_phrases.append(
        MotionPhrase(
            id="phrase-1",
            beats=[
                MotionBeat(clip_id="a", stage="accelerate", intensity=0.4),
                MotionBeat(clip_id="b", stage="settle", intensity=0.3),
            ],
            direction="left",
            translation=0.1,
            scale_delta=0.05,
            blur_strength=0.12,
            cut_window_sec=0.08,
        )
    )
    patch = diff_specs(before, after)
    assert [op.op for op in patch.ops] == ["patch_spec"]
    assert patch.ops[0].path == "motion_phrases"
    assert apply_diff(before, patch) == after


def test_patch_nested_field():
    base = spec(clip("a", 0))
    patch = EditSpecDiff(
        from_version=1,
        to_version=2,
        source="user",
        ops=[PatchClip(clip_id="a", path="decision.reasoning", value="更强的落点")],
    )
    result = apply_diff(base, patch)
    assert result.revision == 2
    assert result.clips[0].decision.reasoning == "更强的落点"
    assert base.clips[0].decision.reasoning is None


def test_shift_after_is_deterministic():
    result = apply_diff(
        spec(clip("a", 0), clip("b", 1), clip("c", 2)),
        EditSpecDiff(
            from_version=1,
            to_version=2,
            ops=[ShiftAfter(from_clip="b", delta_sec=0.25)],
        ),
    )
    assert [item.timeline.in_sec for item in result.clips] == [0, 1.25, 2.25]


def test_non_user_cannot_modify_locked_clip():
    with pytest.raises(DiffError, match="锁定"):
        apply_diff(
            spec(clip("a", 0, locked=True)),
            EditSpecDiff(
                from_version=1,
                to_version=2,
                source="critic",
                ops=[PatchClip(clip_id="a", path="decision.reasoning", value="change")],
            ),
        )


def test_revision_mismatch_rejected():
    with pytest.raises(DiffError, match="revision"):
        apply_diff(
            spec(clip("a", 0), revision=3),
            EditSpecDiff(from_version=1, to_version=2),
        )


def test_invalid_path_rejected():
    with pytest.raises(DiffError, match="不存在"):
        apply_diff(
            spec(clip("a", 0)),
            EditSpecDiff(
                from_version=1,
                to_version=2,
                source="user",
                ops=[PatchClip(clip_id="a", path="retime.no_such_field", value=1)],
            ),
        )
