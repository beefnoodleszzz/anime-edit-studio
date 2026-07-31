"""Action-phase analysis: residual (subject) motion vs. camera motion (REFACTOR.md §11).

The existing ``action_peak.py`` finds *whole-frame* optical-flow maxima —
useful for "is this shot busy" but blind to whether the busy-ness is the
subject or the camera. This module compensates each frame pair for the
global affine transform estimated by ``studio.analysis.global_motion``
(already real-machine proven, reused rather than reimplemented) before
measuring motion, so a camera pan or zoom does not masquerade as a subject
action peak.

``action_score`` here excludes ``editability_score`` (REFACTOR.md §11's
listed 0.10 weight) because editability isn't computed until
``editability.py`` (a later commit); the remaining weights are
renormalized and documented below. ``sequence_scoring.py`` folds in
editability separately once it exists.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from studio.analysis.global_motion import estimate_global_motion
from studio.asset_intelligence.visual.analyzer import VisualAnalyzer
from studio.selection.schemas import ActionProfile

ACTION_ANALYZER_VERSION = "action-analyzer-1.0.0"
SAMPLE_HZ = 10.0
_FLOW_WORKING_HEIGHT = 240
_LANDING_SUBJECT_CONFIDENCE = 0.30
_LANDING_STABLE_SAMPLES = 2

# Renormalized subset of REFACTOR.md §11's action weights (editability's 0.10
# excluded here; see module docstring).
_WEIGHTS = {
    "residual_motion_peak": 0.20 / 0.90,
    "acceleration_peak": 0.15 / 0.90,
    "silhouette_change": 0.15 / 0.90,
    "impact_visual_impulse": 0.15 / 0.90,
    "subject_visible_ratio": 0.10 / 0.90,
    "landing_score": 0.15 / 0.90,
}


@dataclass(frozen=True)
class ActionFrameSample:
    sec: float
    global_translation_mag: float
    residual_magnitude: float
    subject_area: float
    brightness: float
    hue_mean: float
    subject_confidence: float


def _affine_matrix(estimate) -> np.ndarray:
    scale = float(np.exp(estimate.log_scale))
    theta = np.radians(estimate.rotation_deg)
    cos_t, sin_t = np.cos(theta), np.sin(theta)
    return np.array(
        [
            [scale * cos_t, -scale * sin_t, estimate.tx],
            [scale * sin_t, scale * cos_t, estimate.ty],
        ],
        dtype=np.float32,
    )


def _residual_flow_magnitude(prev_gray: np.ndarray, curr_gray: np.ndarray) -> tuple[float, float]:
    """Return (global_translation_mag, residual_motion_mag) for one frame pair."""
    estimate = estimate_global_motion(prev_gray, curr_gray)
    global_mag = float(np.hypot(estimate.tx, estimate.ty))
    matrix = _affine_matrix(estimate)
    warped = cv2.warpAffine(prev_gray, matrix, (curr_gray.shape[1], curr_gray.shape[0]))
    flow = cv2.calcOpticalFlowFarneback(warped, curr_gray, None, 0.5, 3, 15, 3, 5, 1.2, 0)
    residual_mag = float(np.mean(np.hypot(flow[..., 0], flow[..., 1])))
    return global_mag, residual_mag


def _sample_series(
    media: Path, *, start_sec: float, end_sec: float, sample_hz: float = SAMPLE_HZ
) -> list[ActionFrameSample]:
    if end_sec <= start_sec:
        raise ValueError("end_sec 必须大于 start_sec")
    capture = cv2.VideoCapture(str(media))
    if not capture.isOpened():
        raise ValueError(f"无法打开媒体: {media}")
    times = np.arange(start_sec, end_sec, 1 / sample_hz)
    samples: list[ActionFrameSample] = []
    previous_gray: np.ndarray | None = None
    try:
        for sec in times:
            capture.set(cv2.CAP_PROP_POS_MSEC, float(sec) * 1000)
            ok, frame = capture.read()
            if not ok:
                continue
            gray_full = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
            box = VisualAnalyzer._subject_box(gray_full, hsv)
            scale = _FLOW_WORKING_HEIGHT / gray_full.shape[0]
            gray = (
                cv2.resize(gray_full, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
                if scale < 1.0
                else gray_full
            )
            if previous_gray is not None and previous_gray.shape == gray.shape:
                global_mag, residual_mag = _residual_flow_magnitude(previous_gray, gray)
                samples.append(
                    ActionFrameSample(
                        sec=float(sec - start_sec),
                        global_translation_mag=global_mag,
                        residual_magnitude=residual_mag,
                        subject_area=box.width * box.height,
                        brightness=float(np.mean(gray_full)),
                        hue_mean=float(np.mean(hsv[..., 0])),
                        subject_confidence=box.confidence,
                    )
                )
            previous_gray = gray
    finally:
        capture.release()
    return samples


def _phase_markers(samples: list[ActionFrameSample]) -> dict[str, float | None]:
    residual = np.array([s.residual_magnitude for s in samples])
    floor = float(np.median(residual))
    peak_index = int(np.argmax(residual))
    peak_value = float(residual[peak_index])
    threshold = floor + 0.25 * max(peak_value - floor, 1e-6)

    anticipation_index = peak_index
    while anticipation_index > 0 and residual[anticipation_index - 1] >= threshold:
        anticipation_index -= 1
    anticipation_sec = (
        samples[anticipation_index].sec if anticipation_index < peak_index else None
    )

    recovery_index = peak_index
    while recovery_index < len(residual) - 1 and residual[recovery_index + 1] >= threshold:
        recovery_index += 1
    recovery_sec = samples[recovery_index].sec if recovery_index > peak_index else None

    landing_sec = None
    landing_score = 0.0
    start = recovery_index if recovery_sec is not None else peak_index
    for index in range(start, len(samples) - _LANDING_STABLE_SAMPLES + 1):
        window = samples[index : index + _LANDING_STABLE_SAMPLES]
        stable = all(s.residual_magnitude <= threshold for s in window)
        visible = all(s.subject_confidence >= _LANDING_SUBJECT_CONFIDENCE for s in window)
        if stable and visible:
            landing_sec = samples[index].sec
            landing_score = float(
                np.clip(
                    0.5 * (1.0 - min(1.0, window[0].residual_magnitude / max(peak_value, 1e-6)))
                    + 0.5 * min(s.subject_confidence for s in window),
                    0.0,
                    1.0,
                )
            )
            break

    return {
        "impact_sec": samples[peak_index].sec,
        "anticipation_sec": anticipation_sec,
        "recovery_sec": recovery_sec,
        "landing_sec": landing_sec,
        "landing_score": landing_score,
    }


def analyze_action(
    media: Path,
    *,
    start_sec: float,
    end_sec: float,
    sample_hz: float = SAMPLE_HZ,
    subject_visible_ratio: float | None = None,
) -> ActionProfile:
    samples = _sample_series(media, start_sec=start_sec, end_sec=end_sec, sample_hz=sample_hz)
    if len(samples) < 3:
        return ActionProfile()

    global_peak = max(s.global_translation_mag for s in samples)
    residual_series = np.array([s.residual_magnitude for s in samples])
    residual_peak = float(residual_series.max())
    acceleration_peak = float(np.max(np.abs(np.diff(residual_series)))) if len(samples) > 1 else 0.0

    areas = np.array([s.subject_area for s in samples])
    silhouette_change = float(np.clip(np.max(np.abs(np.diff(areas))) / 0.25, 0.0, 1.0)) if len(areas) > 1 else 0.0

    brightness = np.array([s.brightness for s in samples])
    brightness_impulse = float(np.clip(np.max(np.abs(np.diff(brightness))) / 60.0, 0.0, 1.0)) if len(brightness) > 1 else 0.0

    hues = np.array([s.hue_mean for s in samples])
    color_impulse = float(np.clip(np.max(np.abs(np.diff(hues))) / 40.0, 0.0, 1.0)) if len(hues) > 1 else 0.0

    if subject_visible_ratio is None:
        subject_visible_ratio = sum(s.subject_confidence >= 0.35 for s in samples) / len(samples)

    markers = _phase_markers(samples)
    impact_visual_impulse = 0.5 * brightness_impulse + 0.5 * color_impulse

    values = {
        "residual_motion_peak": min(1.0, residual_peak / 8.0),
        "acceleration_peak": min(1.0, acceleration_peak / 8.0),
        "silhouette_change": silhouette_change,
        "impact_visual_impulse": impact_visual_impulse,
        "subject_visible_ratio": subject_visible_ratio,
        "landing_score": markers["landing_score"],
    }
    action_score = sum(values[key] * weight for key, weight in _WEIGHTS.items())

    return ActionProfile(
        global_motion_peak=global_peak,
        residual_motion_peak=residual_peak,
        acceleration_peak=acceleration_peak,
        silhouette_change=silhouette_change,
        brightness_impulse=brightness_impulse,
        color_impulse=color_impulse,
        anticipation_sec=(
            start_sec + markers["anticipation_sec"] if markers["anticipation_sec"] is not None else None
        ),
        impact_sec=start_sec + markers["impact_sec"],
        recovery_sec=(
            start_sec + markers["recovery_sec"] if markers["recovery_sec"] is not None else None
        ),
        landing_sec=(
            start_sec + markers["landing_sec"] if markers["landing_sec"] is not None else None
        ),
        landing_score=markers["landing_score"],
        action_score=max(0.0, min(1.0, action_score)),
    )


__all__ = ["ACTION_ANALYZER_VERSION", "ActionFrameSample", "analyze_action"]
