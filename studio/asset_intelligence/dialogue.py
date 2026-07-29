"""Locate and extract one spoken line's original-language audio.

No speech-to-text exists anywhere in this library yet — every shot's
``dialogue`` column is empty (checked directly: 0/12673 shots have a
non-empty value). Building full ASR is a separate, larger effort. What many
rips already carry, though, is an embedded subtitle track, and a subtitle
event's timing is language-independent: whatever language the *text* is in,
its start/end timestamp tells us exactly when the audio track — in any
language, including the original — says that line too.

So this module never guesses a timestamp (AGENTS.md R6): it locates a line
by searching a subtitle track's actual text, then extracts a clean audio
clip from a *specified* audio stream at that measured timing. The subtitle
language does not need to match the language you want the audio in; you
search in whatever language the embedded track happens to carry and extract
from whichever audio stream index you choose (see ``ffprobe`` to find it).
"""
from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from studio.editspec.schema import AudioLayer, EditSpec, Track

_ASS_TIME = re.compile(r"^(\d+):(\d{2}):(\d{2})\.(\d{2})$")


class SubtitleEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_sec: float = Field(..., ge=0)
    end_sec: float = Field(..., gt=0)
    text: str


def _parse_ass_time(value: str) -> float:
    match = _ASS_TIME.match(value.strip())
    if not match:
        raise ValueError(f"无法解析 ASS 时间戳: {value!r}")
    hours, minutes, seconds, centis = match.groups()
    return (
        int(hours) * 3600
        + int(minutes) * 60
        + int(seconds)
        + int(centis) / 100
    )


def _strip_ass_tags(text: str) -> str:
    """Drop override blocks (``{\\i1}`` etc.) and line breaks, keep the words."""
    text = re.sub(r"\{[^}]*\}", "", text)
    return text.replace("\\N", " ").replace("\\n", " ").strip()


def parse_ass_events(content: str) -> list[SubtitleEvent]:
    """Parse ``Dialogue:`` lines from raw ASS/SSA subtitle text."""
    events: list[SubtitleEvent] = []
    for line in content.splitlines():
        if not line.startswith("Dialogue:"):
            continue
        # Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
        # Text is free-form and may itself contain commas, so split with a cap.
        fields = line[len("Dialogue:"):].split(",", 9)
        if len(fields) < 10:
            continue
        _layer, start, end, _style, _name, _ml, _mr, _mv, _effect, text = fields
        events.append(
            SubtitleEvent(
                start_sec=_parse_ass_time(start),
                end_sec=_parse_ass_time(end),
                text=_strip_ass_tags(text),
            )
        )
    return sorted(events, key=lambda item: item.start_sec)


def extract_subtitle_events(
    source_path: Path, *, stream_index: int, ffmpeg: str = "ffmpeg"
) -> list[SubtitleEvent]:
    """Pull an embedded ASS/SSA subtitle stream and parse its dialogue events."""
    with tempfile.TemporaryDirectory() as scratch:
        out_path = Path(scratch) / "subs.ass"
        result = subprocess.run(
            [
                ffmpeg, "-y", "-i", str(source_path),
                "-map", f"0:{stream_index}", str(out_path),
            ],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0 or not out_path.exists():
            raise RuntimeError(
                f"字幕轨提取失败 (stream {stream_index}): {result.stderr[-500:]}"
            )
        return parse_ass_events(out_path.read_text(encoding="utf-8", errors="replace"))


def find_dialogue_line(
    events: list[SubtitleEvent], query: str
) -> SubtitleEvent | None:
    """First event (by start time) whose text contains ``query``, case-insensitive."""
    needle = query.strip().lower()
    if not needle:
        raise ValueError("query 不能为空")
    return next(
        (event for event in events if needle in event.text.lower()), None
    )


def extract_dialogue_audio(
    source_path: Path,
    event: SubtitleEvent,
    *,
    audio_stream_index: int,
    output_path: Path,
    pad_sec: float = 0.15,
    ffmpeg: str = "ffmpeg",
) -> Path:
    """Cut a clean audio clip for ``event`` from a specific audio stream.

    ``-ss``/``-to`` are placed *after* ``-i`` for frame-accurate trimming —
    a dialogue line is a few seconds long, so decode-then-cut accuracy
    matters more than the seek speed a pre-``-i`` ``-ss`` would buy.
    """
    start = max(0.0, event.start_sec - pad_sec)
    end = event.end_sec + pad_sec
    output_path.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            ffmpeg, "-y", "-i", str(source_path),
            "-ss", f"{start:.3f}", "-to", f"{end:.3f}",
            "-map", f"0:{audio_stream_index}",
            "-c:a", "pcm_s16le",
            str(output_path),
        ],
        capture_output=True, text=True, timeout=120,
    )
    if result.returncode != 0 or not output_path.exists():
        raise RuntimeError(f"台词音频提取失败: {result.stderr[-500:]}")
    return output_path


def _audio_clip_duration(path: Path, *, ffprobe: str = "ffprobe") -> float:
    result = subprocess.run(
        [
            ffprobe, "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(path),
        ],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise RuntimeError(f"无法测量音频时长: {path}: {result.stderr[-300:]}")
    return float(result.stdout.strip())


def apply_dialogue_layer(
    spec: EditSpec,
    *,
    layer_id: str,
    clip_path: Path,
    timeline_in_sec: float,
    gain_db: float = 0.0,
    track: str = "A2",
    track_role: str = "dialogue",
) -> EditSpec:
    """Mix a pre-extracted dialogue clip into the spec as its own audio layer.

    A dialogue line is a distinct semantic layer from the music bed and the
    drum-hit SFX design places (sound_design.py) — it belongs on its own
    Fairlight track, not folded into either. ``spec.tracks`` must declare the
    track (the compiler resolves ``AudioLayer.track`` by position among
    ``kind="audio"`` tracks — see ``_append_audio`` in
    ``studio/execution/compiler.py``), so this adds one if it's not already
    there instead of assuming the caller did.
    """
    if not clip_path.is_file():
        raise FileNotFoundError(f"台词音频不存在: {clip_path}")
    result = spec.model_copy(deep=True)
    if not any(track_item.id == track for track_item in result.tracks):
        result.tracks.append(Track(id=track, kind="audio", role=track_role))
    duration = _audio_clip_duration(clip_path)
    result.audio.append(
        AudioLayer(
            id=layer_id,
            path=str(clip_path),
            track=track,
            timeline_in_sec=timeline_in_sec,
            source_in_sec=0.0,
            duration_sec=duration,
            gain_db=gain_db,
        )
    )
    return result


__all__ = [
    "SubtitleEvent",
    "apply_dialogue_layer",
    "extract_dialogue_audio",
    "extract_subtitle_events",
    "find_dialogue_line",
    "parse_ass_events",
]
