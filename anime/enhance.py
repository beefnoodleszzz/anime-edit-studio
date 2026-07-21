"""选择性画质增强:RIFE 慢动作补帧 / Real-ESRGAN 动漫超分。内容哈希缓存。

按镜头调用,不全片套用。动漫 limited animation 慎补帧(见 build-plan 待决项)。
"""
from __future__ import annotations

import subprocess
import math
import tempfile
from pathlib import Path

from . import cache, config


def interpolate(src: str, mult: int = 2) -> str:
    """RIFE 补帧 mult 倍(24→48/60)。返回缓存中的成品路径。"""
    src = str(Path(src).resolve())
    key = cache.key("rife", cache.sha256_file(src), mult, config.get("tools", "rife_model"))
    # ProRes is not a valid MP4 payload in ffmpeg; use a QuickTime container.
    out = cache.cache_path("rife", key, ".mov")
    if out.exists():
        return str(out)

    rife = config.tool("rife")
    model = str(Path(config.load()["tools"]["rife_model"]).expanduser())
    ffmpeg = config.tool("ffmpeg")
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        (work / "in").mkdir()
        (work / "out").mkdir()
        subprocess.run([ffmpeg, "-y", "-v", "error", "-i", src,
                        str(work / "in/%06d.png")], check=True)
        n_in = len(list((work / "in").glob("*.png")))
        if n_in == 0:
            raise RuntimeError(f"片段无帧: {src}")
        subprocess.run([rife, "-i", str(work / "in"), "-o", str(work / "out"),
                        "-m", model, "-n", str(n_in * mult)], check=True)
        fps = _fps(src) * mult
        subprocess.run([ffmpeg, "-y", "-v", "error", "-framerate", str(fps),
                        "-i", str(work / "out/%08d.png"), "-c:v", "prores_videotoolbox",
                        "-profile:v", "3", str(out)], check=True)
    return str(out)


def upscale(src: str, scale: int = 2) -> str:
    """Real-ESRGAN 动漫超分。返回缓存路径(帧序列超分后重编 ProRes)。"""
    src = str(Path(src).resolve())
    model = config.get("tools", "realesrgan_model", "realesr-animevideov3-x2")
    key = cache.key("realesrgan", cache.sha256_file(src), scale, model)
    out = cache.cache_path("realesrgan", key, ".mov")
    if out.exists():
        return str(out)

    bin_ = config.tool("realesrgan")
    models = str(Path(config.load()["tools"]["realesrgan_models"]).expanduser())
    ffmpeg = config.tool("ffmpeg")
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        (work / "in").mkdir()
        (work / "out").mkdir()
        subprocess.run([ffmpeg, "-y", "-v", "error", "-i", src,
                        str(work / "in/%06d.png")], check=True)
        subprocess.run([bin_, "-i", str(work / "in"), "-o", str(work / "out"),
                        "-n", model, "-s", str(scale), "-m", models], check=True)
        fps = _fps(src)
        subprocess.run([ffmpeg, "-y", "-v", "error", "-framerate", str(fps),
                        "-i", str(work / "out/%06d.png"), "-c:v", "prores_videotoolbox",
                        "-profile:v", "3", str(out)], check=True)
    return str(out)


def multiplier_for_fps(src: str, target_fps: float = 60.0) -> int:
    """Smallest RIFE multiplier whose temporal density reaches target_fps."""
    return max(1, math.ceil(target_fps / _fps(src)))


def _fps(src: str) -> float:
    ffprobe = config.tool("ffprobe")
    out = subprocess.run([ffprobe, "-v", "error", "-select_streams", "v:0",
                          "-show_entries", "stream=r_frame_rate", "-of", "csv=p=0", src],
                         capture_output=True, text=True, check=True).stdout.strip()
    num, _, den = out.partition("/")
    return float(num) / float(den or 1)
