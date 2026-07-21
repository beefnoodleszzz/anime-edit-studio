"""M7 分层声音设计:程序化合成 SFX(impact/riser/whoosh/subdrop),按成片结构点
对齐 BGM 混音(下拍冲击、甩镜 whoosh、高潮 riser+subdrop)。

音频不影响画面 → 直接把混音 remux 到已渲染视频,免重渲。
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np

from . import config

SR = 44100


def _norm(x: np.ndarray, peak: float = 0.9) -> np.ndarray:
    m = np.abs(x).max()
    return (x / m * peak) if m > 1e-6 else x


def _impact(dur=0.38) -> np.ndarray:
    t = np.linspace(0, dur, int(SR * dur), endpoint=False)
    env = np.exp(-t * 11)
    body = (np.sin(2 * np.pi * 68 * t) + 0.6 * np.sin(2 * np.pi * 44 * t)) * env
    click = np.random.randn(len(t)) * np.exp(-t * 90) * 0.35
    return _norm(body + click)


def _whoosh(dur=0.42) -> np.ndarray:
    t = np.linspace(0, dur, int(SR * dur), endpoint=False)
    n = np.random.randn(len(t))
    n = np.convolve(n, np.ones(30) / 30, mode="same")     # 低通
    env = np.sin(np.pi * t / dur) ** 2
    sweep = np.sin(2 * np.pi * (300 + 1400 * t / dur) * t) * 0.15
    return _norm((n + sweep) * env) * 0.6


def _riser(dur=2.0) -> np.ndarray:
    t = np.linspace(0, dur, int(SR * dur), endpoint=False)
    cres = (t / dur) ** 2
    n = np.random.randn(len(t)) * cres
    f = 200 * (10 ** (1.2 * t / dur))                      # 200→~3k Hz
    tone = np.sin(2 * np.pi * np.cumsum(f) / SR) * cres * 0.5
    return _norm(n + tone) * 0.7


def _subdrop(dur=0.75) -> np.ndarray:
    t = np.linspace(0, dur, int(SR * dur), endpoint=False)
    f = 120 * (35 / 120) ** (t / dur)                      # 120→35 Hz 下沉
    return _norm(np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 2.5))


SFX = {"impact": _impact, "whoosh": _whoosh, "riser": _riser, "subdrop": _subdrop}


def _cache_sfx() -> dict:
    import soundfile as sf
    d = config.ROOT / "kit" / "sfx"
    d.mkdir(parents=True, exist_ok=True)
    out = {}
    for name, fn in SFX.items():
        p = d / f"{name}.wav"
        if not p.exists():
            np.random.seed(hash(name) % 2**31)
            sf.write(p, fn(), SR)
        out[name] = str(p)
    return out


def build(editspec_path: str) -> str:
    """按 editspec 结构点合成 SFX 床 → 与 BGM 混音 → remux 到渲染视频,返回带声音设计的 mp4。"""
    import soundfile as sf

    spec_path = Path(editspec_path)
    spec = json.loads(spec_path.read_text())
    project = spec_path.parent
    stem = spec_path.name[: -len(".json")]
    render_mp4 = project / "outputs" / f"{stem}.mp4"
    if not render_mp4.exists():
        raise FileNotFoundError(f"先渲染:{render_mp4}")
    beatmap = json.loads((project / "beatmap.json").read_text())

    fps = spec["fps"]
    total_sec = spec["duration_in_frames"] / fps
    bed = np.zeros(int(SR * (total_sec + 1)), dtype="float32")
    sfx = {k: sf.read(v)[0].astype("float32") for k, v in _cache_sfx().items()}

    def place(name, t, gain=1.0):
        i = int(max(0, t) * SR)
        s = sfx[name] * gain
        end = min(len(bed), i + len(s))
        bed[i:end] += s[: end - i]

    # 事件:下拍闪切→impact;甩镜→whoosh
    for shot in spec["shots"]:
        t = shot["start_frame"] / fps
        if shot["transition"] == "flash":
            place("impact", t, 0.9)
        elif shot["transition"] in ("whipLeft", "whipRight"):
            place("whoosh", t - 0.06, 0.7)

    # 高潮峰值:能量最高拍 → subdrop + 前置 riser
    energy = beatmap.get("beat_energy") or []
    beats = beatmap["beats"]
    t0 = (spec["audio"][0].get("trim_start_frames", 0) / fps) if spec["audio"] else 0
    if energy:
        pk = int(np.argmax(energy))
        pk_t = beats[pk] - t0
        place("riser", pk_t - 2.0, 0.8)
        place("subdrop", pk_t, 1.0)

    bed_path = project / f"{stem}.sfxbed.wav"
    sf.write(bed_path, _norm(bed, 0.95), SR)

    # 混音:BGM(渲染视频里已有)+ SFX 床,SFX 触发时轻微 duck BGM
    ffmpeg = config.tool("ffmpeg")
    out = project / "outputs" / f"{stem}.sound.mp4"
    subprocess.run([
        ffmpeg, "-y", "-v", "error", "-i", str(render_mp4), "-i", str(bed_path),
        "-filter_complex",
        "[1:a]asplit=2[sfx1][sfx2];"
        "[0:a][sfx1]sidechaincompress=threshold=0.05:ratio=4:attack=5:release=250[duck];"
        "[duck][sfx2]amix=inputs=2:weights=1 0.9:normalize=0[mix]",
        "-map", "0:v", "-map", "[mix]", "-c:v", "copy", "-c:a", "aac", "-b:a", "256k",
        "-shortest", str(out),
    ], check=True)
    return str(out)
