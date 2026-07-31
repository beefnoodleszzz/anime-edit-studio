from studio.critic.creative import evaluate_rhythm
from studio.creative.reference import EditingStyleProfile
from studio.editing.music.map import MusicMap, MusicSection
from studio.editspec.schema import (
    Canvas,
    Clip,
    EditSpec,
    SourceRange,
    Timebase,
    TimelinePlacement,
)


def test_rhythm_qa_measures_output_against_style_contract():
    clips = [
        Clip(
            id=f"c{index}",
            asset_id="a",
            source=SourceRange(in_sec=index * .5, out_sec=(index + 1) * .5),
            timeline=TimelinePlacement(in_sec=index * .5, duration_sec=.5),
        )
        for index in range(8)
    ]
    spec = EditSpec(
        id="rhythm",
        timebase=Timebase(num=30, den=1),
        canvas=Canvas(width=1080, height=1350),
        clips=clips,
    )
    music = MusicMap(
        duration_sec=4, bpm=120,
        beats=[index * .5 for index in range(8)],
        bars=[0], downbeats=[0], onsets=[], beat_energy=[],
        sections=[MusicSection(type="drop", start=0, end=4, energy=.9)],
        impact_points=[], risers=[], breaks=[], silences=[],
        spectral_change_points=[],
    )
    profile = EditingStyleProfile(
        id="fast", name="Fast", source="curated",
        target_cut_density=1.75, median_shot_length=.5,
        min_shot_length=.25, max_shot_length=1,
        beat_sync_target=.9,
    )
    result = evaluate_rhythm(spec, music, profile)
    assert result.passed
    assert result.beat_sync_ratio == 1
    assert result.cut_density == 1.75


def test_reference_rhythm_qa_rejects_mechanical_oversync():
    clips = [
        Clip(
            id=f"c{index}",
            asset_id="a",
            source=SourceRange(in_sec=index * .5, out_sec=(index + 1) * .5),
            timeline=TimelinePlacement(in_sec=index * .5, duration_sec=.5),
        )
        for index in range(8)
    ]
    spec = EditSpec(
        id="reference-oversync",
        timebase=Timebase(num=30, den=1),
        canvas=Canvas(width=1080, height=1080),
        clips=clips,
    )
    music = MusicMap(
        duration_sec=4, bpm=120,
        beats=[index * .5 for index in range(8)],
        bars=[0], downbeats=[0], onsets=[], beat_energy=[],
        sections=[MusicSection(type="drop", start=0, end=4, energy=.9)],
        impact_points=[], risers=[], breaks=[], silences=[],
        spectral_change_points=[],
    )
    profile = EditingStyleProfile(
        id="measured-reference", name="Measured", source="reference",
        target_cut_density=1.75, median_shot_length=.5,
        min_shot_length=.25, max_shot_length=1,
        beat_sync_target=.5,
    )

    result = evaluate_rhythm(spec, music, profile)

    beat_check = next(
        check for check in result.checks if check.metric == "beat_sync_ratio"
    )
    assert not beat_check.passed
    assert not result.passed
