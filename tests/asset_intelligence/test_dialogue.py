import shutil
import subprocess
from pathlib import Path

import pytest

from studio.asset_intelligence.dialogue import (
    SubtitleEvent,
    apply_dialogue_layer,
    extract_dialogue_audio,
    find_dialogue_line,
    parse_ass_events,
)
from studio.editspec.schema import Canvas, EditSpec, Timebase, Track

_SAMPLE_ASS = """\
[Script Info]
Title: sample

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:01.00,0:00:02.50,Default,,0,0,0,,¡todos los cazadores de demonios son una molestia!
Dialogue: 0,1:55:18.83,1:55:21.54,Default,,0,0,0,,{\\i1}¡"Demonio" me queda perfecto!{\\i}
Dialogue: 0,0:00:05.00,0:00:06.20,Default,,0,0,0,,Segunda línea,\\Ncon salto y coma.
"""


def test_parse_ass_events_reads_timing_and_strips_tags():
    events = parse_ass_events(_SAMPLE_ASS)
    assert len(events) == 3
    first = events[0]
    assert first.start_sec == pytest.approx(1.0)
    assert first.end_sec == pytest.approx(2.5)
    assert "molestia" in first.text


def test_parse_ass_events_raises_on_unparsable_timestamp():
    """A malformed timestamp must fail loudly, never produce a silent
    wrong-time line — AGENTS R6, deterministic and never silently wrong."""
    bad = (
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        "Dialogue: 0,0:01:55:18.83,0:01:55:21.54,Default,,0,0,0,,bad timestamp\n"
    )
    with pytest.raises(ValueError):
        parse_ass_events(bad)


def test_parse_ass_events_strips_override_blocks_and_line_breaks():
    content = (
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        "Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,"
        "{\\i1}Segunda línea,\\Ncon salto y coma.{\\i}\n"
    )
    events = parse_ass_events(content)
    assert len(events) == 1
    assert events[0].text == "Segunda línea, con salto y coma."
    assert "{" not in events[0].text
    assert "\\N" not in events[0].text


def test_find_dialogue_line_is_case_insensitive_and_deterministic():
    content = (
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        "Dialogue: 0,0:00:10.00,0:00:11.00,Default,,0,0,0,,No es esto.\n"
        "Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,¡DEMONIO me queda perfecto!\n"
    )
    events = parse_ass_events(content)
    found = find_dialogue_line(events, "demonio me queda perfecto")
    assert found is not None
    assert found.start_sec == pytest.approx(1.0)


def test_find_dialogue_line_returns_none_when_absent():
    events = parse_ass_events(_SAMPLE_ASS)
    assert find_dialogue_line(events, "frase que no existe en absoluto") is None


def test_find_dialogue_line_rejects_empty_query():
    with pytest.raises(ValueError):
        find_dialogue_line([], "  ")


def _spec() -> EditSpec:
    return EditSpec(
        id="t", timebase=Timebase(num=30, den=1),
        canvas=Canvas(width=1080, height=1080),
        tracks=[
            Track(id="V1", kind="video"),
            Track(id="A1", kind="audio", role="music"),
        ],
        clips=[],
    )


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg 不在 PATH 上")
def test_apply_dialogue_layer_adds_track_and_audio_layer(tmp_path: Path):
    clip = tmp_path / "line.wav"
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi",
            "-i", "sine=frequency=440:duration=2",
            str(clip),
        ],
        capture_output=True, text=True, check=True, timeout=30,
    )
    updated = apply_dialogue_layer(
        _spec(), layer_id="dx-hook", clip_path=clip,
        timeline_in_sec=0.5, gain_db=-1.5,
    )
    assert [t.id for t in updated.tracks] == ["V1", "A1", "A2"]
    assert [t.role for t in updated.tracks] == [None, "music", "dialogue"]
    assert len(updated.audio) == 1
    layer = updated.audio[0]
    assert layer.track == "A2"
    assert layer.timeline_in_sec == pytest.approx(0.5)
    assert layer.gain_db == pytest.approx(-1.5)
    assert layer.duration_sec == pytest.approx(2.0, abs=0.05)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg 不在 PATH 上")
def test_apply_dialogue_layer_reuses_an_existing_dialogue_track(tmp_path: Path):
    clip = tmp_path / "line.wav"
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi",
            "-i", "sine=frequency=440:duration=1",
            str(clip),
        ],
        capture_output=True, text=True, check=True, timeout=30,
    )
    spec = _spec()
    spec.tracks.append(Track(id="A2", kind="audio", role="dialogue"))
    updated = apply_dialogue_layer(
        spec, layer_id="dx-hook", clip_path=clip, timeline_in_sec=0.0,
    )
    assert [t.id for t in updated.tracks] == ["V1", "A1", "A2"]


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg 不在 PATH 上")
def test_extract_dialogue_audio_cuts_the_measured_window(tmp_path: Path):
    source = tmp_path / "source.wav"
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi",
            "-i", "sine=frequency=440:duration=6",
            str(source),
        ],
        capture_output=True, text=True, check=True, timeout=30,
    )
    event = SubtitleEvent(start_sec=2.0, end_sec=3.0, text="test")
    output = tmp_path / "clip.wav"
    extract_dialogue_audio(
        source, event, audio_stream_index=0, output_path=output, pad_sec=0.2,
    )
    assert output.exists()
    probe = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(output),
        ],
        capture_output=True, text=True, check=True, timeout=30,
    )
    duration = float(probe.stdout.strip())
    # window is (end - start) + 2*pad = 1.0 + 0.4 = 1.4s
    assert duration == pytest.approx(1.4, abs=0.05)
