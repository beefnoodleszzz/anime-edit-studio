"""节拍分析:BPM / beat / downbeat / onset。用于卡点装配。

用 librosa 做 beat tracking;downbeat 近似为 4/4 每 4 拍(M2 够用,
后续可换 madmom 的下拍模型)。
"""
from __future__ import annotations

import json
from pathlib import Path

from . import config


def analyze(audio_path: str) -> dict:
    import librosa
    import numpy as np

    src = str(Path(audio_path).expanduser().resolve())
    y, sr = librosa.load(src, mono=True)
    duration = float(librosa.get_duration(y=y, sr=sr))
    tempo, beat_times = librosa.beat.beat_track(y=y, sr=sr, units="time")
    onsets = librosa.onset.onset_detect(y=y, sr=sr, units="time", backtrack=True)

    bpm = float(np.ravel(tempo)[0])  # librosa 新版返回数组
    beats = [round(float(t), 4) for t in beat_times]
    # 低能量段 librosa 会漏拍:按中位间隔把节拍网格补齐到整首时长
    if len(beats) >= 2:
        interval = float(np.median(np.diff(beats)))
        last = beats[-1]
        while interval > 0 and last + interval < duration:
            last += interval
            beats.append(round(last, 4))
    # 逐拍能量(RMS)→ 归一化 0..1,驱动切点疏密与段落
    rms = librosa.feature.rms(y=y)[0]
    rms_t = librosa.times_like(rms, sr=sr)
    be = np.interp(beats, rms_t, rms)
    span = float(be.max() - be.min())
    be = (be - be.min()) / (span + 1e-9)
    beat_energy = [round(float(v), 3) for v in be]

    # 下拍:4/4 网格相位对齐到强拍(能量对齐,比盲目每 4 拍准;不依赖 madmom)
    phase = max(range(4), key=lambda p: float(be[p::4].mean()) if len(be[p::4]) else 0.0)
    downbeats = beats[phase::4]

    return {
        "audio": src,
        "bpm": round(bpm, 2),
        "duration": round(duration, 3),
        "beats": beats,
        "downbeats": [round(b, 4) for b in downbeats],
        "onsets": [round(float(t), 4) for t in onsets],
        "beat_energy": beat_energy,
    }


def save(project_id: str, beatmap: dict) -> Path:
    proj = config.PROJECTS / project_id
    proj.mkdir(parents=True, exist_ok=True)
    path = proj / "beatmap.json"
    path.write_text(json.dumps(beatmap, ensure_ascii=False, indent=2))
    return path
