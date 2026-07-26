"""Automated Technical QA. Creative quality never affects this result."""
from __future__ import annotations

import json
import re
import sqlite3
from fractions import Fraction
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from studio.execution.ffmpeg import (
    probe_media_json,
    run_media_diagnostic,
)


class QACheck(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    passed: bool
    measured: object = None
    expected: object = None
    detail: str = ""


class TechnicalQAResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    file: str
    passed: bool
    checks: list[QACheck]


def _last(log: str, pattern: str) -> float | None:
    values = re.findall(pattern, log)
    return float(values[-1]) if values else None


def run_technical_qa(
    path: Path,
    *,
    expected_duration: float,
    expected_width: int,
    expected_height: int,
    expected_fps: Fraction,
    allowed_codecs: set[str] | None = None,
    expected_audio: bool = True,
    target_lufs: float = -14.0,
    lufs_tolerance: float = 1.5,
    max_black_sec: float = 0.35,
    max_freeze_sec: float = 0.75,
    max_silence_sec: float = 1.0,
    expected_freeze_ranges: list[tuple[float, float]] | None = None,
    expected_silence_ranges: list[tuple[float, float]] | None = None,
    render_id: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> TechnicalQAResult:
    allowed_codecs = allowed_codecs or {"h264", "hevc", "prores"}
    checks: list[QACheck] = []
    exists = path.is_file() and path.stat().st_size > 0
    checks.append(QACheck(name="file_exists", passed=exists, measured=exists, expected=True))
    if not exists:
        return TechnicalQAResult(file=str(path), passed=False, checks=checks)
    try:
        probe = probe_media_json(path, count_frames=True)
        video = next(
            stream for stream in probe["streams"] if stream.get("codec_type") == "video"
        )
        audio = next(
            (stream for stream in probe["streams"] if stream.get("codec_type") == "audio"),
            None,
        )
        # Picture timing is authoritative for an EditSpec render. Container
        # duration can be longer because AAC is packetized in 1024-sample blocks.
        duration = float(video.get("duration") or probe["format"].get("duration") or 0)
        fps = Fraction(video.get("avg_frame_rate") or video["r_frame_rate"])
        checks.extend(
            [
                QACheck(
                    name="duration",
                    passed=abs(duration - expected_duration) <= max(0.08, 2 / float(expected_fps)),
                    measured=duration, expected=expected_duration,
                ),
                QACheck(
                    name="resolution",
                    passed=(video.get("width"), video.get("height"))
                    == (expected_width, expected_height),
                    measured=[video.get("width"), video.get("height")],
                    expected=[expected_width, expected_height],
                ),
                QACheck(
                    name="fps",
                    passed=abs(float(fps) - float(expected_fps)) <= 0.002,
                    measured=f"{fps.numerator}/{fps.denominator}",
                    expected=f"{expected_fps.numerator}/{expected_fps.denominator}",
                ),
                QACheck(
                    name="codec",
                    passed=video.get("codec_name") in allowed_codecs,
                    measured=video.get("codec_name"),
                    expected=sorted(allowed_codecs),
                ),
                QACheck(
                    name="audio_track",
                    passed=(audio is not None) == expected_audio,
                    measured=audio is not None,
                    expected=expected_audio,
                ),
                QACheck(
                    name="aspect_ratio",
                    passed=video.get("width") * expected_height
                    == video.get("height") * expected_width,
                    measured=f"{video.get('width')}:{video.get('height')}",
                    expected=f"{expected_width}:{expected_height}",
                ),
            ]
        )
        black_code, black_log = run_media_diagnostic(
            path, video_filter="blackdetect=d=0.1:pic_th=0.98"
        )
        black_durations = [
            float(value) for value in re.findall(r"black_duration:([\d.]+)", black_log)
        ]
        checks.append(
            QACheck(
                name="black_frames",
                passed=max(black_durations, default=0) <= max_black_sec,
                measured=max(black_durations, default=0),
                expected=f"<= {max_black_sec}s",
            )
        )
        freeze_code, freeze_log = run_media_diagnostic(
            path, video_filter="freezedetect=n=0.003:d=0.3"
        )
        freeze_starts = [float(value) for value in re.findall(r"freeze_start: ([\d.]+)", freeze_log)]
        freeze_ends = [float(value) for value in re.findall(r"freeze_end: ([\d.]+)", freeze_log)]
        expected_freezes = expected_freeze_ranges or []
        unexpected_freeze_durations = []
        for start, end in zip(freeze_starts, freeze_ends):
            fragments = [(start, end)]
            for allowed_start, allowed_end in expected_freezes:
                next_fragments = []
                for left, right in fragments:
                    if allowed_end <= left or allowed_start >= right:
                        next_fragments.append((left, right))
                    else:
                        if left < allowed_start:
                            next_fragments.append((left, allowed_start))
                        if allowed_end < right:
                            next_fragments.append((allowed_end, right))
                fragments = next_fragments
            unexpected_freeze_durations.extend(
                right - left for left, right in fragments
            )
        checks.append(
            QACheck(
                name="freeze_frames",
                passed=max(unexpected_freeze_durations, default=0) <= max_freeze_sec,
                measured=max(unexpected_freeze_durations, default=0),
                expected=f"<= {max_freeze_sec}s",
                detail=(
                    f"intentional low-motion ranges: {expected_freezes}"
                    if expected_freezes else ""
                ),
            )
        )
        read_frames = int(video.get("nb_read_frames") or video.get("nb_frames") or 0)
        expected_frames = round(duration * float(fps))
        checks.append(
            QACheck(
                name="missing_frames",
                passed=read_frames == 0 or abs(read_frames - expected_frames) <= 2,
                measured=read_frames,
                expected=f"{expected_frames} ±2",
            )
        )
        decode_code, decode_log = run_media_diagnostic(path)
        corrupt_markers = re.findall(
            r"(Invalid data|corrupt|Error while decoding|missing picture)", decode_log,
            re.IGNORECASE,
        )
        checks.append(
            QACheck(
                name="corruption",
                passed=decode_code == 0 and not corrupt_markers,
                measured={"returncode": decode_code, "markers": corrupt_markers[:5]},
                expected={"returncode": 0, "markers": []},
            )
        )
        if expected_audio and audio is not None:
            _, loudness_log = run_media_diagnostic(
                path, audio_filter="ebur128=peak=true"
            )
            lufs = _last(loudness_log, r"\bI:\s*(-?[\d.]+)\s*LUFS")
            checks.append(
                QACheck(
                    name="loudness",
                    passed=lufs is not None and abs(lufs - target_lufs) <= lufs_tolerance,
                    measured=lufs,
                    expected=f"{target_lufs} ± {lufs_tolerance} LUFS",
                )
            )
            _, silence_log = run_media_diagnostic(
                path, audio_filter="silencedetect=n=-45dB:d=0.25"
            )
            starts = [
                float(value)
                for value in re.findall(r"silence_start: ([\d.]+)", silence_log)
            ]
            ends = [
                float(value)
                for value in re.findall(r"silence_end: ([\d.]+)", silence_log)
            ]
            expected = expected_silence_ranges or []
            unexpected_durations = []
            for start, end in zip(starts, ends):
                fragments = [(start, end)]
                for allowed_start, allowed_end in expected:
                    next_fragments = []
                    for left, right in fragments:
                        if allowed_end <= left or allowed_start >= right:
                            next_fragments.append((left, right))
                        else:
                            if left < allowed_start:
                                next_fragments.append((left, allowed_start))
                            if allowed_end < right:
                                next_fragments.append((allowed_end, right))
                    fragments = next_fragments
                unexpected_durations.extend(right - left for left, right in fragments)
            checks.append(
                QACheck(
                    name="unexpected_silence",
                    passed=max(unexpected_durations, default=0) <= max_silence_sec,
                    measured=max(unexpected_durations, default=0),
                    expected=f"<= {max_silence_sec}s",
                    detail=(
                        f"intentional ranges: {expected}"
                        if expected else ""
                    ),
                )
            )
        else:
            checks.extend(
                [
                    QACheck(
                        name="loudness",
                        passed=not expected_audio,
                        measured=None,
                        expected="not applicable",
                    ),
                    QACheck(
                        name="unexpected_silence",
                        passed=not expected_audio,
                        measured=None,
                        expected="not applicable",
                    ),
                ]
            )
    except Exception as exc:  # noqa: BLE001
        checks.append(
            QACheck(
                name="corruption",
                passed=False,
                measured=str(exc),
                expected="fully decodable",
            )
        )
    # Exactly the WANT.md §75 gate: file + 12 other checks.
    result = TechnicalQAResult(
        file=str(path),
        passed=len(checks) == 13 and all(check.passed for check in checks),
        checks=checks,
    )
    if conn is not None and render_id is not None:
        with conn:
            conn.execute(
                "INSERT INTO qa_results(render_id,kind,passed,checks_json) VALUES (?,?,?,?)",
                (
                    render_id,
                    "technical",
                    int(result.passed),
                    json.dumps(
                        [check.model_dump(mode="json") for check in checks],
                        ensure_ascii=False,
                    ),
                ),
            )
    return result


__all__ = ["QACheck", "TechnicalQAResult", "run_technical_qa"]
