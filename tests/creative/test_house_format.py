"""The standing delivery format: 16–20s, 3–5s hook, beat-locked body."""
import pytest

from studio.creative.director.plan import (
    HOOK_RANGE_SEC,
    HOUSE_DURATION_RANGE,
    HOUSE_DURATION_SEC,
    DirectorBrief,
    generate_director_plan,
)
from studio.editing.music import MusicMap


def _music(duration: float = 18.0, **kw) -> MusicMap:
    beats = [round(index * 0.5, 3) for index in range(int(duration / 0.5) + 1)]
    base = dict(
        duration_sec=duration, bpm=120.0, beats=beats, bars=[], downbeats=beats[::4],
        onsets=[], beat_energy=[], sections=[], impact_points=[],
        risers=[], breaks=[], silences=[], spectral_change_points=[],
    )
    base.update(kw)
    return MusicMap(**base)


def _brief(duration: float = HOUSE_DURATION_SEC) -> DirectorBrief:
    return DirectorBrief(
        project_id="house", duration_sec=duration,
        primary_characters=["akaza"], tone=["menacing"],
    )


def test_house_default_sits_inside_the_declared_range():
    low, high = HOUSE_DURATION_RANGE
    assert low <= HOUSE_DURATION_SEC <= high


@pytest.mark.parametrize("duration", [16.0, 18.0, 20.0])
def test_hook_lands_in_the_three_to_five_second_window(duration):
    plan = generate_director_plan(_brief(duration), _music(duration), None)
    hook = plan.structure[0]
    assert hook.start == 0.0
    assert HOOK_RANGE_SEC[0] <= hook.end <= HOOK_RANGE_SEC[1], plan.structure


def test_hook_snaps_to_a_musical_event_not_an_arbitrary_timestamp():
    """A handover on a bare timestamp reads as a stumble."""
    music = _music(18.0, impact_points=[4.25])
    plan = generate_director_plan(_brief(), music, None)
    assert plan.structure[0].end == pytest.approx(4.25)


def test_body_after_the_hook_covers_the_rest_of_the_piece():
    plan = generate_director_plan(_brief(), _music(), None)
    assert len(plan.structure) >= 2
    assert plan.structure[0].end == plan.structure[1].start
    assert plan.structure[-1].end == pytest.approx(plan.duration_sec)
    for left, right in zip(plan.structure, plan.structure[1:]):
        assert left.end == pytest.approx(right.start), plan.structure


def test_hook_is_not_cut_as_densely_as_the_body():
    """The hook has to establish who and where before the body starts cutting."""
    plan = generate_director_plan(_brief(), _music(), None)
    body = plan.structure[1:]
    assert plan.structure[0].average_shot_length >= min(
        section.average_shot_length for section in body
    )


def test_short_pieces_are_left_alone():
    """Below the format's floor a 3–5s hook would swallow the whole piece."""
    plan = generate_director_plan(_brief(6.0), _music(6.0), None)
    assert plan.structure[0].end < HOOK_RANGE_SEC[1]


def test_a_musical_section_already_in_the_window_is_respected():
    """Reshaping a head that already fits would override measured music."""
    from studio.editing.music.map import MusicSection

    music = _music(18.0, sections=[
        MusicSection(type="intro", start=0.0, end=3.8, energy=0.35),
        MusicSection(type="drop", start=3.8, end=18.0, energy=0.9),
    ])
    plan = generate_director_plan(_brief(), music, None)
    assert plan.structure[0].end == pytest.approx(3.8)
