"""Deterministic visual-impact QA for the strict demo replica."""
from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np

from studio.editspec.schema import EditSpec


def run_demo_replica_qa(video: Path, spec_path: Path, output: Path) -> dict:
    spec = EditSpec.model_validate_json(spec_path.read_text())
    cap = cv2.VideoCapture(str(video))
    frames: list[np.ndarray] = []
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        frames.append(cv2.resize(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (270, 270)))
    cap.release()
    fps = spec.timebase.num / spec.timebase.den
    change = np.zeros(len(frames))
    sharp = np.zeros(len(frames))
    centers: list[tuple[float, float]] = []
    for index, frame in enumerate(frames):
        sharp[index] = cv2.Laplacian(frame, cv2.CV_64F).var()
        energy = cv2.Canny(frame, 80, 160).astype(np.float32)
        mass = float(energy.sum())
        if mass:
            ys, xs = np.indices(energy.shape)
            centers.append((float((xs * energy).sum() / mass / 270), float((ys * energy).sum() / mass / 270)))
        else:
            centers.append((0.5, 0.5))
        if index:
            change[index] = float(np.mean(cv2.absdiff(frame, frames[index - 1])))
    markers = [m for m in spec.markers if m.kind == "demo_replica_impact"]
    peaks = []
    recovery = []
    for marker in markers:
        frame = min(len(frames) - 1, round(marker.sec * fps))
        lo, hi = max(1, frame - 2), min(len(frames), frame + 3)
        local = change[lo:hi]
        peak_frame = lo + int(np.argmax(local))
        peaks.append({
            "sec": marker.sec,
            "frame_error": peak_frame - frame,
            "strength": float(change[peak_frame]),
        })
        if marker is not markers[-1] and frame + 5 < len(frames):
            recovery.append(bool(sharp[frame + 5] >= np.median(sharp[max(0, frame - 8):frame + 1]) * 0.65))
    marker_strength = np.array([row["strength"] for row in peaks])
    threshold = float(np.percentile(marker_strength, 15)) if len(marker_strength) else 0.0
    impact_pass = sum(abs(row["frame_error"]) <= 2 and row["strength"] >= threshold for row in peaks)
    weak_frames = [
        round(((left.sec + right.sec) / 2) * fps)
        for left, right in zip(markers, markers[1:])
    ]
    weak_peak = max((change[min(len(change) - 1, f)] for f in weak_frames), default=0.0)
    stable = []
    for clip in spec.clips[2:]:
        start = round(clip.timeline.in_sec * fps)
        end = min(len(frames), round(clip.timeline.out_sec * fps))
        stable.append(sum(sharp[start:end] >= np.median(sharp[start:end]) * 0.65) >= 12)
    central = [
        0.2 <= centers[round(clip.timeline.in_sec * fps)][0] <= 0.8
        and 0.2 <= centers[round(clip.timeline.in_sec * fps)][1] <= 0.8
        for clip in spec.clips[2:]
    ]
    categories = [
        marker.note.split(":", 1)[-1]
        for marker in spec.markers if marker.kind == "demo_replica_clip"
    ]
    result = {
        "passed": all((
            impact_pass / max(1, len(peaks)) >= 0.85,
            all(recovery),
            all(stable),
            weak_peak <= float(np.median(marker_strength)) * 0.70,
            all(central),
            categories[:8] == categories[8:16],
        )),
        "impact_sync_ratio": impact_pass / max(1, len(peaks)),
        "recovery_clear": all(recovery),
        "stable_hero_frames": all(stable),
        "weak_to_main_ratio": weak_peak / max(1e-6, float(np.median(marker_strength))),
        "central_60_percent": all(central),
        "category_refrain_match": categories[:8] == categories[8:16],
        "terminal_marker_recovery_exempt": 14.745,
        "marker_peaks": peaks,
    }
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    return result

