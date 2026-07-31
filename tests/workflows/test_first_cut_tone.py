from studio.workflows.first_cut import (
    _action_sync_for_mode,
    _character_group_exclusions,
    _is_relationship_character,
    _tone_allows_menacing_expression,
)
from studio.editing.music import MusicMap
from studio.editspec.schema import (
    Canvas,
    Clip,
    EditSpec,
    SourceRange,
    Timebase,
    TimelinePlacement,
)


def test_menacing_tones_preserve_villain_performance_signals():
    assert _tone_allows_menacing_expression(["front_facing", "menacing"])
    assert _tone_allows_menacing_expression(["dominant"])


def test_neutral_tones_keep_default_expression_filter():
    assert not _tone_allows_menacing_expression(["clean", "heroic"])
    assert not _tone_allows_menacing_expression(None)


def test_nezuko_identity_filter_keeps_female_subject_tags():
    exclusions = _character_group_exclusions("kamado_nezuko")

    assert "1girl" not in exclusions
    assert "1boy" in exclusions
    assert "multiple_girls" in exclusions
    assert "2girls" in exclusions


def test_default_identity_filter_keeps_male_subject_tags():
    exclusions = _character_group_exclusions("akaza")

    assert "1boy" not in exclusions
    assert "1girl" in exclusions


def test_relationship_catalog_keeps_multi_subject_shots():
    assert _is_relationship_character("binquan_cp")
    assert not _is_relationship_character("akaza")


def test_naked_cut_mode_never_adds_action_sync_retime():
    spec = EditSpec(
        id="naked",
        timebase=Timebase(num=24, den=1),
        canvas=Canvas(width=1080, height=1350, aspect="4:5"),
        clips=[
            Clip(
                id="c1",
                asset_id="a",
                shot_id="s1",
                source=SourceRange(in_sec=10, out_sec=12),
                timeline=TimelinePlacement(in_sec=0, duration_sec=2),
            ),
        ],
    )
    music = MusicMap(
        duration_sec=2,
        bpm=120,
        beats=[.5],
        bars=[],
        downbeats=[],
        onsets=[],
        beat_energy=[],
        sections=[],
        impact_points=[.5],
        risers=[],
        breaks=[],
        silences=[],
        spectral_change_points=[],
    )

    result, report = _action_sync_for_mode(
        spec,
        music=music,
        peaks_by_shot={"s1": [object()]},
        shot_starts={"s1": 8.0},
        naked_cut=True,
    )

    assert result.clips[0].retime.type == "constant"
    assert report.retimed_clips == 0
    assert report.rows[0].note == "naked cut: action sync disabled"
