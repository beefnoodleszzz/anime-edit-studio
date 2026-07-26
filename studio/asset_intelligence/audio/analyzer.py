"""Per-shot audio energy and conservative music-likelihood estimation."""
from __future__ import annotations

import json
import math
import sqlite3
from pathlib import Path

import numpy as np

from studio.core.cache import JsonCache
from studio.core.hashing import analysis_cache_key
from studio.execution.ffmpeg import decode_audio_mono

AUDIO_PIPELINE_VERSION = "audio-1.0.0"
MODEL = "deterministic-spectral"
MODEL_VERSION = "numpy-rms-flatness-v1"
SAMPLE_RATE = 8000


def _rms(samples: np.ndarray) -> float:
    if samples.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(samples, dtype=np.float64)) + 1e-12))


def _music_likelihood(samples: np.ndarray) -> float:
    """Low-confidence acoustic estimate, not semantic music recognition."""
    if samples.size < SAMPLE_RATE // 4:
        return 0.0
    window = 1024
    count = min(12, max(1, samples.size // window))
    positions = np.linspace(0, max(0, samples.size - window), count).astype(int)
    flatness, rms_values = [], []
    hann = np.hanning(window)
    for start in positions:
        frame = samples[start:start + window]
        if frame.size < window:
            frame = np.pad(frame, (0, window - frame.size))
        spectrum = np.abs(np.fft.rfft(frame * hann)) + 1e-9
        flatness.append(
            float(np.exp(np.mean(np.log(spectrum))) / np.mean(spectrum))
        )
        rms_values.append(_rms(frame))
    tonal = 1.0 - float(np.mean(flatness))
    mean_rms = float(np.mean(rms_values))
    stability = 1.0 - min(1.0, float(np.std(rms_values)) / (mean_rms + 1e-6))
    zcr = float(np.mean(np.abs(np.diff(np.signbit(samples).astype(np.int8)))))
    zcr_score = math.exp(-((zcr - 0.12) / 0.14) ** 2)
    return max(0.0, min(1.0, 0.55 * tonal + 0.25 * stability + 0.2 * zcr_score))


def _analyze_asset(
    *,
    media: Path,
    shots: list[sqlite3.Row],
    asset_hash: str,
    cache: JsonCache,
) -> list[dict]:
    key = analysis_cache_key(
        asset_hash=asset_hash,
        model=MODEL,
        model_version=MODEL_VERSION,
        pipeline_version=AUDIO_PIPELINE_VERSION,
        parameters={
            "sample_rate": SAMPLE_RATE,
            "shot_ranges": [
                [row["id"], row["start_sec"], row["end_sec"]] for row in shots
            ],
        },
    )
    cached = cache.get("audio-v2", key)
    if cached is not None:
        return list(cached)

    samples = decode_audio_mono(media, sample_rate=SAMPLE_RATE)
    rows = []
    if samples.size == 0:
        rows = [
            {
                "shot_id": shot["id"],
                "audio_energy": 0.0,
                "audio_energy_confidence": 1.0,
                "music_presence": 0.0,
                "music_presence_confidence": 1.0,
                "method": "no_audio_stream",
            }
            for shot in shots
        ]
    else:
        raw = []
        for shot in shots:
            start = max(0, round(shot["start_sec"] * SAMPLE_RATE))
            end = min(samples.size, round(shot["end_sec"] * SAMPLE_RATE))
            segment = samples[start:end]
            raw.append((_rms(segment), _music_likelihood(segment)))
        energies = np.array([item[0] for item in raw], dtype=np.float64)
        low, high = np.percentile(energies, [10, 95]) if len(energies) else (0, 1)
        scale = max(float(high - low), 1e-8)
        for shot, (energy, music) in zip(shots, raw):
            rows.append(
                {
                    "shot_id": shot["id"],
                    "audio_energy": max(0.0, min(1.0, (energy - low) / scale)),
                    "audio_energy_confidence": 0.95,
                    "music_presence": music,
                    "music_presence_confidence": 0.35,
                    "method": "rms_percentile+spectral_flatness_estimate",
                }
            )
    cache.put("audio-v2", key, rows)
    return rows


def analyze_pending_audio(
    conn: sqlite3.Connection,
    *,
    cache_root: Path,
    asset_id: str | None = None,
    asset_limit: int | None = None,
) -> dict:
    conn.row_factory = sqlite3.Row
    sql = """
        SELECT a.id,a.sha256,a.path,a.proxy_path
        FROM assets a
        WHERE EXISTS (
          SELECT 1 FROM shots s
          WHERE s.asset_id=a.id
            AND (s.audio_energy IS NULL OR s.music_presence IS NULL)
        )
        ORDER BY a.id
    """
    params: list[object] = []
    if asset_id:
        sql = sql.replace("ORDER BY", "AND a.id=? ORDER BY")
        params.append(asset_id)
    if asset_limit is not None:
        sql += " LIMIT ?"
        params.append(asset_limit)
    assets = conn.execute(sql, params).fetchall()
    cache = JsonCache(cache_root)
    analyzed_shots = 0
    failures = []
    for asset in assets:
        shots = conn.execute(
            "SELECT id,start_sec,end_sec FROM shots WHERE asset_id=? ORDER BY idx",
            (asset["id"],),
        ).fetchall()
        candidates = [asset["proxy_path"], asset["path"]]
        media = next((Path(value) for value in candidates if value and Path(value).is_file()), None)
        if media is None:
            failures.append({"asset_id": asset["id"], "error": "media missing"})
            continue
        try:
            results = _analyze_asset(
                media=media,
                shots=shots,
                asset_hash=asset["sha256"],
                cache=cache,
            )
            conn.executemany(
                """
                UPDATE shots SET
                  audio_energy=?,audio_energy_confidence=?,
                  music_presence=?,music_presence_confidence=?
                WHERE id=?
                """,
                [
                    (
                        row["audio_energy"], row["audio_energy_confidence"],
                        row["music_presence"], row["music_presence_confidence"],
                        row["shot_id"],
                    )
                    for row in results
                ],
            )
            conn.commit()
            analyzed_shots += len(results)
        except Exception as exc:  # noqa: BLE001
            failures.append({"asset_id": asset["id"], "error": str(exc)})
    return {
        "assets_selected": len(assets),
        "shots_analyzed": analyzed_shots,
        "failed": failures,
        "pipeline_version": AUDIO_PIPELINE_VERSION,
    }


__all__ = ["AUDIO_PIPELINE_VERSION", "analyze_pending_audio"]
