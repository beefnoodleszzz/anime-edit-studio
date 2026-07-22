"""渲染桥接:镜头级缓存渲染(#3)。

- 逐镜按内容 hash 缓存渲染成段(render-shots.mjs,bundle 一次+复用浏览器),重渲只做变了的镜头。
- concat 段 → 加 BGM 音轨。
- preview=True 走 0.5 等比缩放快速迭代;终版按 EditSpec 自适应画布全分辨率。
Remotion 用 staticFile 加载媒体,故先把源软/硬链到 renderer/public/sources/。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from . import config


def _stage_sources(spec: dict) -> dict:
    pub = config.RENDERER / "public" / "sources"
    pub.mkdir(parents=True, exist_ok=True)
    staged = json.loads(json.dumps(spec))

    def link(src: str) -> str:
        if src.startswith("http"):
            return src
        p = Path(src).resolve()
        target = pub / p.name
        if target.exists() or target.is_symlink():
            target.unlink()
        try:
            os.link(p, target)
        except OSError:
            shutil.copy2(p, target)
        return f"sources/{p.name}"

    for shot in staged.get("shots", []):
        shot["src"] = link(shot["src"])
        if shot.get("matte"):
            shot["matte"] = link(shot["matte"])
    for a in staged.get("audio", []):
        a["src"] = link(a["src"])
    return staged


def render(editspec_path: str, *, out: str | None = None, preview: bool = False,
           smooth: bool = True, allow_unapproved: bool = False) -> str:
    spec_path = Path(editspec_path).resolve()
    if not spec_path.exists():
        raise FileNotFoundError(spec_path)
    spec = json.loads(spec_path.read_text())
    # 正式导出(非 --preview)必须通过素材权利门禁:实际使用的镜头须全部 approved+可商用。
    if not preview and not allow_unapproved:
        from . import decision_loop
        decision_loop.assert_shots_exportable(spec.get("shots", []))
    project = spec_path.parent
    stem = spec_path.stem
    suffix = ".preview" if preview else ""
    # render-shots runs with renderer/ as cwd, so every downstream metadata path
    # must be absolute when callers choose a custom relative output location.
    out_path = (Path(out).expanduser().resolve() if out
                else project / "outputs" / f"{stem}{suffix}.mp4")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # 有真慢镜(speed<1)则先 RIFE 光流平滑(缓存,只跑一次),渲平滑内容、输出仍用原名
    render_spec = spec
    if smooth and any((s.get("speed") or 1) < 0.99 for s in spec["shots"]):
        from . import slowmo
        sm = slowmo.smooth(str(spec_path))["editspec"]
        render_spec = json.loads(Path(sm).read_text())

    staged = _stage_sources(render_spec)
    staged_path = project / f"{stem}.staged.json"
    staged_path.write_text(json.dumps(staged, ensure_ascii=False))

    scale = "0.5" if preview else "1"
    seg_meta = out_path.parent / f".seg_{stem}{suffix}"
    seg_meta.mkdir(parents=True, exist_ok=True)
    cache_dir = config.CACHE / "rendersegs"

    # 逐镜缓存渲染
    subprocess.run(["node", "render-shots.mjs", str(staged_path), str(seg_meta),
                    str(cache_dir), scale], cwd=config.RENDERER, check=True)
    segments = json.loads((seg_meta / "segments.json").read_text())

    ffmpeg = config.tool("ffmpeg")
    listfile = seg_meta / "list.txt"
    listfile.write_text("\n".join(f"file '{s}'" for s in segments))
    silent = seg_meta / "silent.mp4"
    subprocess.run([ffmpeg, "-y", "-v", "error", "-f", "concat", "-safe", "0",
                    "-i", str(listfile), "-c", "copy", str(silent)], check=True)

    # 加 BGM 音轨(按 trim/gain)
    audio = staged.get("audio") or []
    if audio:
        a = audio[0]
        fps = spec["fps"]
        trim = a.get("trim_start_frames", 0) / fps
        bgm = str(config.RENDERER / "public" / a["src"])
        gain = 10 ** (a.get("gain_db", 0) / 20)
        subprocess.run([ffmpeg, "-y", "-v", "error", "-i", str(silent), "-ss", f"{trim:.3f}",
                        "-i", bgm, "-map", "0:v", "-map", "1:a", "-af", f"volume={gain:.4f}",
                        "-c:v", "copy", "-c:a", "aac", "-b:a", "256k", "-shortest",
                        str(out_path)], check=True)
    else:
        shutil.copy(silent, out_path)
    return str(out_path)
