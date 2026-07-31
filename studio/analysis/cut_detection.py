"""Multi-signal cut detection and classification (REFACTOR.md §6.1).

A single PySceneDetect threshold cannot tell a hard cut from a flash frame
or a whip-hidden cut, so boundaries are proposed by PySceneDetect's
``ContentDetector`` (already real-machine proven in
``studio/creative/reference/fingerprint.py``) and then re-scored against
independent per-frame signals — HSV histogram distance, edge-map distance,
mean pixel difference, and a Laplacian sharpness jump — before being
classified. Flash cuts are frames that spike in brightness and immediately
revert; whip-hidden cuts are boundaries sitting inside a sustained
high-motion window on both sides.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from scenedetect import ContentDetector, SceneManager, open_video

CutType = str  # "hard_cut" | "flash_cut" | "whip_hidden_cut" | "dissolve" | "unknown"


@dataclass(frozen=True)
class CutCandidate:
    frame_index: int
    sec: float
    hist_distance: float
    edge_distance: float
    mean_diff: float
    sharpness_delta: float
    cut_type: CutType
    confidence: float


def _hist_distance(a: np.ndarray, b: np.ndarray) -> float:
    hist_a = cv2.calcHist([a], [0, 1, 2], None, [8, 8, 8], [0, 256] * 3)
    hist_b = cv2.calcHist([b], [0, 1, 2], None, [8, 8, 8], [0, 256] * 3)
    cv2.normalize(hist_a, hist_a)
    cv2.normalize(hist_b, hist_b)
    return float(cv2.compareHist(hist_a, hist_b, cv2.HISTCMP_BHATTACHARYYA))


def _edge_distance(gray_a: np.ndarray, gray_b: np.ndarray) -> float:
    edges_a = cv2.Canny(gray_a, 60, 160)
    edges_b = cv2.Canny(gray_b, 60, 160)
    return float(np.mean(cv2.absdiff(edges_a, edges_b))) / 255.0


def _mean_diff(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.mean(cv2.absdiff(a, b))) / 255.0


def _sharpness(gray: np.ndarray) -> float:
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def _classify(
    *, hist_distance: float, edge_distance: float, mean_diff: float,
    sharpness_delta: float, brightness_before: float, brightness_after: float,
    brightness_next: float, motion_before: float, motion_after: float,
) -> tuple[CutType, float]:
    signal = 0.4 * min(1.0, hist_distance * 2) + 0.35 * min(1.0, edge_distance * 3) \
        + 0.25 * min(1.0, mean_diff * 3)

    flash_spike = brightness_after - brightness_before
    flash_revert = brightness_after - brightness_next
    if flash_spike > 0.35 and flash_revert > 0.25:
        return "flash_cut", min(1.0, signal + 0.2)

    if motion_before > 0.55 and motion_after > 0.55:
        return "whip_hidden_cut", max(0.25, signal * 0.7)

    if 0.15 < hist_distance < 0.35 and edge_distance < 0.12 and sharpness_delta < 40:
        # Gradual color blend without a hard edge/texture jump.
        return "dissolve", max(0.2, signal * 0.6)

    if signal >= 0.35:
        return "hard_cut", signal

    return "unknown", signal


def detect_cuts(path: Path, *, content_threshold: float = 27.0) -> list[CutCandidate]:
    """Detect and classify cuts by combining several independent signals.

    Boundaries are proposed by PySceneDetect, then each boundary is scored
    against histogram/edge/pixel-diff/sharpness deltas plus a brightness and
    motion window to separate hard cuts from flash cuts, whip-hidden cuts,
    and dissolves. Confidence reflects signal agreement, not a single
    threshold crossing.
    """
    manager = SceneManager()
    manager.add_detector(ContentDetector(threshold=content_threshold, min_scene_len=2))
    manager.detect_scenes(open_video(str(path)))
    scenes = manager.get_scene_list()
    if len(scenes) <= 1:
        return []

    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise ValueError(f"cannot open reference video: {path}")
    fps = capture.get(cv2.CAP_PROP_FPS) or 24.0

    def frame_at(index: int) -> np.ndarray | None:
        capture.set(cv2.CAP_PROP_POS_FRAMES, max(0, index))
        ok, image = capture.read()
        return image if ok else None

    candidates: list[CutCandidate] = []
    try:
        for start, _end in scenes[1:]:
            frame_index = start.frame_num
            before = frame_at(frame_index - 2)
            after = frame_at(frame_index)
            next_frame = frame_at(frame_index + 2)
            if before is None or after is None:
                continue
            gray_before = cv2.cvtColor(before, cv2.COLOR_BGR2GRAY)
            gray_after = cv2.cvtColor(after, cv2.COLOR_BGR2GRAY)

            hist_distance = _hist_distance(before, after)
            edge_distance = _edge_distance(gray_before, gray_after)
            mean_diff = _mean_diff(before, after)
            sharpness_before = _sharpness(gray_before)
            sharpness_after = _sharpness(gray_after)
            sharpness_delta = abs(sharpness_after - sharpness_before)

            brightness_before = float(gray_before.mean()) / 255.0
            brightness_after = float(gray_after.mean()) / 255.0
            brightness_next = (
                float(cv2.cvtColor(next_frame, cv2.COLOR_BGR2GRAY).mean()) / 255.0
                if next_frame is not None else brightness_after
            )

            far_before = frame_at(frame_index - 6)
            far_after = frame_at(frame_index + 6)
            motion_before = (
                _mean_diff(far_before, before) if far_before is not None else 0.0
            )
            motion_after = (
                _mean_diff(after, far_after) if far_after is not None else 0.0
            )

            cut_type, confidence = _classify(
                hist_distance=hist_distance, edge_distance=edge_distance,
                mean_diff=mean_diff, sharpness_delta=sharpness_delta,
                brightness_before=brightness_before, brightness_after=brightness_after,
                brightness_next=brightness_next,
                motion_before=motion_before, motion_after=motion_after,
            )
            candidates.append(
                CutCandidate(
                    frame_index=frame_index,
                    sec=frame_index / fps,
                    hist_distance=hist_distance,
                    edge_distance=edge_distance,
                    mean_diff=mean_diff,
                    sharpness_delta=sharpness_delta,
                    cut_type=cut_type,
                    confidence=confidence,
                )
            )
    finally:
        capture.release()
    return candidates


__all__ = ["CutCandidate", "detect_cuts"]
