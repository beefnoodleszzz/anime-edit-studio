"""Frame-exact action-peak detection for Cut Sync → Action Sync.

Shot-level motion metrics answer "is this shot busy"; they cannot tell the
planner *when inside the shot* the sword actually lands.  This module samples
the proxy inside each shot window at a fixed rate, builds a whole-frame optical
flow magnitude series on the same 10 Hz basis the StyleFingerprint uses, and
persists the local maxima as measured landmarks.

The output is analysis data only.  It lets Sequence Planner replace the bounded
semantic phase estimate in ``SourceSelection`` with a measured anchor, and lets
the retime solver place that anchor on a drum marker.  No Resolve capability is
involved and no landmark is claimed that was not measured.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from studio.core.cache import JsonCache
from studio.core.hashing import analysis_cache_key

ACTION_PEAK_VERSION = "action-peak-1.0.0"
MODEL = "opencv-farneback-fullframe"
MODEL_VERSION = f"opencv-{cv2.__version__}"

# 10 Hz keeps this consistent with the StyleFingerprint velocity basis so a
# measured peak and a reference velocity peak are directly comparable.
SAMPLE_HZ = 10.0
# Below this whole-frame flow magnitude a shot has no landmark worth syncing.
MIN_PEAK_MAGNITUDE = 0.6


@dataclass(frozen=True)
class ActionPeak:
    """One measured motion landmark, seconds relative to the shot start."""

    sec: float
    magnitude: float
    confidence: float

    def to_dict(self) -> dict:
        return {
            "sec": round(self.sec, 4),
            "magnitude": round(self.magnitude, 4),
            "confidence": round(self.confidence, 4),
        }


class ActionPeakDetector:
    def __init__(self, *, sample_hz: float = SAMPLE_HZ, max_peaks: int = 4):
        if sample_hz <= 0 or max_peaks < 1:
            raise ValueError("invalid detector parameters")
        self.sample_hz = sample_hz
        self.max_peaks = max_peaks

    def _magnitude_series(
        self, media: Path, *, start_sec: float, end_sec: float
    ) -> tuple[list[float], list[float]]:
        if end_sec <= start_sec:
            raise ValueError("end_sec 必须大于 start_sec")
        capture = cv2.VideoCapture(str(media))
        if not capture.isOpened():
            raise ValueError(f"无法打开媒体: {media}")
        times = np.arange(start_sec, end_sec, 1 / self.sample_hz)
        offsets: list[float] = []
        magnitudes: list[float] = []
        previous: np.ndarray | None = None
        try:
            for sec in times:
                capture.set(cv2.CAP_PROP_POS_MSEC, float(sec) * 1000)
                ok, frame = capture.read()
                if not ok:
                    continue
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                # Downscale keeps flow cheap and stable against per-pixel noise;
                # a fixed working height makes the magnitude comparable across
                # source resolutions.
                scale = 240 / gray.shape[0]
                if scale < 1.0:
                    gray = cv2.resize(
                        gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA
                    )
                if previous is not None and previous.shape == gray.shape:
                    flow = cv2.calcOpticalFlowFarneback(
                        previous, gray, None, 0.5, 3, 15, 3, 5, 1.2, 0
                    )
                    magnitude = float(np.mean(np.hypot(flow[..., 0], flow[..., 1])))
                    offsets.append(float(sec - start_sec))
                    magnitudes.append(magnitude)
                previous = gray
        finally:
            capture.release()
        return offsets, magnitudes

    def detect(
        self, media: Path, *, start_sec: float, end_sec: float
    ) -> list[ActionPeak]:
        offsets, magnitudes = self._magnitude_series(
            media, start_sec=start_sec, end_sec=end_sec
        )
        return self._peaks(offsets, magnitudes)

    def _peaks(
        self, offsets: list[float], magnitudes: list[float]
    ) -> list[ActionPeak]:
        if len(magnitudes) < 3:
            return []
        series = np.asarray(magnitudes, dtype=np.float64)
        peak_reference = float(series.max())
        if peak_reference < MIN_PEAK_MAGNITUDE:
            return []
        floor = float(np.median(series))
        span = max(peak_reference - floor, 1e-6)
        candidates: list[ActionPeak] = []
        for index in range(1, len(series) - 1):
            value = series[index]
            if value < series[index - 1] or value < series[index + 1]:
                continue
            if value < MIN_PEAK_MAGNITUDE:
                continue
            # Confidence = how far the local maximum stands above the shot's own
            # motion floor.  A flat, uniformly busy shot yields low confidence.
            confidence = float(np.clip((value - floor) / span, 0.0, 1.0))
            candidates.append(
                ActionPeak(
                    sec=offsets[index],
                    magnitude=float(value),
                    confidence=round(confidence, 4),
                )
            )
        candidates.sort(key=lambda peak: peak.magnitude, reverse=True)
        selected = candidates[: self.max_peaks]
        selected.sort(key=lambda peak: peak.sec)
        return selected


def _media_for(row: sqlite3.Row) -> Path | None:
    return next(
        (
            Path(value)
            for value in (row["proxy_path"], row["path"])
            if value and Path(value).is_file()
        ),
        None,
    )


def analyze_pending_action_peaks(
    conn: sqlite3.Connection,
    *,
    cache_root: Path,
    asset_id: str | None = None,
    limit: int | None = None,
    detector: ActionPeakDetector | None = None,
) -> dict:
    """Populate ``shots.action_peaks`` for shots that lack a current version."""
    conn.row_factory = sqlite3.Row
    where = (
        "WHERE (s.action_peaks_version IS NULL "
        "OR s.action_peaks_version != ?) AND s.motion_mag IS NOT NULL"
    )
    params: list[object] = [ACTION_PEAK_VERSION]
    if asset_id:
        where += " AND s.asset_id=?"
        params.append(asset_id)
    sql = (
        "SELECT s.id,s.start_sec,s.end_sec,a.sha256,a.path,a.proxy_path "
        "FROM shots s JOIN assets a ON a.id=s.asset_id "
        f"{where} ORDER BY s.asset_id,s.idx"
    )
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    cache = JsonCache(cache_root)
    detector = detector or ActionPeakDetector()
    done, skipped, failed = 0, 0, []
    for row in rows:
        media = _media_for(row)
        if media is None:
            skipped += 1
            continue
        key = analysis_cache_key(
            asset_hash=row["sha256"],
            model=MODEL,
            model_version=MODEL_VERSION,
            pipeline_version=ACTION_PEAK_VERSION,
            parameters={
                "shot_id": row["id"],
                "start_sec": round(float(row["start_sec"]), 4),
                "end_sec": round(float(row["end_sec"]), 4),
                "sample_hz": detector.sample_hz,
                "max_peaks": detector.max_peaks,
            },
        )
        try:
            cached = cache.get("action-peak-v1", key)
            if cached is None:
                peaks = detector.detect(
                    media,
                    start_sec=float(row["start_sec"]),
                    end_sec=float(row["end_sec"]),
                )
                cached = [peak.to_dict() for peak in peaks]
                cache.put("action-peak-v1", key, cached)
            conn.execute(
                "UPDATE shots SET action_peaks=?,action_peaks_version=? WHERE id=?",
                (json.dumps(cached, separators=(",", ":")), ACTION_PEAK_VERSION, row["id"]),
            )
            done += 1
            if done % 250 == 0:
                conn.commit()
        except Exception as exc:  # noqa: BLE001
            failed.append({"shot_id": row["id"], "error": str(exc)})
    conn.commit()
    return {
        "selected": len(rows),
        "analyzed": done,
        "skipped_no_media": skipped,
        "failed": failed,
        "version": ACTION_PEAK_VERSION,
    }


def load_action_peaks(raw: str | None) -> list[ActionPeak]:
    """Rehydrate persisted peaks; tolerate NULL and legacy empty values."""
    if not raw:
        return []
    try:
        items = json.loads(raw)
    except (ValueError, TypeError):
        return []
    peaks: list[ActionPeak] = []
    for item in items:
        try:
            peaks.append(
                ActionPeak(
                    sec=float(item["sec"]),
                    magnitude=float(item["magnitude"]),
                    confidence=float(item["confidence"]),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return peaks


__all__ = [
    "ACTION_PEAK_VERSION",
    "ActionPeak",
    "ActionPeakDetector",
    "analyze_pending_action_peaks",
    "load_action_peaks",
]
