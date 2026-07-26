"""Motion analysis from representative frames already extracted per shot."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import cv2
import numpy as np

from studio.core.cache import JsonCache
from studio.core.hashing import analysis_cache_key, file_sha256

MOTION_PIPELINE_VERSION = "motion-1.0.0"
MODEL = "opencv-farneback"
MODEL_VERSION = f"opencv-{cv2.__version__}"


def _pair(keyframe: Path) -> tuple[Path, Path]:
    prefix = keyframe.stem.rsplit("_c", 1)[0] if "_c" in keyframe.stem else keyframe.stem
    candidates = sorted(keyframe.parent.glob(f"{prefix}_c*.jpg"))
    if len(candidates) >= 4:
        return candidates[1], candidates[-2]
    if len(candidates) >= 2:
        return candidates[0], candidates[-1]
    return keyframe, keyframe


def _compute(first: Path, second: Path, representative: Path) -> dict:
    a = cv2.imread(str(first), cv2.IMREAD_GRAYSCALE)
    b = cv2.imread(str(second), cv2.IMREAD_GRAYSCALE)
    middle = cv2.imread(str(representative), cv2.IMREAD_GRAYSCALE)
    if a is None or b is None or middle is None:
        raise ValueError("motion analyzer 无法读取候选帧")
    if a.shape != b.shape:
        b = cv2.resize(b, (a.shape[1], a.shape[0]), interpolation=cv2.INTER_AREA)
    flow = cv2.calcOpticalFlowFarneback(a, b, None, 0.5, 3, 15, 3, 5, 1.2, 0)
    fx, fy = float(np.median(flow[..., 0])), float(np.median(flow[..., 1]))
    magnitude = float(np.median(np.hypot(flow[..., 0], flow[..., 1])))
    if magnitude < 0.3:
        direction = "static"
    else:
        angle = np.degrees(np.arctan2(fy, fx))
        directions = [
            "right", "down-right", "down", "down-left",
            "left", "up-left", "up", "up-right",
        ]
        direction = directions[int(((angle + 202.5) % 360) // 45)]
    return {
        "brightness": round(float(middle.mean()) / 255, 6),
        "sharpness": round(float(cv2.Laplacian(middle, cv2.CV_64F).var()), 4),
        "motion_dir": direction,
        "motion_mag": round(magnitude, 6),
        "confidence": 0.78 if first != second else 0.3,
        "method": "candidate_pair_farneback_median",
    }


def analyze_pending_motion(
    conn: sqlite3.Connection,
    *,
    cache_root: Path,
    asset_id: str | None = None,
    limit: int | None = None,
) -> dict:
    conn.row_factory = sqlite3.Row
    where = "WHERE s.motion_mag IS NULL AND s.keyframe IS NOT NULL"
    params: list[object] = []
    if asset_id:
        where += " AND s.asset_id=?"
        params.append(asset_id)
    sql = (
        "SELECT s.id,s.keyframe,a.sha256 FROM shots s "
        f"JOIN assets a ON a.id=s.asset_id {where} ORDER BY s.asset_id,s.idx"
    )
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    cache = JsonCache(cache_root)
    done, failed = 0, []
    for row in rows:
        representative = Path(row["keyframe"])
        first, second = _pair(representative)
        key = analysis_cache_key(
            asset_hash=row["sha256"],
            model=MODEL,
            model_version=MODEL_VERSION,
            pipeline_version=MOTION_PIPELINE_VERSION,
            parameters={
                "shot_id": row["id"],
                "frames": [
                    file_sha256(first),
                    file_sha256(second),
                    file_sha256(representative),
                ],
            },
        )
        try:
            result = cache.get("motion-v2", key)
            if result is None:
                result = _compute(first, second, representative)
                cache.put("motion-v2", key, result)
            conn.execute(
                "UPDATE shots SET brightness=?,sharpness=?,motion_dir=?,motion_mag=? "
                "WHERE id=?",
                (
                    result["brightness"],
                    result["sharpness"],
                    result["motion_dir"],
                    result["motion_mag"],
                    row["id"],
                ),
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
        "failed": failed,
        "pipeline_version": MOTION_PIPELINE_VERSION,
    }


__all__ = ["MOTION_PIPELINE_VERSION", "analyze_pending_motion"]
