from studio.finishing import FINISHING_VERSION, finish_locked_picture

from tests.editspec.test_schema_and_validator import make_clip, make_spec


def test_locked_finishing_preserves_picture_and_is_bounded():
    source = make_spec(
        [make_clip("c1", 0.0, 1.0), make_clip("c2", 1.0, 1.0)]
    )
    # The production function requires accepted recipes, while this unit test
    # supplies a registry-backed real spec and an all-true capability check.
    result = finish_locked_picture(
        source,
        drop_sec=source.clips[-1].timeline.in_sec,
        capability_check=lambda _: True,
    )

    assert result.revision == source.revision + 1
    assert all(clip.decision.locked for clip in result.clips)
    assert [
        (c.id, c.asset_id, c.source, c.timeline) for c in result.clips
    ] == [
        (c.id, c.asset_id, c.source, c.timeline) for c in source.clips
    ]
    assert result.meta.model_versions["locked_finishing"] == FINISHING_VERSION
    assert sum(len(clip.audio.sfx) for clip in result.clips) <= 12
