"""Real-ESRGAN 真超分接管线(选择性):对指定镜头的源片段 2× 动漫超分,
重写 EditSpec 指向超分片段。当前"4K"= 拉伸 1080p 代理,本步给关键镜头补真细节。

超分慢,按需用于 hero/climax 镜头,不建议全片套用。生成 editspec.<name>.sr.json。
"""
from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

from . import cache, config, enhance


def _extract_segment(src: str, in_sec: float, dur_sec: float) -> str:
    ffmpeg = config.tool("ffmpeg")
    key = cache.key("srseg", cache.sha256_file(src), round(in_sec, 3), round(dur_sec, 3))
    out = cache.cache_path("srseg", key, ".mp4")
    if out.exists():
        return str(out)
    subprocess.run([ffmpeg, "-y", "-v", "error", "-ss", f"{in_sec:.3f}",
                    "-t", f"{max(dur_sec, 0.1):.3f}", "-i", src,
                    "-c:v", "libx264", "-crf", "12", "-pix_fmt", "yuv420p", str(out)],
                   check=True)
    return str(out)


def upscale_editspec(editspec_path: str, only_ids: list[str] | None = None,
                     scale: int = 2) -> dict:
    spec = json.loads(Path(editspec_path).read_text())
    fps = spec["fps"]
    done = 0
    for shot in spec["shots"]:
        if only_ids and shot["id"] not in only_ids:
            continue
        shown = (shot["duration_in_frames"] / fps) * shot["speed"]
        seg = _extract_segment(shot["src"], shot["source_in_sec"], shown + 0.15)
        up = enhance.upscale(seg, scale=scale)   # realesr-animevideov3 → ProRes,同帧同时长
        shot["src"] = up
        shot["source_in_sec"] = 0.0
        done += 1

    p = Path(editspec_path)
    out_path = p.with_name(p.name[: -len(".json")] + ".sr.json")
    out_path.write_text(json.dumps(spec, ensure_ascii=False, indent=2))
    return {"editspec": str(out_path), "upscaled_shots": done}
