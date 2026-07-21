"""自动 QA:规格 / 黑帧 / 响度(EBU R128) → report.json。"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from . import config


def qa(path: str, *, expected_width: int | None = None,
       expected_height: int | None = None,
       expected_audio: bool = True) -> dict:
    src = str(Path(path).resolve())
    ffprobe = config.tool("ffprobe")
    ffmpeg = config.tool("ffmpeg")

    probe = json.loads(subprocess.run(
        [ffprobe, "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height,r_frame_rate,codec_name,nb_frames",
         "-show_entries", "format=duration", "-of", "json", src],
        capture_output=True, text=True, check=True).stdout)
    stream = probe.get("streams", [{}])[0]

    # d=0.3:只报≥0.3s 的黑场(真 gap);快剪里的瞬时暗帧属正常内容不误报
    black = subprocess.run(
        [ffmpeg, "-i", src, "-vf", "blackdetect=d=0.3:pic_th=0.98", "-an", "-f", "null", "-"],
        capture_output=True, text=True).stderr
    black_frames = len(re.findall(r"black_start", black))

    # 冻结帧检测(异常静止 ≥0.5s)
    freeze = subprocess.run(
        [ffmpeg, "-i", src, "-vf", "freezedetect=n=0.003:d=0.5", "-an", "-f", "null", "-"],
        capture_output=True, text=True).stderr
    freeze_segments = len(re.findall(r"freeze_start", freeze))

    audio_probe = json.loads(subprocess.run(
        [ffprobe, "-v", "error", "-select_streams", "a", "-show_entries", "stream=index",
         "-of", "json", src], capture_output=True, text=True, check=True).stdout)
    has_audio = bool(audio_probe.get("streams"))
    loud = ""
    if has_audio:
        loud = subprocess.run(
            [ffmpeg, "-i", src, "-af", "ebur128=peak=true", "-f", "null", "-"],
            capture_output=True, text=True).stderr
    lufs = _last(loud, r"\bI:\s*(-?[\d.]+)\s*LUFS")
    tp = _last(loud, r"Peak:\s*(-?[\d.]+)")

    dimensions_match = True
    if expected_width is not None or expected_height is not None:
        if expected_width is None or expected_height is None:
            raise ValueError("expected_width 与 expected_height 必须同时提供")
        dimensions_match = (stream.get("width") == expected_width and
                            stream.get("height") == expected_height)
    report = {
        "file": src,
        "width": stream.get("width"), "height": stream.get("height"),
        "fps": stream.get("r_frame_rate"), "codec": stream.get("codec_name"),
        "duration": float(probe.get("format", {}).get("duration", 0)),
        "has_audio": has_audio,
        "black_segments": black_frames,
        "integrated_lufs": lufs, "true_peak_dbfs": tp,
        "checks": {
            "resolution_ok": dimensions_match,
            "audio_ok": has_audio == expected_audio,
            "loudness_ok": ((lufs is not None and -16 <= lufs <= -12)
                            if expected_audio else not has_audio),
            "no_clip": tp is None or tp <= -0.1,   # 真峰值不削波
        },
        # 黑场/冻结为告警供人工复核:自动检测无法可靠区分故障 gap 与风格化暗帧/暗剪影/hero 长握。
        # 结构性开场黑场已由"首镜锚 0"从根杜绝。
        "warnings": {"black_segments": black_frames, "freeze_segments": freeze_segments},
    }
    report["pass"] = all(report["checks"].values())
    out = Path(src).with_name("qa-report.json")
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    report["report_path"] = str(out)
    return report


def _last(text: str, pattern: str) -> float | None:
    m = re.findall(pattern, text)
    return float(m[-1]) if m else None
