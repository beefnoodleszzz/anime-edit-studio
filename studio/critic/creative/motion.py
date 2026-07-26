"""Deterministic rendered-motion QA for motion choreography."""
from __future__ import annotations

from pathlib import Path
from statistics import median

import cv2
import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from studio.creative.reference import EditingStyleProfile

MOTION_QA_VERSION = "motion-qa-1.0.0"


class MotionCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metric: str
    actual: float
    target: float
    tolerance: float = Field(..., ge=0)
    passed: bool


class MotionQAResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str = MOTION_QA_VERSION
    style_id: str
    sample_count: int
    median_motion: float
    p75_motion: float
    dynamic_range: float
    hold_ratio: float
    direction_balance: float
    direction_reversal_rate: float
    cross_cut_continuity: float
    checks: list[MotionCheck]
    passed: bool


def _read_frame(capture: cv2.VideoCapture, sec: float) -> np.ndarray | None:
    capture.set(cv2.CAP_PROP_POS_MSEC, max(0.0, sec) * 1000)
    ok, frame = capture.read()
    if not ok:
        return None
    return cv2.resize(frame, (320, 180), interpolation=cv2.INTER_AREA)


def _global_motion(first: np.ndarray, second: np.ndarray) -> tuple[float, float]:
    gray_a = cv2.cvtColor(first, cv2.COLOR_BGR2GRAY)
    gray_b = cv2.cvtColor(second, cv2.COLOR_BGR2GRAY)
    flow = cv2.calcOpticalFlowFarneback(
        gray_a, gray_b, None, 0.5, 3, 15, 3, 5, 1.2, 0
    )
    dx = float(np.median(flow[..., 0]))
    magnitude = float(np.median(np.hypot(flow[..., 0], flow[..., 1])))
    return magnitude, dx


def _cut_like(first: np.ndarray, second: np.ndarray) -> bool:
    delta = cv2.absdiff(first, second)
    return float(np.mean(delta)) / 255 >= 0.18


def evaluate_motion(
    video: Path,
    profile: EditingStyleProfile,
    *,
    cut_times: list[float] | None = None,
    sample_interval_sec: float = 0.25,
) -> MotionQAResult:
    """Measure motion dynamics without treating hard cuts as camera movement."""
    capture = cv2.VideoCapture(str(video))
    if not capture.isOpened():
        raise ValueError(f"无法打开 Motion QA 视频: {video}")
    fps = capture.get(cv2.CAP_PROP_FPS) or 24.0
    duration = capture.get(cv2.CAP_PROP_FRAME_COUNT) / fps
    magnitudes: list[float] = []
    horizontal: list[int] = []
    try:
        sec = 0.0
        while sec + sample_interval_sec < duration:
            first = _read_frame(capture, sec)
            second = _read_frame(capture, sec + sample_interval_sec)
            sec += sample_interval_sec
            if first is None or second is None or _cut_like(first, second):
                continue
            magnitude, dx = _global_motion(first, second)
            magnitudes.append(magnitude)
            if magnitude >= 0.3 and abs(dx) >= 0.15:
                horizontal.append(-1 if dx < 0 else 1)

        continuity: list[float] = []
        frame_sec = 1.0 / fps
        for cut in cut_times or []:
            # Stay outside the accepted 6–7 frame blur envelope. Optical flow
            # at peak blur loses its tracking signal and falsely reports hold.
            before_a = _read_frame(capture, cut - 8 * frame_sec)
            before_b = _read_frame(capture, cut - 6 * frame_sec)
            after_a = _read_frame(capture, cut + 6 * frame_sec)
            after_b = _read_frame(capture, cut + 8 * frame_sec)
            if any(value is None for value in (before_a, before_b, after_a, after_b)):
                continue
            before_mag, before_dx = _global_motion(before_a, before_b)
            after_mag, after_dx = _global_motion(after_a, after_b)
            if before_mag < 0.2 and after_mag < 0.2:
                continuity.append(0.0)
                continue
            direction = 1.0 if before_dx * after_dx > 0 else 0.0
            magnitude_fit = min(before_mag, after_mag) / max(
                before_mag, after_mag, 0.2
            )
            continuity.append(0.7 * direction + 0.3 * magnitude_fit)
    finally:
        capture.release()

    values = np.asarray(magnitudes or [0.0], dtype=np.float64)
    motion_median = float(np.median(values))
    p75 = float(np.percentile(values, 75))
    dynamic_range = max(1.0, p75 / max(motion_median, 0.08))
    hold_threshold = max(0.3, motion_median * 0.28)
    hold_ratio = float(np.mean(values <= hold_threshold))
    left, right = horizontal.count(-1), horizontal.count(1)
    balance = min(left, right) / max(left, right) if left or right else 0.5
    reversals = sum(a != b for a, b in zip(horizontal, horizontal[1:]))
    reversal_rate = reversals / max(1, len(horizontal) - 1)
    cross_cut = float(median(continuity)) if continuity else 0.0

    def around(metric: str, actual: float, target: float, tolerance: float):
        return MotionCheck(
            metric=metric,
            actual=actual,
            target=target,
            tolerance=tolerance,
            passed=abs(actual - target) <= tolerance,
        )

    checks = [
        around(
            "motion_median", motion_median, profile.motion_median_target,
            max(0.18, profile.motion_median_target * 0.5),
        ),
        around(
            "motion_p75", p75, profile.motion_p75_target,
            max(0.3, profile.motion_p75_target * 0.45),
        ),
        MotionCheck(
            metric="motion_dynamic_range",
            actual=dynamic_range,
            target=profile.motion_dynamic_range_target,
            tolerance=max(0.35, profile.motion_dynamic_range_target * 0.4),
            passed=dynamic_range + max(
                0.35, profile.motion_dynamic_range_target * 0.4
            ) >= profile.motion_dynamic_range_target,
        ),
        around("hold_ratio", hold_ratio, profile.hold_ratio_target, 0.12),
        MotionCheck(
            metric="direction_balance",
            actual=balance,
            target=profile.direction_balance_target,
            tolerance=0.2,
            passed=balance + 0.2 >= profile.direction_balance_target,
        ),
        MotionCheck(
            metric="direction_reversal_rate",
            actual=reversal_rate,
            target=profile.direction_reversal_target,
            tolerance=0.2,
            passed=reversal_rate + 0.2 >= profile.direction_reversal_target,
        ),
    ]
    if cut_times:
        checks.append(
            MotionCheck(
                metric="cross_cut_continuity",
                actual=cross_cut,
                target=0.65,
                tolerance=0.15,
                passed=cross_cut >= 0.5,
            )
        )
    return MotionQAResult(
        style_id=profile.id,
        sample_count=len(magnitudes),
        median_motion=motion_median,
        p75_motion=p75,
        dynamic_range=dynamic_range,
        hold_ratio=hold_ratio,
        direction_balance=balance,
        direction_reversal_rate=reversal_rate,
        cross_cut_continuity=cross_cut,
        checks=checks,
        passed=all(check.passed for check in checks),
    )


__all__ = [
    "MOTION_QA_VERSION",
    "MotionCheck",
    "MotionQAResult",
    "evaluate_motion",
]
