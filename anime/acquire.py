"""互联网取材(集成 agent-reach 的 yt-dlp 后端):搜索 + 下载动漫素材入库。

- source():YouTube/B站 搜索候选(标题/时长/播放/URL)
- fetch():下载视频 → 可选自动 ingest→shots→analyze→embed,并登记版权来源
yt-dlp 同时支持 youtube 与 bilibili URL。
"""
from __future__ import annotations

import json as _json
import subprocess
from pathlib import Path

from . import config

_YTDLP = "yt-dlp"


def source(query: str, platform: str = "youtube", n: int = 10) -> list[dict]:
    target = f"ytsearch{n}:{query}" if platform == "youtube" else query
    out = subprocess.run(
        [_YTDLP, "--flat-playlist", "--dump-json", target],
        capture_output=True, text=True).stdout
    results = []
    for line in out.strip().splitlines():
        try:
            e = _json.loads(line)
        except ValueError:
            continue
        results.append({
            "id": e.get("id"), "title": e.get("title"),
            "duration_s": _num(e.get("duration")), "views": _num(e.get("view_count")),
            "url": e.get("webpage_url") or e.get("url"),
        })
    return results


def fetch(url: str, *, ingest: bool = True, max_height: int = 1080,
          min_height: int = 1080) -> dict:
    if min_height < 1080:
        raise ValueError("交付素材最低分辨率为 1080p；min_height 不得低于 1080")
    if max_height < min_height:
        raise ValueError("max_height 不能小于 min_height")
    downloads = config.LIBRARY / "downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    # Never silently fall back to a low-resolution rendition.  AI enhancement
    # may improve compression artifacts, but it cannot make a 480p stream a
    # delivery-grade 1080p source.
    fmt = f"bv*[height>={min_height}][height<={max_height}][ext=mp4]+ba/b[height>={min_height}][height<={max_height}]/b[height>={min_height}][height<={max_height}]"
    tmpl = str(downloads / "%(id)s.%(ext)s")
    subprocess.run(
        [_YTDLP, "-f", fmt, "--merge-output-format", "mp4",
         "-o", tmpl, "--no-playlist", url],
        check=True)
    # 找刚下载的文件
    files = sorted(downloads.glob("*.mp4"), key=lambda p: p.stat().st_mtime)
    if not files:
        raise RuntimeError("下载失败,未找到 mp4")
    path = str(files[-1])

    result: dict = {"path": path, "url": url}
    if ingest:
        from . import analyze, embed, ingest as ingest_mod, rights, shots, tag
        asset = ingest_mod.ingest(path)
        aid = asset["id"]
        shots.detect(aid)
        analyze.analyze(aid)
        for step in (lambda: embed.embed_asset(aid), lambda: tag.tag_asset(aid)):
            try:
                step()               # 需 .[ml];未装则跳过
            except Exception:
                pass
        rights.set_rights(aid, source=url, license="网络素材,授权未确认", commercial=False)
        result.update(asset_id=aid, ingested=True)
    return result


def _num(s: str):
    try:
        return int(float(s))
    except (ValueError, TypeError):
        return None
