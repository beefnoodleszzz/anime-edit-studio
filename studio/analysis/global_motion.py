"""Two-tier global motion estimation (REFACTOR.md §6.2).

Tier 1: sparse Lucas-Kanade optical flow on good features, robustly fit
with RANSAC via ``estimateAffinePartial2D``. Falls back to Tier 2 (ECC,
direct pixel-intensity alignment) when Tier 1 finds too few trackable
points or too few RANSAC inliers to trust — the classic failure mode on
flat, low-texture anime frames where corner features barely exist.

Farneback dense flow (used elsewhere for cheap local/energy signals) is
deliberately not used as ground truth here: a full-frame median flow
vector conflates subject motion with camera motion and has no principled
inlier/outlier separation.
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

MIN_TRACK_POINTS = 12
MIN_INLIER_RATIO = 0.45
MIN_INLIERS_ABS = 8


@dataclass(frozen=True)
class GlobalMotionEstimate:
    tx: float
    ty: float
    log_scale: float
    rotation_deg: float
    inlier_ratio: float
    confidence: float
    method: str


def _lk_ransac(prev_gray: np.ndarray, curr_gray: np.ndarray) -> GlobalMotionEstimate | None:
    points = cv2.goodFeaturesToTrack(
        prev_gray, maxCorners=300, qualityLevel=0.01, minDistance=7, blockSize=7,
    )
    if points is None or len(points) < MIN_TRACK_POINTS:
        return None

    tracked, status, _ = cv2.calcOpticalFlowPyrLK(
        prev_gray, curr_gray, points, None,
        winSize=(21, 21), maxLevel=3,
        criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 30, 0.01),
    )
    if tracked is None:
        return None
    status = status.reshape(-1).astype(bool)
    source = points[status]
    target = tracked[status]
    if len(source) < MIN_TRACK_POINTS:
        return None

    matrix, inliers = cv2.estimateAffinePartial2D(
        source, target, method=cv2.RANSAC, ransacReprojThreshold=3.0, maxIters=2000,
    )
    if matrix is None or inliers is None:
        return None
    inlier_count = int(inliers.sum())
    inlier_ratio = inlier_count / len(source)
    if inlier_count < MIN_INLIERS_ABS or inlier_ratio < MIN_INLIER_RATIO:
        return None

    a, b = matrix[0, 0], matrix[0, 1]
    scale = float(np.hypot(a, b))
    rotation = float(np.degrees(np.arctan2(b, a)))
    tx, ty = float(matrix[0, 2]), float(matrix[1, 2])
    confidence = min(1.0, inlier_ratio * min(1.0, len(source) / 60))
    return GlobalMotionEstimate(
        tx=tx, ty=ty,
        log_scale=float(np.log(max(scale, 1e-6))),
        rotation_deg=rotation,
        inlier_ratio=inlier_ratio,
        confidence=confidence,
        method="lk_ransac",
    )


def _ecc(prev_gray: np.ndarray, curr_gray: np.ndarray) -> GlobalMotionEstimate:
    warp = np.eye(2, 3, dtype=np.float32)
    criteria = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 50, 1e-4)
    try:
        _, warp = cv2.findTransformECC(
            prev_gray, curr_gray, warp, cv2.MOTION_AFFINE, criteria, None, 5,
        )
        a, b = warp[0, 0], warp[0, 1]
        scale = float(np.hypot(a, b))
        rotation = float(np.degrees(np.arctan2(b, a)))
        tx, ty = float(warp[0, 2]), float(warp[1, 2])
        # ECC has no inlier concept; confidence is capped below LK/RANSAC's
        # ceiling because it can converge to a locally-consistent but wrong
        # optimum on repetitive anime textures.
        confidence = 0.55
    except cv2.error:
        tx = ty = 0.0
        scale = 1.0
        rotation = 0.0
        confidence = 0.1
    return GlobalMotionEstimate(
        tx=tx, ty=ty,
        log_scale=float(np.log(max(scale, 1e-6))),
        rotation_deg=rotation,
        inlier_ratio=0.0,
        confidence=confidence,
        method="ecc_fallback",
    )


def estimate_global_motion(prev_gray: np.ndarray, curr_gray: np.ndarray) -> GlobalMotionEstimate:
    """Estimate the frame-to-frame global (camera-like) transform.

    Always returns an estimate with an honest ``confidence`` — callers must
    not treat a low-confidence result as ground truth (REFACTOR.md §6.2,
    §0.4). ``prev_gray``/``curr_gray`` must be single-channel uint8 frames
    of equal shape.
    """
    if prev_gray.shape != curr_gray.shape:
        raise ValueError("prev_gray and curr_gray must share shape")
    result = _lk_ransac(prev_gray, curr_gray)
    if result is not None:
        return result
    return _ecc(prev_gray, curr_gray)


__all__ = ["GlobalMotionEstimate", "estimate_global_motion"]
