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
import tempfile
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
           smooth: bool = True, relink_master: bool = True) -> str:
    spec_path = Path(editspec_path).resolve()
    if not spec_path.exists():
        raise FileNotFoundError(spec_path)
    spec = json.loads(spec_path.read_text())
    # 正式导出(非 --preview)按 shot.id 从 DB 回源到本地母版路径,让成片用最高画质原片
    # 而不是代理文件。不做权利拦截。finalize 渲染增强后(restore/RIFE/超分)的 spec 时传
    # relink_master=False,否则回链会把超分后的 ProRes 源覆盖回母版、白做超分。
    if not preview and relink_master:
        from . import decision_loop
        spec = decision_loop.resolve_master_sources(spec)
    project = spec_path.parent
    stem = spec_path.stem
    suffix = ".preview" if preview else ""
    # render-shots runs with renderer/ as cwd, so every downstream metadata path
    # must be absolute when callers choose a custom relative output location.
    out_path = (Path(out).expanduser().resolve() if out
                else project / "outputs" / f"{stem}{suffix}.mp4")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # 有真慢镜(speed<1)则先 RIFE 光流平滑(缓存,只跑一次),渲平滑内容、输出仍用原名。
    # 直接处理内存中的 spec(已重解析母版路径),不再从磁盘重读旧 EditSpec。
    render_spec = spec
    if smooth and any((s.get("speed") or 1) < 0.99 for s in spec["shots"]):
        from . import slowmo
        render_spec = slowmo.smooth_spec(spec)

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

    # 预览阶段套签名 LUT,让 Phase A 看到与成片一致的调色(finalize 走 preview=False,
    # 只在 master 套 LUT → 两条路径互斥,不会双重调色)。终版渲染保持中性、由 master 负责。
    from .master import _lut_path
    lut = _lut_path() if preview else None
    lut_vf = ["-vf", f"lut3d='{lut}'"] if lut else []
    lut_vc = ["-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p"] if lut else ["-c:v", "copy"]

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
                        *lut_vf, *lut_vc, "-c:a", "aac", "-b:a", "256k", "-shortest",
                        str(out_path)], check=True)
    elif lut:
        subprocess.run([ffmpeg, "-y", "-v", "error", "-i", str(silent),
                        *lut_vf, *lut_vc, str(out_path)], check=True)
    else:
        shutil.copy(silent, out_path)
    return str(out_path)


def _draft_proxy_sources(spec: dict) -> dict:
    """按 shot.id 把 src 换成低画质代理(draft 求快),解析不到的保持原 src。"""
    from . import db

    out = json.loads(json.dumps(spec))
    conn = db.connect()
    try:
        for shot in out.get("shots", []):
            row = conn.execute(
                "SELECT a.proxy_path, a.path FROM shots s JOIN assets a ON a.id=s.asset_id WHERE s.id=?",
                (shot.get("id"),),
            ).fetchone()
            if row:
                shot["src"] = row["proxy_path"] or row["path"] or shot["src"]
    finally:
        conn.close()
    return out


def draft(editspec_path: str, *, out: str | None = None, long_edge: int = 720) -> str:
    """最快迭代渲染:纯 ffmpeg 拼接代理低分辨率片段,不走 Remotion(免 webpack 打包/Chrome
    与 40GB 临时件),不做特效/转场/超分/补帧。只为判断选片、构图裁切、节奏卡点是否成立。
    """
    spec_path = Path(editspec_path).resolve()
    if not spec_path.exists():
        raise FileNotFoundError(spec_path)
    spec = _draft_proxy_sources(json.loads(spec_path.read_text()))
    fps = spec["fps"]
    sw, sh = spec["width"], spec["height"]
    scale = long_edge / max(sw, sh)
    dw, dh = max(2, int(sw * scale) // 2 * 2), max(2, int(sh * scale) // 2 * 2)
    target_ar = dw / dh

    ffmpeg = config.tool("ffmpeg")
    project = spec_path.parent
    stem = spec_path.stem
    out_path = (Path(out).expanduser().resolve() if out
                else project / "outputs" / f"{stem}.draft.mp4")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="anime-draft-") as tmp:
        root = Path(tmp)
        segments = []
        for i, shot in enumerate(spec["shots"]):
            seg = root / f"{i:04d}.mp4"
            nframes = max(int(shot["duration_in_frames"]), 1)
            dur = nframes / fps
            speed = float(shot.get("speed") or 1.0)
            # 源侧按 speed 消耗(ramp 用线性近似),窗口边界与成片一致,慢镜不再越界
            span = max(dur * speed, 1 / fps)
            pts = f"setpts=PTS/{speed:.4f}," if abs(speed - 1.0) > 1e-3 else ""
            if (shot.get("fill_mode") or "crop") == "crop":
                # 覆盖画布后按 reframe_x 横向偏移裁切,尽量还原成片构图取舍。
                rx = max(-1.0, min(float(shot.get("reframe_x") or 0.0), 1.0))
                vf = (f"{pts}scale={dw}:{dh}:force_original_aspect_ratio=increase,"
                      f"crop={dw}:{dh}:(iw-{dw})/2+{rx}*(iw-{dw})/2:(ih-{dh})/2,fps={fps}")
            else:  # fit:完整画面居中黑边(draft 用黑边替代毛玻璃背景求快)
                vf = (f"{pts}scale={dw}:{dh}:force_original_aspect_ratio=decrease,"
                      f"pad={dw}:{dh}:(ow-iw)/2:(oh-ih)/2:black,fps={fps}")
            subprocess.run(
                [ffmpeg, "-y", "-v", "error", "-ss", f"{shot['source_in_sec']:.3f}",
                 "-t", f"{span:.3f}", "-i", shot["src"], "-vf", vf, "-an",
                 # -frames:v 锁定每段精确帧数,杜绝逐段取整误差累计成漂移
                 "-frames:v", str(nframes),
                 "-r", str(fps), "-g", str(fps), "-c:v", "libx264",
                 "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(seg)],
                check=True,
            )
            segments.append(seg)
        listfile = root / "list.txt"
        listfile.write_text("\n".join(f"file '{s.as_posix()}'" for s in segments))
        silent = root / "silent.mp4"
        subprocess.run([ffmpeg, "-y", "-v", "error", "-f", "concat", "-safe", "0",
                        "-i", str(listfile), "-c", "copy", str(silent)], check=True)

        audio = spec.get("audio") or []
        if audio:
            a = audio[0]
            trim = a.get("trim_start_frames", 0) / fps
            bgm = a["src"]
            gain = 10 ** (a.get("gain_db", 0) / 20)
            subprocess.run([ffmpeg, "-y", "-v", "error", "-i", str(silent), "-ss", f"{trim:.3f}",
                            "-i", bgm, "-map", "0:v", "-map", "1:a", "-af", f"volume={gain:.4f}",
                            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest",
                            str(out_path)], check=True)
        else:
            shutil.copy(silent, out_path)
    return str(out_path)
