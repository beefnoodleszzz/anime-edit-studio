"""Phase 1 验收测试 —— MIGRATION_PLAN §2 的硬性标准。

需要本机运行 DaVinci Resolve，CI 中跳过：
    .venv/bin/python -m pytest -m requires_resolve -v

验收标准：
    给定一份最简 EditSpec，一条命令完成
    连接 → 建工程 → 导入媒体 → 建时间线 → 按 in/out 放片段 → 打标记，
    且重跑幂等、改一个 clip 只更新一个 clip、用户 0 次手动操作。
"""
from __future__ import annotations

import pytest

from studio.core.assets import PROXY_DIR, FilesystemResolver
from studio.editspec.schema import (
    Canvas,
    Clip,
    Decision,
    EditSpec,
    Marker,
    SourceRange,
    Timebase,
    TimelinePlacement,
)
from studio.execution.compiler import ResolveCompiler, clip_fingerprint

pytestmark = pytest.mark.requires_resolve

PROJECT_NAME = "_aes_phase1_acceptance"


@pytest.fixture(scope="module")
def asset_ids() -> list[str]:
    ids = FilesystemResolver([PROXY_DIR]).available_ids()
    if len(ids) < 2:
        pytest.skip(f"需要至少 2 个素材，{PROXY_DIR} 中只有 {len(ids)} 个")
    return ids[:2]


@pytest.fixture
def resolver():
    return FilesystemResolver([PROXY_DIR])


@pytest.fixture
def adapter():
    from studio.execution.resolve import ResolveAdapter, ResolveUnavailable

    try:
        return ResolveAdapter.open()
    except ResolveUnavailable as exc:
        pytest.skip(f"Resolve 不可用: {str(exc).splitlines()[0]}")


@pytest.fixture
def compiler(adapter, resolver, tmp_path):
    return ResolveCompiler(adapter, resolver, state_dir=tmp_path)


def make_spec(asset_ids: list[str], *, second_source_in: float = 30.0) -> EditSpec:
    """MIGRATION_PLAN §2 里那份最简 EditSpec。"""
    a, b = asset_ids
    return EditSpec(
        id=PROJECT_NAME,
        timebase=Timebase(num=24, den=1),
        canvas=Canvas(width=1080, height=1350, aspect="4:5"),
        clips=[
            Clip(
                id="clip_001",
                asset_id=a,
                source=SourceRange(in_sec=10.0, out_sec=12.0),
                timeline=TimelinePlacement(in_sec=0.0, duration_sec=2.0),
                role="opening",
                decision=Decision(source="rule", reasoning="Phase 1 验收：首片段"),
            ),
            Clip(
                id="clip_002",
                asset_id=b,
                source=SourceRange(in_sec=second_source_in, out_sec=second_source_in + 2.0),
                timeline=TimelinePlacement(in_sec=2.0, duration_sec=2.0),
                role="impact",
                decision=Decision(source="rule", reasoning="Phase 1 验收：次片段"),
            ),
        ],
        markers=[Marker(sec=2.0, kind="cut", note="clip 边界")],
    )


class TestPhase1SuccessCriteria:
    def test_build_from_scratch(self, compiler, adapter, asset_ids):
        """核心标准：一条命令从零建出时间线。"""
        report = compiler.build(make_spec(asset_ids), reset_project=True)

        assert report.clips_total == 2
        assert report.clips_written == 2
        assert report.markers_written == 2, "每个 clip 都应带 clip_id 标记"

        items = adapter.timeline_items(1)
        assert len(items) == 2, "时间线上应有 2 个片段"

    def test_source_in_out_is_honored(self, compiler, adapter, asset_ids):
        """片段必须落在指定的源入出点上，而不是从素材头开始。

        注意用 source_in_seconds（基于 GetLeftOffset），
        不能用 GetSourceStartFrame —— 后者含媒体起始时间码偏移（P7）。
        """
        compiler.build(make_spec(asset_ids), reset_project=True)
        items = sorted(adapter.timeline_items(1), key=lambda i: i.GetStart())

        assert adapter.source_in_seconds(items[0]) == pytest.approx(10.0, abs=0.05)
        assert adapter.source_in_seconds(items[1]) == pytest.approx(30.0, abs=0.05)

    def test_timeline_durations_are_correct(self, compiler, adapter, asset_ids):
        """24fps 下 2 秒 = 48 帧，且两段首尾相接无空隙。"""
        compiler.build(make_spec(asset_ids), reset_project=True)
        items = adapter.timeline_items(1)

        for item in items:
            assert item.GetDuration() == 48, f"应为 48 帧，实际 {item.GetDuration()}"
        assert items[1].GetStart() == items[0].GetEnd(), "两段之间不应有空隙"

    def test_rebuild_is_idempotent(self, compiler, adapter, asset_ids):
        """同一 spec 跑两次，结果必须一致 —— 不重复堆片段。"""
        spec = make_spec(asset_ids)
        compiler.build(spec, reset_project=True)
        first = [(i.GetStart(), i.GetDuration()) for i in adapter.timeline_items(1)]

        compiler.build(spec)
        second = [(i.GetStart(), i.GetDuration()) for i in adapter.timeline_items(1)]

        assert first == second, "重跑后时间线布局发生了变化"
        assert len(second) == 2, "重跑不应累积出多余片段"


class TestIncrementalUpdate:
    """MASTER PLAN §54：修订应基于 diff。

    实测约束 P10：Resolve 的 AppendToTimeline 无法填补轨道空洞，
    因此时间线总是全量重建；增量体现在 changed_ranges —— 渲染层只渲变化区间。
    """

    def test_unchanged_spec_needs_no_rebuild(self, compiler, asset_ids):
        spec = make_spec(asset_ids)
        compiler.build(spec, reset_project=True)

        report = compiler.update(spec)
        assert report.clips_changed == 0
        assert report.clips_unchanged == 2
        assert report.changed_ranges == [], "无变化时不应有待渲区间"
        assert report.clips_written == 0, "无变化时不应重建时间线"

    def test_changing_one_clip_narrows_render_scope(self, compiler, adapter, asset_ids):
        """一个 clip 变了 → 只有它的时间区间需要重渲。"""
        compiler.build(make_spec(asset_ids), reset_project=True)
        report = compiler.update(make_spec(asset_ids, second_source_in=60.0))

        assert report.clips_changed == 1, "只有 1 个 clip 变化"
        assert report.clips_unchanged == 1
        assert report.changed_ranges == [(2.0, 4.0)], "待渲区间应只覆盖第 2 个 clip"
        assert report.changed_duration_sec == pytest.approx(2.0)
        # 全片 4 秒，只需重渲 2 秒
        assert report.changed_duration_sec < 4.0

        assert len(adapter.timeline_items(1)) == 2, "总片段数不变"

    def test_updated_clip_actually_uses_new_source(self, compiler, adapter, asset_ids):
        compiler.build(make_spec(asset_ids), reset_project=True)
        compiler.update(make_spec(asset_ids, second_source_in=60.0))

        items = sorted(adapter.timeline_items(1), key=lambda i: i.GetStart())
        assert adapter.source_in_seconds(items[1]) == pytest.approx(60.0, abs=0.05)
        # 未改动的那个必须原封不动
        assert adapter.source_in_seconds(items[0]) == pytest.approx(10.0, abs=0.05)

    def test_clip_id_marker_enables_lookup(self, compiler, adapter, asset_ids):
        """增量更新依赖 marker 定位，这条断了增量就退化成全量。"""
        compiler.build(make_spec(asset_ids), reset_project=True)

        assert adapter.find_item_by_clip_id("clip_001") is not None
        assert adapter.find_item_by_clip_id("clip_002") is not None
        assert adapter.find_item_by_clip_id("clip_999") is None


class TestChangedRanges:
    """changed_ranges 的合并逻辑 —— 不依赖 Resolve。"""

    pytestmark = []

    def test_merges_adjacent_ranges(self):
        from studio.execution.compiler import merge_ranges

        assert merge_ranges([(0.0, 2.0), (2.0, 4.0)]) == [(0.0, 4.0)]

    def test_merges_overlapping(self):
        from studio.execution.compiler import merge_ranges

        assert merge_ranges([(0.0, 3.0), (1.0, 2.0)]) == [(0.0, 3.0)]

    def test_keeps_disjoint_separate(self):
        from studio.execution.compiler import merge_ranges

        assert merge_ranges([(5.0, 6.0), (0.0, 1.0)]) == [(0.0, 1.0), (5.0, 6.0)]

    def test_empty(self):
        from studio.execution.compiler import merge_ranges

        assert merge_ranges([]) == []


class TestFingerprint:
    """指纹决定是否重建 —— 不依赖 Resolve，可离线跑。"""

    pytestmark = []  # 覆盖模块级 marker

    def test_layout_change_triggers_rebuild(self):
        a = Clip(
            id="c", asset_id="x",
            source=SourceRange(in_sec=0, out_sec=1),
            timeline=TimelinePlacement(in_sec=0, duration_sec=1),
        )
        b = a.model_copy(deep=True)
        b.source.out_sec = 2.0
        b.timeline.duration_sec = 2.0
        assert clip_fingerprint(a) != clip_fingerprint(b)

    def test_metadata_change_does_not_trigger_rebuild(self):
        """AI 改一句 reasoning 不应导致重渲 —— 否则增量更新形同虚设。"""
        a = Clip(
            id="c", asset_id="x",
            source=SourceRange(in_sec=0, out_sec=1),
            timeline=TimelinePlacement(in_sec=0, duration_sec=1),
            decision=Decision(reasoning="原因 A", confidence=0.5),
        )
        b = a.model_copy(deep=True)
        b.decision.reasoning = "完全不同的解释"
        b.decision.confidence = 0.99
        assert clip_fingerprint(a) == clip_fingerprint(b)

    def test_recipe_and_audio_changes_trigger_rebuild(self):
        from studio.editspec.schema import RecipeRef, SfxCue

        a = Clip(
            id="c", asset_id="x",
            source=SourceRange(in_sec=0, out_sec=1),
            timeline=TimelinePlacement(in_sec=0, duration_sec=1),
        )
        effect = a.model_copy(deep=True)
        effect.effects = [RecipeRef(recipe="white_flash_v1")]
        assert clip_fingerprint(a) != clip_fingerprint(effect)
        sound = a.model_copy(deep=True)
        sound.audio.sfx = [SfxCue(recipe="impact_low_v1")]
        assert clip_fingerprint(a) != clip_fingerprint(sound)
