"""M7 分层声音设计:程序化合成 SFX(impact/riser/whoosh/subdrop),按成片结构点
对齐 BGM 混音(下拍冲击、甩镜 whoosh、高潮 riser+subdrop)。

音频不影响画面 → 直接把混音 remux 到已渲染视频,免重渲。
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import numpy as np

from . import cache, config

SR = 44100


def _norm(x: np.ndarray, peak: float = 0.9) -> np.ndarray:
    m = np.abs(x).max()
    return (x / m * peak) if m > 1e-6 else x


def _limit(x: np.ndarray, peak: float = .95) -> np.ndarray:
    maximum = float(np.abs(x).max()) if len(x) else 0
    return x * (peak / maximum) if maximum > peak else x


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


def capabilities() -> dict:
    ffmpeg = config.tool("ffmpeg")
    filters = subprocess.run([ffmpeg, "-hide_banner", "-filters"],
                             capture_output=True, text=True).stdout
    return {
        "source_audio_bed": True,
        "rubberband": " rubberband " in filters,
        "demucs": bool(shutil.which("demucs")),
        "policy": {
            "rubberband": "available" if " rubberband " in filters else "atempo_fallback",
            "demucs": "available" if shutil.which("demucs") else "not_installed",
        },
    }


def _atempo(speed: float) -> str:
    """Build a legal atempo chain for arbitrary positive speed."""
    if speed <= 0:
        raise ValueError("speed 必须大于 0")
    factors = []
    while speed > 2:
        factors.append(2.0)
        speed /= 2
    while speed < .5:
        factors.append(.5)
        speed /= .5
    factors.append(speed)
    return ",".join(f"atempo={factor:.6f}" for factor in factors)


def _source_audio_bed(spec: dict, project: Path, stem: str,
                      total_sec: float, gain: float) -> tuple[Path, int]:
    import soundfile as sf
    ffmpeg = config.tool("ffmpeg")
    fps = float(spec["fps"])
    bed = np.zeros(int(SR * (total_sec + 1)), dtype="float32")
    used = 0
    for shot in spec.get("shots") or []:
        shown = float(shot["duration_in_frames"]) / fps
        speed = float(shot.get("speed") or 1)
        source_duration = shown * speed
        source = str(shot.get("source_audio_src") or shot["src"])
        source_in = float(shot.get("source_audio_in_sec",
                                   shot.get("source_in_sec") or 0))
        key = cache.key("source-audio", cache.sha256_file(source),
                        round(source_in, 3),
                        round(source_duration, 3), round(speed, 4))
        clip = cache.cache_path("source-audio", key, ".wav")
        if not clip.exists():
            result = subprocess.run([
                ffmpeg, "-y", "-v", "error",
                "-ss", f"{source_in:.3f}",
                "-t", f"{source_duration:.3f}", "-i", source,
                "-vn", "-ac", "1", "-ar", str(SR), "-af", _atempo(speed),
                "-c:a", "pcm_s16le", str(clip),
            ], capture_output=True, text=True)
            if result.returncode:
                clip.unlink(missing_ok=True)
                continue
        audio, sample_rate = sf.read(clip)
        if sample_rate != SR:
            continue
        if audio.ndim > 1:
            audio = audio.mean(axis=1)
        audio = audio.astype("float32")
        target = int(shown * SR)
        audio = audio[:target]
        start = int(float(shot["start_frame"]) / fps * SR)
        end = min(len(bed), start + len(audio))
        if end > start:
            bed[start:end] += audio[:end - start] * gain
            used += 1
    path = project / f"{stem}.sourcebed.wav"
    sf.write(path, _limit(bed, .95), SR)
    return path, used


def build(editspec_path: str, *, source_suffix: str = "",
          include_source_audio: bool = True, source_gain: float = .28) -> str:
    """按 editspec 结构点合成 SFX 床 → 与 BGM 混音 → remux 到渲染视频,返回带声音设计的 mp4。

    source_suffix="" 针对终版渲染 {stem}.mp4;传 ".preview" 可给 0.5 预览配同一套声音
    设计,让 Phase A 就能听到成片音效(富预览),而非等 finalize。
    """
    import soundfile as sf

    spec_path = Path(editspec_path)
    spec = json.loads(spec_path.read_text())
    project = spec_path.parent
    stem = spec_path.name[: -len(".json")]
    render_mp4 = project / "outputs" / f"{stem}{source_suffix}.mp4"
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

    # 事件床:t0 对齐绝对音频时间以判定下拍
    t0 = (spec["audio"][0].get("trim_start_frames", 0) / fps) if spec["audio"] else 0
    downbeats = beatmap.get("downbeats") or []

    def on_downbeat(abs_t: float) -> bool:
        return any(abs(abs_t - d) < 0.05 for d in downbeats)

    for shot in spec["shots"]:
        t = shot["start_frame"] / fps                      # 相对成片时间
        tr = shot["transition"]
        # 冲击低频:闪切=硬冲击;落在下拍的任意硬切=轻冲击脉冲,让低频锁拍而不只闪切响
        if tr == "flash":
            place("impact", t, 0.9)
        elif on_downbeat(t0 + t):
            place("impact", t, 0.5)
        if tr in ("whipLeft", "whipRight"):
            place("whoosh", t - 0.06, 0.7)
        # 真慢镜入点:时间弯曲的 whoosh + 轻 sub(hero 落地那一下的空气感)
        if (shot.get("speed") or 1.0) < 0.9:
            place("whoosh", t - 0.05, 0.5)
            place("subdrop", t, 0.5)

    # 高潮峰值:能量最高拍 → subdrop + 前置 riser
    energy = beatmap.get("beat_energy") or []
    beats = beatmap["beats"]
    if energy:
        pk = int(np.argmax(energy))
        pk_t = beats[pk] - t0
        place("riser", pk_t - 2.0, 0.8)
        place("subdrop", pk_t, 1.0)

    bed_path = project / f"{stem}{source_suffix}.sfxbed.wav"
    sf.write(bed_path, _norm(bed, 0.95), SR)
    source_path = None
    source_shots = 0
    if include_source_audio:
        source_path, source_shots = _source_audio_bed(
            spec, project, f"{stem}{source_suffix}", total_sec, source_gain)

    # 混音:BGM(渲染视频里已有)+ SFX 床。只在强 SFX 下轻度让位，避免
    # 燃向混剪的主旋律听感被明显抽低。
    ffmpeg = config.tool("ffmpeg")
    out = project / "outputs" / f"{stem}{source_suffix}.sound.mp4"
    command = [ffmpeg, "-y", "-v", "error", "-i", str(render_mp4),
               "-i", str(bed_path)]
    if source_path and source_shots:
        command += ["-i", str(source_path)]
        graph = (
            "[1:a]asplit=2[sfx1][sfx2];"
            "[0:a][sfx1]sidechaincompress=threshold=0.18:ratio=1.8:"
            "attack=12:release=120[duck];"
            "[duck][sfx2][2:a]amix=inputs=3:weights=1 0.9 1:normalize=0[mix]"
        )
    else:
        graph = (
            "[1:a]asplit=2[sfx1][sfx2];"
            "[0:a][sfx1]sidechaincompress=threshold=0.18:ratio=1.8:"
            "attack=12:release=120[duck];"
            "[duck][sfx2]amix=inputs=2:weights=1 0.9:normalize=0[mix]"
        )
    command += ["-filter_complex", graph, "-map", "0:v", "-map", "[mix]",
                "-c:v", "copy", "-c:a", "aac", "-b:a", "256k",
                "-shortest", str(out)]
    subprocess.run(command, check=True)
    return str(out)
