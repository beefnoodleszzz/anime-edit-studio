"""EditSpec schema 与 validator 测试。

重点覆盖 v1 从未校验、因而反复出问题的几类错误：
时长不自洽、时间线重叠、媒体缺失、以及使用未验证能力（R3）。
"""
from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError as PydanticValidationError

from studio.editspec.schema import (
    Canvas,
    Clip,
    EditSpec,
    Framing,
    Retime,
    SourceRange,
    Timebase,
    TimelinePlacement,
    Track,
)
from studio.editspec.validator import Severity, ValidationError, validate


def make_clip(cid: str, t_in: float, dur: float, *, src_in: float = 10.0, **kw) -> Clip:
    return Clip(
        id=cid,
        asset_id="asset_a",
        source=SourceRange(in_sec=src_in, out_sec=src_in + dur),
        timeline=TimelinePlacement(in_sec=t_in, duration_sec=dur),
        **kw,
    )


def make_spec(clips: list[Clip], **kw) -> EditSpec:
    return EditSpec(
        id="test",
        timebase=Timebase(num=24000, den=1001),
        canvas=Canvas(width=3072, height=3840, aspect="4:5"),
        clips=clips,
        **kw,
    )


ALL_VERIFIED = lambda _: True      # noqa: E731
NONE_VERIFIED = lambda _: False    # noqa: E731


class TestSchema:
    def test_minimal_spec(self):
        spec = make_spec([make_clip("c1", 0.0, 2.0)])
        assert spec.spec_version == "2.0.0"
        assert spec.revision == 1
        assert spec.duration_sec == pytest.approx(2.0)

    def test_extra_fields_forbidden(self):
        """v1 的病：JSON 字段随意增长。这里从 schema 层堵死。"""
        with pytest.raises(PydanticValidationError):
            Canvas(width=100, height=100, bogus_field=1)

    def test_source_out_must_exceed_in(self):
        with pytest.raises(PydanticValidationError, match="必须大于"):
            SourceRange(in_sec=5.0, out_sec=5.0)

    def test_speed_consistency_enforced(self):
        """源 2s、速度 1.0，却声称时间线 5s —— 必须拒绝。"""
        with pytest.raises(PydanticValidationError, match="不符"):
            Clip(
                id="bad",
                asset_id="a",
                source=SourceRange(in_sec=0, out_sec=2),
                timeline=TimelinePlacement(in_sec=0, duration_sec=5),
            )

    def test_slow_motion_is_consistent(self):
        """源 1s、0.5 倍速 → 时间线 2s，合法。"""
        clip = Clip(
            id="slow",
            asset_id="a",
            source=SourceRange(in_sec=0, out_sec=1.0),
            timeline=TimelinePlacement(in_sec=0, duration_sec=2.0),
            retime=Retime(type="constant", speed=0.5),
        )
        assert clip.retime.speed == 0.5

    def test_duration_derived_not_stored(self):
        """总时长永远由 clips 算出，不可能与 clips 不一致。"""
        spec = make_spec([make_clip("c1", 0.0, 2.0), make_clip("c2", 2.0, 3.0)])
        assert spec.duration_sec == pytest.approx(5.0)

    def test_clip_lookup_and_track_filter(self):
        spec = make_spec([make_clip("c2", 2.0, 1.0), make_clip("c1", 0.0, 2.0)])
        assert spec.clip_by_id("c1").timeline.in_sec == 0.0
        assert spec.clip_by_id("nope") is None
        # clips_on_track 按时间排序，与声明顺序无关
        assert [c.id for c in spec.clips_on_track("V1")] == ["c1", "c2"]


class TestStructureValidation:
    def test_valid_spec_passes(self):
        spec = make_spec([make_clip("c1", 0.0, 2.0), make_clip("c2", 2.0, 1.5)])
        result = validate(spec, is_verified=ALL_VERIFIED)
        assert result.ok

    def test_empty_spec_rejected(self):
        result = validate(make_spec([]), is_verified=ALL_VERIFIED)
        assert not result.ok
        assert result.errors[0].code == "EMPTY_SPEC"

    def test_duplicate_clip_id_rejected(self):
        """diff 靠 clip.id 定位，重复 ID 会让增量更新失控。"""
        spec = make_spec([make_clip("dup", 0.0, 1.0), make_clip("dup", 1.0, 1.0)])
        result = validate(spec, is_verified=ALL_VERIFIED)
        assert not result.ok
        assert any(i.code == "DUPLICATE_CLIP_ID" for i in result.errors)

    def test_overlap_is_error(self):
        spec = make_spec([make_clip("c1", 0.0, 2.0), make_clip("c2", 1.0, 2.0)])
        result = validate(spec, is_verified=ALL_VERIFIED)
        assert not result.ok
        overlap = next(i for i in result.errors if i.code == "TIMELINE_OVERLAP")
        assert overlap.clip_id == "c2"

    def test_gap_is_only_warning(self):
        """空洞可能是有意的黑场，不阻断执行。"""
        spec = make_spec([make_clip("c1", 0.0, 1.0), make_clip("c2", 3.0, 1.0)])
        result = validate(spec, is_verified=ALL_VERIFIED)
        assert result.ok
        assert any(i.code == "TIMELINE_GAP" for i in result.warnings)

    def test_subframe_touching_is_not_overlap(self):
        """相邻片段的浮点噪声不应被误判为重叠。"""
        spec = make_spec([make_clip("c1", 0.0, 1.0), make_clip("c2", 0.9999999, 1.0)])
        result = validate(spec, is_verified=ALL_VERIFIED)
        assert not any(i.code == "TIMELINE_OVERLAP" for i in result.issues)

    def test_unknown_track_rejected(self):
        spec = make_spec([make_clip("c1", 0.0, 1.0)])
        spec.clips[0].timeline.track = "V9"
        result = validate(spec, is_verified=ALL_VERIFIED)
        assert any(i.code == "UNKNOWN_TRACK" for i in result.errors)

    def test_multitrack_overlap_checked_per_track(self):
        """不同轨道上的同时段片段是合法的（V1 底 + V2 叠加）。"""
        spec = make_spec(
            [make_clip("c1", 0.0, 2.0), make_clip("c2", 0.0, 2.0)],
            tracks=[Track(id="V1", kind="video"), Track(id="V2", kind="video")],
        )
        spec.clips[1].timeline.track = "V2"
        result = validate(spec, is_verified=ALL_VERIFIED)
        assert result.ok


class TestMediaValidation:
    def test_missing_asset_rejected(self):
        spec = make_spec([make_clip("c1", 0.0, 1.0)])
        result = validate(spec, resolve_asset=lambda _: None, is_verified=ALL_VERIFIED)
        assert any(i.code == "ASSET_NOT_FOUND" for i in result.errors)

    def test_missing_file_rejected(self):
        spec = make_spec([make_clip("c1", 0.0, 1.0)])
        result = validate(
            spec,
            resolve_asset=lambda _: Path("/nonexistent/nope.mov"),
            is_verified=ALL_VERIFIED,
        )
        assert any(i.code == "MEDIA_MISSING" for i in result.errors)

    def test_existing_file_passes(self, tmp_path):
        media = tmp_path / "a.mov"
        media.write_bytes(b"x")
        spec = make_spec([make_clip("c1", 0.0, 1.0)])
        result = validate(spec, resolve_asset=lambda _: media, is_verified=ALL_VERIFIED)
        assert result.ok

    def test_no_resolver_warns_but_passes(self):
        spec = make_spec([make_clip("c1", 0.0, 1.0)])
        result = validate(spec, is_verified=ALL_VERIFIED)
        assert result.ok
        assert any(i.code == "MEDIA_UNCHECKED" for i in result.warnings)


class TestCapabilityGate:
    """AGENTS.md R3 —— 未 verified 的能力不得进入执行层。"""

    def test_plain_cut_needs_nothing(self):
        spec = make_spec([make_clip("c1", 0.0, 1.0)])
        assert validate(spec, is_verified=NONE_VERIFIED).ok

    def test_smart_reframe_blocked_when_unverified(self):
        spec = make_spec(
            [make_clip("c1", 0.0, 1.0, framing=Framing(mode="smart_reframe"))]
        )
        result = validate(spec, is_verified=NONE_VERIFIED)
        assert not result.ok
        issue = next(i for i in result.errors if i.code == "CAPABILITY_NOT_VERIFIED")
        assert "portrait_reframe" in issue.message

    def test_speed_ramp_blocked_when_unverified(self):
        spec = make_spec(
            [
                make_clip(
                    "c1", 0.0, 1.0,
                    retime=Retime(type="speed_ramp", entry_speed=1.0,
                                  impact_speed=0.35, exit_speed=1.4),
                )
            ]
        )
        result = validate(spec, is_verified=NONE_VERIFIED)
        assert any("speed_ramp" in i.message for i in result.errors)

    def test_allowed_once_verified(self):
        spec = make_spec(
            [make_clip("c1", 0.0, 1.0, framing=Framing(mode="smart_reframe"))]
        )
        assert validate(spec, is_verified=ALL_VERIFIED).ok

    def test_uses_real_capability_matrix_by_default(self):
        """默认走真实 yaml：当前 portrait_reframe 未 verified，应被拦截。"""
        spec = make_spec(
            [make_clip("c1", 0.0, 1.0, framing=Framing(mode="smart_reframe"))]
        )
        assert not validate(spec).ok


class TestValidationResult:
    def test_reports_all_issues_not_just_first(self):
        """AI 生成的 spec 常一次错一批，必须一次报全。"""
        spec = make_spec([make_clip("dup", 0.0, 2.0), make_clip("dup", 1.0, 2.0)])
        result = validate(spec, is_verified=ALL_VERIFIED)
        codes = {i.code for i in result.errors}
        assert {"DUPLICATE_CLIP_ID", "TIMELINE_OVERLAP"} <= codes

    def test_raise_if_failed(self):
        with pytest.raises(ValidationError, match="校验失败"):
            validate(make_spec([]), is_verified=ALL_VERIFIED).raise_if_failed()

    def test_to_dict_is_json_friendly(self):
        result = validate(make_spec([]), is_verified=ALL_VERIFIED)
        d = result.to_dict()
        assert d["ok"] is False and d["error_count"] >= 1
        assert d["issues"][0]["severity"] == Severity.ERROR.value
