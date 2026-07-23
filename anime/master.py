"""母带与平台版:两遍 EBU R128 loudnorm(-10 LUFS / TP -1.0)+ 平台分辨率导出。"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from . import config

TARGET_I = float(config.get("delivery", "master_lufs", -10.0))
TARGET_TP = float(config.get("delivery", "true_peak_dbfs", -1.0))
TARGET_LRA = 11.0


def _measure(src: str) -> dict:
    ffmpeg = config.tool("ffmpeg")
    out = subprocess.run(
        [ffmpeg, "-i", src, "-af",
         f"loudnorm=I={TARGET_I}:TP={TARGET_TP}:LRA={TARGET_LRA}:print_format=json",
         "-f", "null", "-"],
        capture_output=True, text=True).stderr
    m = re.search(r"\{[^{}]*\"input_i\"[\s\S]*?\}", out)
    if not m:
        raise RuntimeError("loudnorm 测量失败(音频可能为空)")
    return json.loads(m.group(0))


def _loudnorm_af(meas: dict) -> str:
    # 动态 loudnorm + 末级硬限幅(alimiter)保底:loudnorm 的 TP 限制不总生效、AAC 编码还会过冲,
    # 加 limit=0.8(≈-1.9dBFS)给过冲留余量,确保成片真峰值不削波。
    return (
        f"loudnorm=I={TARGET_I}:TP={TARGET_TP}:LRA={TARGET_LRA}"
        f":measured_I={meas['input_i']}:measured_TP={meas['input_tp']}"
        f":measured_LRA={meas['input_lra']}:measured_thresh={meas['input_thresh']}"
        f":offset={meas['target_offset']}:print_format=summary"
        f",alimiter=limit=0.8:level=false"
    )


def _lut_path() -> str | None:
    lut = config.get("look", "lut", "")
    if not lut:
        return None
    p = Path(lut)
    p = p if p.is_absolute() else config.ROOT / p
    return str(p) if p.exists() else None


def master(src: str) -> dict:
    """输出母版 + 保持原画幅的平台版；支持有声或明确静音交付。"""
    src = str(Path(src).resolve())
    ffmpeg = config.tool("ffmpeg")
    outdir = Path(src).parent
    audio_probe = json.loads(subprocess.run(
        [config.tool("ffprobe"), "-v", "error", "-select_streams", "a",
         "-show_entries", "stream=index", "-of", "json", src],
        capture_output=True, text=True, check=True).stdout)
    has_audio = bool(audio_probe.get("streams"))
    meas = _measure(src) if has_audio else None
    audio_args = (["-af", _loudnorm_af(meas), "-c:a", "aac", "-b:a", "256k"]
                  if meas else ["-an"])
    lut = _lut_path()
    lut_vf = f"lut3d='{lut}'" if lut else None

    probe = json.loads(subprocess.run(
        [config.tool("ffprobe"), "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "json", src],
        capture_output=True, text=True, check=True).stdout)
    stream = probe["streams"][0]
    src_w, src_h = int(stream["width"]), int(stream["height"])
    # Scale delivery bitrate with actual pixel count.  A 3072x3840 4:5 master
    # carries more pixels than UHD and should not inherit the old 40 Mbps cap.
    megapixels = src_w * src_h / 1_000_000
    master_mbps = max(40, min(100, round(megapixels * 8)))

    master_path = outdir / "master.mp4"
    master_vf = ["-vf", lut_vf] if lut_vf else []
    master_vc = (["-c:v", "hevc_videotoolbox", "-b:v", f"{master_mbps}M", "-tag:v", "hvc1"]
                 if lut_vf else ["-c:v", "copy"])   # 套 LUT 需重编码
    subprocess.run(
        [ffmpeg, "-y", "-v", "error", "-i", src, *master_vf, *master_vc,
         *audio_args, "-movflags", "+faststart", str(master_path)],
        check=True)

    # Platform copy caps landscape at 1920x1080 and portrait at 1080x1920,
    # while preserving any cinematic/non-standard aspect ratio.
    max_w, max_h = ((1920, 1080) if src_w >= src_h else (1080, 1920))
    ratio = min(1.0, max_w / src_w, max_h / src_h)
    plat_w = max(2, int(src_w * ratio) // 2 * 2)
    plat_h = max(2, int(src_h * ratio) // 2 * 2)
    platform_path = outdir / f"platform_{plat_w}x{plat_h}.mp4"
    plat_vf = f"scale={plat_w}:{plat_h}:flags=lanczos" + (f",{lut_vf}" if lut_vf else "")
    subprocess.run(
        [ffmpeg, "-y", "-v", "error", "-i", src, "-vf", plat_vf,
         "-c:v", "h264_videotoolbox", "-b:v", "12M", "-tag:v", "avc1",
         "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
         *audio_args, "-movflags", "+faststart", str(platform_path)],
        check=True)

    return {
        "has_audio": has_audio,
        "measured_lufs": float(meas["input_i"]) if meas else None,
        "target_lufs": TARGET_I,
        "master_bitrate_mbps": master_mbps,
        "lut": lut,
        "master": str(master_path),
        "platform": str(platform_path),
        "platform_width": plat_w,
        "platform_height": plat_h,
    }
