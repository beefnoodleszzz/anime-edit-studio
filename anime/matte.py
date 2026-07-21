"""主体遮罩:rembg(isnet-anime 动漫模型)分割人物,供"主体高亮/特效藏人物后"。

M4 为静态遮罩(取镜头中间帧,适合短镜头/主体基本静止);逐帧遮罩视频是后续项。
遮罩为灰度 PNG(白=主体),渲染端用作 CSS mask。
"""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from . import config, db

_session = None


def _get_session():
    global _session
    if _session is None:
        from rembg import new_session
        _session = new_session("isnet-anime")
    return _session


def matte_shot(shot_id: str, width: int = 720) -> str:
    from PIL import Image
    from rembg import remove

    conn = db.connect()
    shot = conn.execute("SELECT * FROM shots WHERE id=?", (shot_id,)).fetchone()
    if not shot:
        conn.close()
        raise ValueError(f"未找到镜头: {shot_id}")
    asset = db.asset_by_id(conn, shot["asset_id"])
    conn.close()
    source = asset["proxy_path"] or asset["path"]
    mid = (shot["start_sec"] + shot["end_sec"]) / 2

    ffmpeg = config.tool("ffmpeg")
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        frame = tmp.name
    subprocess.run([ffmpeg, "-y", "-v", "error", "-ss", f"{mid:.3f}", "-i", source,
                    "-frames:v", "1", "-vf", f"scale={width}:-2", frame], check=True)

    mask = remove(Image.open(frame).convert("RGB"), session=_get_session(), only_mask=True)
    # 存成 RGBA(alpha=主体),渲染端 CSS mask 走 alpha 通道
    rgba = Image.new("RGBA", mask.size, (255, 255, 255, 0))
    rgba.putalpha(mask.convert("L"))
    out = config.KEYFRAMES / shot["asset_id"] / f"shot_{shot['idx']:04d}_matte.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    rgba.save(out)
    Path(frame).unlink(missing_ok=True)
    return str(out)
