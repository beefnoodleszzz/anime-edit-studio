"""Measure reference-video camera motion over beat-to-beat intervals."""

from __future__ import annotations

import json
from pathlib import Path

import cv2
import librosa
import numpy as np


REFERENCE = Path("/Users/zhangxiaolong/Desktop/study/a.mp4")
OUTPUT = Path(__file__).with_name("reference_beat_curve_analysis.json")


def _pair_motion(previous: np.ndarray, current: np.ndarray) -> dict[str, float]:
    points = cv2.goodFeaturesToTrack(
        previous, maxCorners=500, qualityLevel=0.01, minDistance=6, blockSize=5
    )
    if points is None or len(points) < 12:
        return {"valid": 0.0}
    moved, status, _ = cv2.calcOpticalFlowPyrLK(
        previous,
        current,
        points,
        None,
        winSize=(21, 21),
        maxLevel=3,
        criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 30, 0.01),
    )
    if moved is None or status is None:
        return {"valid": 0.0}
    keep = status.reshape(-1).astype(bool)
    source = points.reshape(-1, 2)[keep]
    target = moved.reshape(-1, 2)[keep]
    if len(source) < 12:
        return {"valid": 0.0}
    matrix, inliers = cv2.estimateAffinePartial2D(
        source,
        target,
        method=cv2.RANSAC,
        ransacReprojThreshold=2.0,
        maxIters=2000,
        confidence=0.995,
    )
    if matrix is None or inliers is None:
        return {"valid": 0.0}
    ratio = float(inliers.mean())
    a, b = float(matrix[0, 0]), float(matrix[0, 1])
    scale = max(1e-8, (a * a + b * b) ** 0.5)
    return {
        "valid": 1.0 if ratio >= 0.35 else 0.0,
        "inlier_ratio": ratio,
        "dx": float(matrix[0, 2] / previous.shape[1]),
        "dy": float(matrix[1, 2] / previous.shape[0]),
        "dlog_scale": float(np.log(scale)),
        "dangle_deg": float(np.degrees(np.arctan2(b, a))),
    }


def main() -> None:
    audio, sample_rate = librosa.load(REFERENCE, sr=22050, mono=True)
    _, beat_frames = librosa.beat.beat_track(y=audio, sr=sample_rate, units="frames")
    beats = librosa.frames_to_time(beat_frames, sr=sample_rate).astype(float)

    capture = cv2.VideoCapture(str(REFERENCE))
    fps = float(capture.get(cv2.CAP_PROP_FPS))
    rows: list[dict[str, float]] = []
    previous = None
    frame = 0
    while True:
        ok, image = capture.read()
        if not ok:
            break
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        if gray.shape[1] > 480:
            ratio = 480 / gray.shape[1]
            gray = cv2.resize(gray, (480, round(gray.shape[0] * ratio)))
        if previous is not None:
            row = _pair_motion(previous, gray)
            row["time"] = frame / fps
            if row.get("valid"):
                row["energy"] = float(
                    np.hypot(row["dx"], row["dy"]) * 100
                    + abs(row["dlog_scale"]) * 18
                    + abs(row["dangle_deg"]) * 0.018
                )
            rows.append(row)
        previous = gray
        frame += 1
    capture.release()

    # Stable section only. Each interval is normalized to 21 phase samples.
    stable = beats[(beats >= 2.3) & (beats <= 23.6)]
    phases = np.linspace(0.0, 1.0, 21)
    interval_curves: list[list[float | None]] = []
    interval_details: list[dict[str, object]] = []
    times = np.asarray([row["time"] for row in rows])
    energy = np.asarray(
        [row.get("energy", np.nan) if row.get("valid") else np.nan for row in rows]
    )
    dx = np.asarray([row.get("dx", np.nan) if row.get("valid") else np.nan for row in rows])
    dy = np.asarray([row.get("dy", np.nan) if row.get("valid") else np.nan for row in rows])
    dlog_scale = np.asarray(
        [row.get("dlog_scale", np.nan) if row.get("valid") else np.nan for row in rows]
    )
    for start, end in zip(stable[:-1], stable[1:], strict=True):
        sample_times = start + phases * (end - start)
        values: list[float | None] = []
        for sample_time in sample_times:
            mask = np.abs(times - sample_time) <= 1.1 / fps
            value = float(np.nanmedian(energy[mask])) if np.any(mask) else np.nan
            values.append(None if not np.isfinite(value) else value)
        if sum(value is not None for value in values) >= 15:
            interval_curves.append(values)
            numeric = np.asarray(
                [np.nan if value is None else value for value in values], dtype=float
            )
            interval_details.append(
                {
                    "start": round(float(start), 6),
                    "end": round(float(end), 6),
                    "duration": round(float(end - start), 6),
                    "peak_phase": round(float(phases[np.nanargmax(numeric)]), 3),
                    "net_dx_canvas": round(float(np.nansum(dx[(times > start) & (times <= end)])), 6),
                    "net_dy_canvas": round(float(np.nansum(dy[(times > start) & (times <= end)])), 6),
                    "net_scale_ratio": round(
                        float(np.exp(np.nansum(dlog_scale[(times > start) & (times <= end)]))),
                        6,
                    ),
                    "curve": values,
                }
            )

    matrix = np.asarray(
        [[np.nan if value is None else value for value in curve] for curve in interval_curves]
    )
    # Normalize each interval independently; we care about curve shape, not shot energy.
    low = np.nanpercentile(matrix, 10, axis=1, keepdims=True)
    high = np.nanpercentile(matrix, 90, axis=1, keepdims=True)
    normalized = np.clip((matrix - low) / np.maximum(high - low, 1e-8), 0, 1)
    median_curve = np.nanmedian(normalized, axis=0)
    q25_curve = np.nanpercentile(normalized, 25, axis=0)
    q75_curve = np.nanpercentile(normalized, 75, axis=0)
    peak_phases = np.asarray(
        [detail["peak_phase"] for detail in interval_details], dtype=float
    )
    cumulative = np.concatenate(
        ([0.0], np.cumsum((median_curve[:-1] + median_curve[1:]) / 2))
    )
    cumulative /= cumulative[-1]
    result = {
        "reference": str(REFERENCE),
        "fps": fps,
        "beats": [round(float(value), 6) for value in stable],
        "beat_intervals": [round(float(value), 6) for value in np.diff(stable)],
        "valid_intervals": len(interval_curves),
        "phase": [round(float(value), 2) for value in phases],
        "median_normalized_motion": [round(float(value), 4) for value in median_curve],
        "median_normalized_position": [round(float(value), 4) for value in cumulative],
        "q25_normalized_motion": [round(float(value), 4) for value in q25_curve],
        "q75_normalized_motion": [round(float(value), 4) for value in q75_curve],
        "median_curve_peak_phase": round(float(phases[np.nanargmax(median_curve)]), 3),
        "interval_peak_phase_median": round(float(np.median(peak_phases)), 3),
        "interval_peak_phase_p25": round(float(np.percentile(peak_phases, 25)), 3),
        "interval_peak_phase_p75": round(float(np.percentile(peak_phases, 75)), 3),
        "interval_net_dx_median_abs_canvas": round(
            float(np.median(np.abs([detail["net_dx_canvas"] for detail in interval_details]))),
            6,
        ),
        "interval_net_dx_positive_ratio": round(
            float(np.mean([detail["net_dx_canvas"] > 0 for detail in interval_details])),
            4,
        ),
        "interval_net_scale_median_abs_change": round(
            float(
                np.median(
                    np.abs([detail["net_scale_ratio"] - 1 for detail in interval_details])
                )
            ),
            6,
        ),
        "intervals": interval_details,
    }
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({key: value for key, value in result.items() if key != "intervals"}, indent=2))


if __name__ == "__main__":
    main()
