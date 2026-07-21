"""END 卡尾字:在成片尾部叠加渐显标题 + 轻微压暗,收束混剪(竞品同款硬收尾观感)。

本机 ffmpeg 未编 drawtext(无 libfreetype),故用 PIL 生成一张「半透明压暗底 + 白色
END 字」的 RGBA 图,再用 ffmpeg overlay + fade(alpha)在尾部渐显。纯后处理,不进渲染
缓存也不改 EditSpec——套在 master/platform 成片上即可。
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from . import config

_FONTS = [
    "/System/Library/Fonts/HelveticaNeue.ttc",
    "/System/Library/Fonts/Helvetica.ttc",
    "/System/Library/Fonts/Supplemental/Futura.ttc",
    "/System/Library/Fonts/SFNS.ttf",
]


def _font_path() -> str:
    for f in _FONTS:
        if Path(f).exists():
            return f
    raise RuntimeError("未找到系统字体,无法绘制 END 卡")


def _probe(src: str) -> tuple[float, int, int]:
    ffprobe = config.tool("ffprobe")
    out = subprocess.run(
        [ffprobe, "-v", "error", "-select_streams", "v:0",
         "-show_entries", "format=duration:stream=width,height",
         "-of", "default=nokey=1:noprint_wrappers=1", src],
        capture_output=True, text=True, check=True).stdout.split()
    w, h, dur = int(out[0]), int(out[1]), float(out[2])
    return dur, w, h


def _render_card(text: str, w: int, h: int, dim: float, out_png: str) -> None:
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new("RGBA", (w, h), (0, 0, 0, int(255 * dim)))   # 半透明压暗底
    draw = ImageDraw.Draw(img)
    spaced = "  ".join(text.upper())                             # 字距拉开
    font = ImageFont.truetype(_font_path(), max(int(h / 16), 12))
    box = draw.textbbox((0, 0), spaced, font=font)
    tw, th = box[2] - box[0], box[3] - box[1]
    draw.text(((w - tw) / 2 - box[0], (h - th) / 2 - box[1]), spaced,
              font=font, fill=(255, 255, 255, 255))
    img.save(out_png)


def add(src: str, text: str = "END", hold: float = 2.5, dim: float = 0.45,
        out: str | None = None) -> dict:
    """尾部 hold 秒内把「压暗+END 字」整体从透明渐显到实,做硬收尾。"""
    src = str(Path(src).resolve())
    ffmpeg = config.tool("ffmpeg")
    dur, w, h = _probe(src)
    t0 = max(dur - hold, 0.0)

    card = str(Path(src).with_suffix(".endcard.png"))
    _render_card(text, w, h, dim, card)

    fc = (f"[1:v]format=rgba,fade=t=in:st={t0:.3f}:d={hold}:alpha=1[ov];"
          f"[0:v][ov]overlay=0:0:format=auto[v]")
    out_path = out or str(Path(src).with_name(Path(src).stem + "_end" + Path(src).suffix))
    subprocess.run(
        [ffmpeg, "-y", "-v", "error", "-i", src, "-loop", "1", "-i", card,
         "-filter_complex", fc, "-map", "[v]", "-map", "0:a?", "-shortest",
         "-c:v", "h264_videotoolbox", "-b:v", "12M", "-tag:v", "avc1",
         "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
         "-c:a", "copy", "-movflags", "+faststart", out_path],
        check=True)
    Path(card).unlink(missing_ok=True)
    return {"output": out_path, "text": text, "fade_from_s": round(t0, 2),
            "duration_s": round(dur, 2)}
