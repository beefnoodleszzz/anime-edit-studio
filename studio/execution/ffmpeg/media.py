"""Centralised ffprobe/ffmpeg subprocess boundary."""
from __future__ import annotations

import json
import subprocess
import os
import re
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

import numpy as np

DELIVERY_TARGET_LUFS = -11.0


class MediaToolError(RuntimeError):
    pass


@dataclass(frozen=True)
class MediaProbe:
    width: int
    height: int
    fps_num: int
    fps_den: int
    duration_sec: float
    codec: str
    has_audio: bool
    sample_rate: int | None


def _run(command: list[str], *, binary: bool = False) -> subprocess.CompletedProcess:
    result = subprocess.run(
        command,
        capture_output=True,
        text=not binary,
        check=False,
    )
    if result.returncode:
        stderr = (
            result.stderr.decode("utf-8", errors="replace")
            if isinstance(result.stderr, bytes) else result.stderr
        )
        raise MediaToolError(f"{command[0]} 失败 ({result.returncode}): {stderr[-2000:]}")
    return result


def probe_media(path: Path) -> MediaProbe:
    result = _run(
        [
            "ffprobe", "-v", "error", "-show_streams", "-show_format",
            "-of", "json", str(path),
        ]
    )
    payload = json.loads(result.stdout)
    video = next(
        (stream for stream in payload.get("streams", []) if stream.get("codec_type") == "video"),
        None,
    )
    if video is None:
        raise MediaToolError(f"没有视频流: {path}")
    audio = next(
        (stream for stream in payload.get("streams", []) if stream.get("codec_type") == "audio"),
        None,
    )
    rate = Fraction(video.get("avg_frame_rate") or video.get("r_frame_rate") or "0/1")
    duration = float(
        payload.get("format", {}).get("duration")
        or video.get("duration")
        or 0
    )
    return MediaProbe(
        width=int(video.get("width") or 0),
        height=int(video.get("height") or 0),
        fps_num=rate.numerator,
        fps_den=rate.denominator,
        duration_sec=duration,
        codec=str(video.get("codec_name") or ""),
        has_audio=audio is not None,
        sample_rate=int(audio["sample_rate"]) if audio and audio.get("sample_rate") else None,
    )


def probe_media_json(path: Path, *, count_frames: bool = False) -> dict:
    command = [
        "ffprobe", "-v", "error", "-show_streams", "-show_format",
        "-of", "json",
    ]
    if count_frames:
        command.insert(-2, "-count_frames")
    result = _run([*command, str(path)])
    return json.loads(result.stdout)


def run_media_diagnostic(
    path: Path,
    *,
    video_filter: str | None = None,
    audio_filter: str | None = None,
) -> tuple[int, str]:
    """Decode media and return diagnostics; callers interpret the log."""
    command = ["ffmpeg", "-v", "info", "-i", str(path)]
    if video_filter:
        command.extend(["-vf", video_filter])
    if audio_filter:
        command.extend(["-af", audio_filter])
    command.extend(["-f", "null", "-"])
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    return result.returncode, result.stderr


def measure_integrated_lufs(path: Path) -> float:
    """Measure EBU R128 integrated loudness using the same basis as Technical QA."""
    code, log = run_media_diagnostic(path, audio_filter="ebur128=peak=true")
    if code:
        raise MediaToolError(f"loudness 测量失败: {path}")
    values = re.findall(r"\bI:\s*(-?[\d.]+)\s*LUFS", log)
    if not values:
        raise MediaToolError(f"未找到 integrated LUFS: {path}")
    return float(values[-1])


def decode_audio_mono(path: Path, *, sample_rate: int = 8000) -> np.ndarray:
    """Decode to mono float32 in [-1,1]. Empty array means no audio stream."""
    probe = probe_media(path)
    if not probe.has_audio:
        return np.empty(0, dtype=np.float32)
    result = _run(
        [
            "ffmpeg", "-v", "error", "-i", str(path), "-vn",
            "-ac", "1", "-ar", str(sample_rate), "-f", "s16le", "pipe:1",
        ],
        binary=True,
    )
    samples = np.frombuffer(result.stdout, dtype="<i2")
    return samples.astype(np.float32) / 32768.0


def create_proxy(source: Path, target: Path, *, height: int = 1080) -> Path:
    """Atomically create an H.264 edit proxy; VideoToolbox with software fallback."""
    if target.exists():
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.stem}.partial{target.suffix}")
    temporary.unlink(missing_ok=True)
    common = [
        "ffmpeg", "-y", "-v", "error", "-i", str(source),
        "-map", "0:v:0", "-map", "0:a:0?", "-vf", f"scale=-2:{height}",
        "-c:a", "aac", "-b:a", "160k",
    ]
    errors = []
    for encoder in (
        ["-c:v", "h264_videotoolbox", "-b:v", "8M"],
        ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p"],
    ):
        result = subprocess.run(
            [*common, *encoder, str(temporary)],
            capture_output=True, text=True, check=False,
        )
        if result.returncode == 0 and temporary.is_file():
            os.replace(temporary, target)
            return target
        errors.append(result.stderr[-1500:])
        temporary.unlink(missing_ok=True)
    raise MediaToolError("代理生成失败:\n" + "\n".join(errors))


def create_shot_preview(
    source: Path,
    target: Path,
    *,
    start_sec: float,
    end_sec: float,
    height: int = 540,
) -> Path:
    """Atomically render a bounded review clip (tool output, not final render)."""
    if start_sec < 0 or end_sec <= start_sec:
        raise ValueError("preview 时间范围无效")
    if target.exists():
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.stem}.partial{target.suffix}")
    temporary.unlink(missing_ok=True)
    _run(
        [
            "ffmpeg", "-y", "-v", "error", "-ss", f"{start_sec:.6f}",
            "-i", str(source), "-t", f"{end_sec - start_sec:.6f}",
            "-vf", f"scale=-2:{height}", "-c:v", "libx264", "-preset", "veryfast",
            "-crf", "20", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart", str(temporary),
        ]
    )
    if not temporary.is_file():
        raise MediaToolError("preview 未生成")
    os.replace(temporary, target)
    return target


def prebake_audio(
    source: Path,
    target: Path,
    *,
    source_in_sec: float = 0.0,
    duration_sec: float | None = None,
    gain_db: float = 0.0,
) -> Path:
    """Materialize trim/gain automation before Resolve (Fairlight P17 fallback)."""
    if source_in_sec < 0 or (duration_sec is not None and duration_sec <= 0):
        raise ValueError("audio trim 时间范围无效")
    if target.exists():
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.stem}.partial{target.suffix}")
    temporary.unlink(missing_ok=True)
    command = [
        "ffmpeg", "-y", "-v", "error", "-ss", f"{source_in_sec:.6f}",
        "-i", str(source), "-vn",
    ]
    if duration_sec is not None:
        command.extend(["-t", f"{duration_sec:.6f}"])
    command.extend(
        [
            "-af", f"volume={gain_db:.6f}dB",
            "-ar", "48000", "-ac", "2", "-c:a", "pcm_s24le", str(temporary),
        ]
    )
    _run(command)
    if not temporary.is_file():
        raise MediaToolError("audio 预烘焙未生成")
    os.replace(temporary, target)
    return target


__all__ = [
    "MediaProbe",
    "MediaToolError",
    "decode_audio_mono",
    "create_proxy",
    "create_shot_preview",
    "prebake_audio",
    "probe_media_json",
    "probe_media",
    "run_media_diagnostic",
    "measure_integrated_lufs",
]
