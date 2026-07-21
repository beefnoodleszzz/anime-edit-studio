"""导入素材:sha256 + ffprobe 元数据 + 1080p 代理(VideoToolbox)。"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from . import cache, config, db


def _ffprobe(path: str) -> dict:
    ffprobe = config.tool("ffprobe")
    out = subprocess.run(
        [ffprobe, "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height,r_frame_rate,codec_name",
         "-show_entries", "format=duration", "-of", "json", path],
        capture_output=True, text=True, check=True,
    ).stdout
    data = json.loads(out)
    stream = data.get("streams", [{}])[0]
    num, _, den = stream.get("r_frame_rate", "0/1").partition("/")
    fps = float(num) / float(den) if float(den or 0) else 0.0
    return {
        "width": stream.get("width"),
        "height": stream.get("height"),
        "codec": stream.get("codec_name"),
        "fps": round(fps, 3),
        "duration": float(data.get("format", {}).get("duration", 0.0)),
    }


def _make_proxy(src: str, asset_id: str) -> str:
    ffmpeg = config.tool("ffmpeg")
    height = config.get("proxy", "edit_height", 1080)
    dst = config.PROXIES / f"{asset_id}.mp4"
    if dst.exists():
        return str(dst)
    subprocess.run(
        [ffmpeg, "-y", "-v", "error", "-i", src,
         "-vf", f"scale=-2:{height}", "-c:v", "h264_videotoolbox", "-b:v", "8M",
         "-c:a", "aac", "-b:a", "160k", str(dst)],
        check=True,
    )
    return str(dst)


def ingest(path: str) -> dict:
    """导入一个视频,返回 asset 记录。"""
    src = str(Path(path).expanduser().resolve())
    if not Path(src).exists():
        raise FileNotFoundError(src)
    sha = cache.sha256_file(src)
    asset_id = sha[:12]
    meta = _ffprobe(src)
    proxy = _make_proxy(src, asset_id)
    asset = {"id": asset_id, "path": src, "sha256": sha, "proxy_path": proxy, **meta}
    conn = db.connect()
    db.upsert_asset(conn, asset)
    conn.close()
    return asset
