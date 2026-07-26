from pathlib import Path

import pytest

from studio.editspec.schema import (
    CameraMove,
    Canvas,
    Caption,
    Clip,
    ClipAudio,
    Decision,
    EditSpec,
    Framing,
    Marker,
    RecipeRef,
    Retime,
    SfxCue,
    SourceRange,
    Timebase,
    TimelinePlacement,
    Track,
    Transition,
    TransitionEnd,
    VolumePoint,
)
from studio.editspec.validator import ValidationError, validate
from studio.execution.compiler import ResolveCompiler
from studio.execution.recipes import ParameterRule, Recipe, RecipeRegistry


def full_spec() -> EditSpec:
    return EditSpec(
        id="phase2-full",
        timebase=Timebase(num=24000, den=1001),
        canvas=Canvas(width=3072, height=3840, aspect="4:5"),
        tracks=[
            Track(id="V1", kind="video"),
            Track(id="A1", kind="audio", role="music"),
            Track(id="S1", kind="subtitle"),
        ],
        clips=[
            Clip(
                id="clip_001",
                asset_id="asset",
                shot_id="shot",
                source=SourceRange(in_sec=10, out_sec=12),
                timeline=TimelinePlacement(in_sec=0, duration_sec=2),
                framing=Framing(mode="manual", offset_x=0.1, scale=1.1),
                camera=CameraMove(move="push_in", from_scale=1, to_scale=1.1),
                retime=Retime(type="constant", speed=1, interpolation="optical_flow"),
                transition=Transition(
                    out=TransitionEnd(
                        recipe="flash_transition",
                        duration_sec=0.1,
                        params={"strength": 0.5},
                    )
                ),
                effects=[RecipeRef(recipe="impact", params={"strength": 0.7})],
                color=RecipeRef(recipe="anime_color", params={"intensity": 0.8}),
                audio=ClipAudio(
                    sfx=[SfxCue(recipe="impact_sound", at_sec=1, gain_db=-3)],
                    source_gain_db=-12,
                    volume_automation=[
                        VolumePoint(sec=0, db=-12),
                        VolumePoint(sec=1, db=-3),
                    ],
                ),
                decision=Decision(
                    source="ai",
                    confidence=0.8,
                    reasoning="drop 落点",
                    alternatives=["shot_b"],
                ),
            )
        ],
        markers=[Marker(sec=1, kind="drop", clip_id="clip_001")],
        captions=[
            Caption(
                id="cap",
                text="觉醒",
                start_sec=0.1,
                end_sec=0.8,
                style=RecipeRef(recipe="title", params={}),
            )
        ],
    )


def accepted_registry(tmp_path: Path) -> RecipeRegistry:
    folder = tmp_path / "recipes"
    folder.mkdir()
    recipes = []
    for recipe_id, kind, params in (
        ("impact", "effect", {"strength": ParameterRule(type="float", min=0, max=1)}),
        ("anime_color", "color", {"intensity": ParameterRule(type="float", min=0, max=1)}),
        ("impact_sound", "sound", {}),
        ("flash_transition", "transition", {"strength": ParameterRule(type="float", min=0, max=1)}),
        ("title", "title", {}),
    ):
        item = folder / recipe_id
        item.mkdir()
        for name in ("artifact.bin", "preview.mp4", "ACCEPTANCE.md"):
            (item / name).write_bytes(b"accepted")
        recipes.append(
            Recipe(
                id=recipe_id,
                version="1",
                kind=kind,
                engine="fusion" if kind != "sound" else "audio",
                capability="test",
                verified=True,
                artifact=f"recipes/{recipe_id}/artifact.bin",
                preview=f"recipes/{recipe_id}/preview.mp4",
                acceptance=f"recipes/{recipe_id}/ACCEPTANCE.md",
                parameters=params,
            )
        )
    return RecipeRegistry(recipes, root=tmp_path)


def test_complete_phase2_spec_rejects_multiple_fusion_versions_on_one_clip(tmp_path):
    media = tmp_path / "source.mov"
    media.write_bytes(b"media")
    result = validate(
        full_spec(),
        resolve_asset=lambda _: media,
        resolve_shot=lambda _: {"id": "shot"},
        is_verified=lambda _: True,
        recipe_registry=accepted_registry(tmp_path),
    )
    assert not result.ok
    assert any(
        issue.code == "FUSION_STACK_UNSUPPORTED"
        for issue in result.errors
    )


def test_compiler_rejects_unregistered_and_unverified_recipe_before_resolve(tmp_path):
    media = tmp_path / "source.mov"
    media.write_bytes(b"media")
    compiler = ResolveCompiler(
        object(),  # must never be touched: validation fails before Resolve execution
        lambda _: media,
        resolve_shot=lambda _: {"id": "shot"},
        state_dir=tmp_path,
    )
    with pytest.raises(ValidationError) as caught:
        compiler.build(full_spec())
    message = str(caught.value)
    assert "impact" in message
    assert "anime_color" in message
    assert "impact_sound" in message
    assert "flash_transition" in message
    assert "title" in message
