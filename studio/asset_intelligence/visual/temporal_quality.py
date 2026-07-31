"""Multi-frame technical and delivery-crop quality for shortlisted shots.

This is deliberately deterministic and cached.  It does not decide whether a
shot is creative or exciting; it only prevents a weak technical window from
being rescued by contextual ranking.
"""
from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from studio.asset_intelligence.visual.analyzer import VisualAnalyzer

TEMPORAL_QUALITY_VERSION = "temporal-square-quality-1.0.0"
DEFAULT_TARGET_ASPECT = 1.0


@dataclass(frozen=True)
class TemporalQuality:
    shot_id: str
    sample_count: int
    quality_floor: float
    bad_frame_ratio: float
    crop_fitness: float
    details: dict

    @property
    def accepted(self) -> bool:
        return (
            self.sample_count >= 3
            and self.quality_floor >= 0.48
            and self.bad_frame_ratio <= 0.34
            and self.crop_fitness >= 0.68
        )


def _square_crop_fitness(box, source_aspect: float, target_aspect: float) -> float:
    if source_aspect > target_aspect:
        crop_w, crop_h = target_aspect / source_aspect, 1.0
    else:
        crop_w, crop_h = 1.0, source_aspect / target_aspect
    crop_x, crop_y = (1.0 - crop_w) / 2.0, (1.0 - crop_h) / 2.0
    ix = max(0.0, min(box.x + box.width, crop_x + crop_w) - max(box.x, crop_x))
    iy = max(0.0, min(box.y + box.height, crop_y + crop_h) - max(box.y, crop_y))
    visible = ix * iy / max(box.width * box.height, 1e-6)
    # Low-confidence saliency boxes must not pretend to prove crop safety.
    return float(np.clip(visible * (0.75 + 0.25 * box.confidence), 0.0, 1.0))


def _frame_metrics(frame: np.ndarray, target_aspect: float) -> dict[str, float]:
    height, width = frame.shape[:2]
    scale = min(1.0, 640.0 / max(width, 1))
    if scale < 1.0:
        frame = cv2.resize(frame, (round(width * scale), round(height * scale)))
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    sharp_score = 1.0 - math.exp(-sharpness / 115.0)
    compression = VisualAnalyzer._blockiness(gray)
    clipped = float(np.mean((gray <= 3) | (gray >= 252)))
    exposure_score = 1.0 - min(1.0, clipped / 0.38)
    quality = float(
        np.clip(0.55 * sharp_score + 0.25 * (1.0 - compression) + 0.20 * exposure_score, 0, 1)
    )
    box = VisualAnalyzer._subject_box(gray, hsv)
    crop = _square_crop_fitness(box, width / max(height, 1), target_aspect)
    bad = sharp_score < 0.30 or compression > 0.48 or clipped > 0.42 or crop < 0.58
    return {
        "quality": quality,
        "sharpness": sharp_score,
        "compression": compression,
        "clipped": clipped,
        "crop_fitness": crop,
        "bad": float(bad),
    }


def analyze_temporal_quality(
    media: Path,
    *,
    shot_id: str,
    start_sec: float,
    end_sec: float,
    target_aspect: float = DEFAULT_TARGET_ASPECT,
    sample_count: int = 7,
) -> TemporalQuality:
    if end_sec <= start_sec:
        raise ValueError("end_sec 必须大于 start_sec")
    capture = cv2.VideoCapture(str(media))
    if not capture.isOpened():
        raise ValueError(f"无法打开媒体: {media}")
    margin = min(0.08, (end_sec - start_sec) * 0.08)
    times = np.linspace(start_sec + margin, end_sec - margin, sample_count)
    metrics: list[dict[str, float]] = []
    try:
        for sec in times:
            capture.set(cv2.CAP_PROP_POS_MSEC, float(sec) * 1000)
            ok, frame = capture.read()
            if ok:
                metrics.append(_frame_metrics(frame, target_aspect))
    finally:
        capture.release()
    if not metrics:
        raise ValueError(f"镜头无法解码: {shot_id}")
    qualities = [item["quality"] for item in metrics]
    crops = [item["crop_fitness"] for item in metrics]
    return TemporalQuality(
        shot_id=shot_id,
        sample_count=len(metrics),
        quality_floor=float(np.quantile(qualities, 0.20)),
        bad_frame_ratio=sum(item["bad"] for item in metrics) / len(metrics),
        crop_fitness=float(np.quantile(crops, 0.20)),
        details={"samples": metrics, "target_aspect": target_aspect},
    )


def gate_candidates(
    conn: sqlite3.Connection,
    shot_ids: list[str],
    *,
    target_aspect: float = DEFAULT_TARGET_ASPECT,
) -> tuple[list[str], list[dict]]:
    """Analyze missing candidate windows and return only hard-gate survivors."""
    if not shot_ids:
        return [], []
    placeholders = ",".join("?" for _ in shot_ids)
    rows = conn.execute(
        f"""
        SELECT s.id,s.start_sec,s.end_sec,a.path,a.proxy_path,a.sha256 AS asset_hash
        FROM shots s JOIN assets a ON a.id=s.asset_id
        WHERE s.id IN ({placeholders})
        """,
        shot_ids,
    ).fetchall()
    by_id = {row["id"]: row for row in rows}
    accepted: list[str] = []
    report: list[dict] = []
    for shot_id in shot_ids:
        cached = conn.execute(
            """
            SELECT * FROM shot_temporal_quality
            WHERE shot_id=? AND version=? AND target_aspect=? AND asset_hash=?
            """,
            (
                shot_id,
                TEMPORAL_QUALITY_VERSION,
                target_aspect,
                by_id[shot_id]["asset_hash"] if shot_id in by_id else "",
            ),
        ).fetchone()
        if cached:
            result = TemporalQuality(
                shot_id=shot_id,
                sample_count=cached["sample_count"],
                quality_floor=cached["quality_floor"],
                bad_frame_ratio=cached["bad_frame_ratio"],
                crop_fitness=cached["crop_fitness"],
                details=json.loads(cached["details_json"]),
            )
        else:
            row = by_id.get(shot_id)
            media = None if row is None else next(
                (
                    Path(value)
                    for value in (row["proxy_path"], row["path"])
                    if value and Path(value).is_file()
                ),
                None,
            )
            if row is None or media is None:
                report.append({"shot_id": shot_id, "accepted": False, "reason": "media_unreachable"})
                continue
            try:
                result = analyze_temporal_quality(
                    media,
                    shot_id=shot_id,
                    start_sec=float(row["start_sec"]),
                    end_sec=float(row["end_sec"]),
                    target_aspect=target_aspect,
                )
            except Exception as exc:  # noqa: BLE001
                report.append({"shot_id": shot_id, "accepted": False, "reason": str(exc)})
                continue
            conn.execute(
                """
                INSERT INTO shot_temporal_quality(
                  shot_id,version,asset_hash,target_aspect,sample_count,quality_floor,
                  bad_frame_ratio,crop_fitness,details_json
                ) VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (
                    shot_id,
                    TEMPORAL_QUALITY_VERSION,
                    row["asset_hash"],
                    target_aspect,
                    result.sample_count,
                    result.quality_floor,
                    result.bad_frame_ratio,
                    result.crop_fitness,
                    json.dumps(result.details, separators=(",", ":")),
                ),
            )
        if result.accepted:
            accepted.append(shot_id)
        report.append(
            {
                "shot_id": shot_id,
                "accepted": result.accepted,
                "sample_count": result.sample_count,
                "quality_floor": round(result.quality_floor, 4),
                "bad_frame_ratio": round(result.bad_frame_ratio, 4),
                "crop_fitness": round(result.crop_fitness, 4),
            }
        )
    conn.commit()
    return accepted, report


__all__ = [
    "DEFAULT_TARGET_ASPECT",
    "TEMPORAL_QUALITY_VERSION",
    "TemporalQuality",
    "analyze_temporal_quality",
    "gate_candidates",
]
