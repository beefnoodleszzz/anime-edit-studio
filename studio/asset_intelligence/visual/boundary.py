"""Boundary-level cutability computed from adjacent shot candidate frames."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import cv2
import numpy as np

from studio.core.cache import JsonCache
from studio.core.hashing import analysis_cache_key, file_sha256

PIPELINE_VERSION = "cutability-1.0.0"
MODEL = "opencv-boundary-analysis"
MODEL_VERSION = f"opencv-{cv2.__version__}"


def _candidates(keyframe: Path) -> list[Path]:
    prefix = keyframe.stem.rsplit("_c", 1)[0] if "_c" in keyframe.stem else keyframe.stem
    return sorted(keyframe.parent.glob(f"{prefix}_c*.jpg")) or [keyframe]


def _gray(path: Path) -> np.ndarray:
    value = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if value is None:
        raise ValueError(f"无法读取边界帧: {path}")
    return cv2.resize(value, (192, 108), interpolation=cv2.INTER_AREA)


def _difference(a: np.ndarray, b: np.ndarray) -> float:
    hist_a = cv2.calcHist([a], [0], None, [32], [0, 256])
    hist_b = cv2.calcHist([b], [0], None, [32], [0, 256])
    cv2.normalize(hist_a, hist_a)
    cv2.normalize(hist_b, hist_b)
    histogram = cv2.compareHist(hist_a, hist_b, cv2.HISTCMP_BHATTACHARYYA)
    pixel = float(np.mean(cv2.absdiff(a, b))) / 255
    return max(0.0, min(1.0, 0.55 * histogram + 0.45 * min(1.0, pixel * 2)))


def analyze_cutability(
    conn: sqlite3.Connection,
    *,
    cache_root: Path,
    asset_id: str | None = None,
) -> dict:
    conn.row_factory = sqlite3.Row
    assets_sql = "SELECT DISTINCT asset_id FROM shots"
    params: list[object] = []
    if asset_id:
        assets_sql += " WHERE asset_id=?"
        params.append(asset_id)
    asset_ids = [row[0] for row in conn.execute(assets_sql, params)]
    cache = JsonCache(cache_root)
    analyzed, failed = 0, []
    for current_asset in asset_ids:
        rows = conn.execute(
            """
            SELECT s.id,s.keyframe,a.sha256
            FROM shots s JOIN assets a ON a.id=s.asset_id
            WHERE s.asset_id=? AND s.keyframe IS NOT NULL ORDER BY s.idx
            """,
            (current_asset,),
        ).fetchall()
        for index, row in enumerate(rows):
            try:
                current = _candidates(Path(row["keyframe"]))
                endpoint_a = current[-2] if len(current) >= 2 else current[-1]
                endpoint_b = current[-1]
                next_first = (
                    _candidates(Path(rows[index + 1]["keyframe"]))[0]
                    if index + 1 < len(rows)
                    else endpoint_b
                )
                key = analysis_cache_key(
                    asset_hash=row["sha256"],
                    model=MODEL,
                    model_version=MODEL_VERSION,
                    pipeline_version=PIPELINE_VERSION,
                    parameters={
                        "shot_id": row["id"],
                        "frames": [
                            file_sha256(endpoint_a),
                            file_sha256(endpoint_b),
                            file_sha256(next_first),
                        ],
                    },
                )
                result = cache.get("cutability-v2", key)
                if result is None:
                    end_motion = _difference(_gray(endpoint_a), _gray(endpoint_b))
                    cut_contrast = (
                        _difference(_gray(endpoint_b), _gray(next_first))
                        if index + 1 < len(rows)
                        else 0.5
                    )
                    result = {
                        "value": max(
                            0.0,
                            min(1.0, 0.55 * cut_contrast + 0.45 * (1 - end_motion)),
                        ),
                        "confidence": 0.82 if len(current) >= 2 else 0.45,
                        "method": "endpoint_stability+adjacent_boundary_contrast",
                    }
                    cache.put("cutability-v2", key, result)
                conn.execute(
                    "UPDATE shots SET cutability=?,cutability_confidence=? WHERE id=?",
                    (result["value"], result["confidence"], row["id"]),
                )
                analyzed += 1
                if analyzed % 250 == 0:
                    conn.commit()
            except Exception as exc:  # noqa: BLE001
                failed.append({"shot_id": row["id"], "error": str(exc)})
    conn.commit()
    return {
        "analyzed": analyzed,
        "failed": failed,
        "pipeline_version": PIPELINE_VERSION,
    }


__all__ = ["PIPELINE_VERSION", "analyze_cutability"]
