"""RIFE 平滑慢动作:对慢镜(speed<1)预生成光流插帧片段,消除慢放卡顿。

按镜头处理、内容哈希缓存。动漫 limited animation 慎用——仅对显式慢镜生效,
硬切/闪帧镜头(flash)默认跳过。生成 editspec.<name>.smooth.json 供渲染。
"""
from __future__ import annotations

import json
import math
import subprocess
import tempfile
from pathlib import Path

from . import cache, config


def _seg_fps(src: str) -> float:
    ffprobe = config.tool("ffprobe")
    out = subprocess.run([ffprobe, "-v", "error", "-select_streams", "v:0",
                          "-show_entries", "stream=r_frame_rate", "-of", "csv=p=0", src],
                         capture_output=True, text=True, check=True).stdout.strip()
    num, _, den = out.partition("/")
    return float(num) / float(den or 1)


def _rife_slowmo(src: str, in_sec: float, shown_src_sec: float,
                 out_frames: int, out_fps: int) -> str:
    """提取源片段 → RIFE 加密帧 → 编码成时长匹配 out_frames 的顺滑片段。"""
    key = cache.key("slowmo", cache.sha256_file(src), round(in_sec, 3),
                    round(shown_src_sec, 3), out_frames, out_fps,
                    config.get("tools", "rife_model"))
    out = cache.cache_path("slowmo", key, ".mov")
    if out.exists():
        return str(out)

    ffmpeg = config.tool("ffmpeg")
    rife = config.tool("rife")
    model = str(Path(config.load()["tools"]["rife_model"]).expanduser())
    work = Path(tempfile.mkdtemp())
    (work / "in").mkdir()
    (work / "out").mkdir()
    subprocess.run([ffmpeg, "-y", "-v", "error", "-ss", f"{in_sec:.3f}",
                    "-t", f"{max(shown_src_sec, 0.05):.3f}", "-i", src,
                    str(work / "in/%06d.png")], check=True)
    n_in = len(list((work / "in").glob("*.png")))
    if n_in == 0:
        raise RuntimeError(f"片段无帧: {src} @ {in_sec}")
    k = max(2, math.ceil(out_frames / n_in))
    n_out = n_in * k
    subprocess.run([rife, "-i", str(work / "in"), "-o", str(work / "out"),
                    "-m", model, "-n", str(n_out)], check=True)
    # 让 n_out 帧铺满 out_frames/out_fps 秒 → 顺滑慢放
    fps_out = n_out / (out_frames / out_fps)
    subprocess.run([ffmpeg, "-y", "-v", "error", "-framerate", f"{fps_out:.4f}",
                    "-i", str(work / "out/%08d.png"), "-c:v", "prores_videotoolbox",
                    "-profile:v", "3", str(out)], check=True)
    return str(out)


def smooth_spec(spec: dict, *, min_speed: float = 0.95) -> dict:
    """对内存中的 EditSpec 直接做 RIFE 平滑,返回新副本。

    渲染流水线应传入内存中已处理好的 spec,而不是让 slowmo 再从磁盘读旧文件——
    否则内存里刚重解析的母版路径/最新数据会丢失,慢镜版与普通版素材不一致。
    """
    output = json.loads(json.dumps(spec))
    fps = output["fps"]
    for shot in output["shots"]:
        if (shot.get("speed") or 1.0) >= min_speed:
            continue
        if any(e.get("type") == "flash" for e in shot.get("effects", [])):
            continue  # 闪切镜头不插帧
        shown = (shot["duration_in_frames"] / fps) * shot["speed"]
        clip = _rife_slowmo(shot["src"], shot["source_in_sec"], shown,
                            shot["duration_in_frames"], fps)
        shot["src"] = clip
        shot["source_in_sec"] = 0.0
        shot["speed"] = 1.0
    return output


def smooth(editspec_path: str, *, min_speed: float = 0.95) -> dict:
    spec = json.loads(Path(editspec_path).read_text())
    output = smooth_spec(spec, min_speed=min_speed)
    processed = sum(1 for a, b in zip(spec["shots"], output["shots"]) if a.get("src") != b.get("src"))
    p = Path(editspec_path)
    out_path = p.with_name(p.name[: -len(".json")] + ".smooth.json")
    out_path.write_text(json.dumps(output, ensure_ascii=False, indent=2))
    return {"editspec": str(out_path), "smoothed_shots": processed}
