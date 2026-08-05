"""Dependency-free single-frame heuristics used by the reference analyzer.

Saliency subject box, dominant color palette, and a coarse frontal/eye
estimate — all cheap OpenCV, no learned model. These are soft descriptive
signals for the *Demo* video's own screen language, not a footage quality
gate (footage itself is pre-curated externally), so a low-confidence
heuristic is an honest fit rather than a placeholder for a "real" model.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import cv2
import numpy as np


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


@dataclass(frozen=True)
class SubjectBox:
    x: float
    y: float
    width: float
    height: float
    confidence: float


@dataclass(frozen=True)
class FaceEstimate:
    frontal_probability: float
    eyes_visible_ratio: float
    confidence: float


def subject_box(gray: np.ndarray, hsv: np.ndarray) -> SubjectBox:
    height, width = gray.shape
    edges = cv2.Canny(gray, 60, 160).astype(np.float32) / 255.0
    saturation = hsv[..., 1].astype(np.float32) / 255.0
    yy, xx = np.mgrid[0:height, 0:width]
    center = np.exp(
        -(((xx - width / 2) / (0.55 * width)) ** 2
          + ((yy - height / 2) / (0.65 * height)) ** 2)
    )
    saliency = cv2.GaussianBlur(0.65 * edges + 0.35 * saturation, (0, 0), 7)
    saliency *= center
    threshold = float(np.quantile(saliency, 0.82))
    mask = (saliency >= threshold).astype(np.uint8) * 255
    kernel = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE, (max(3, width // 40) | 1, max(3, height // 40) | 1)
    )
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    candidates = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        area = w * h / (width * height)
        if area < 0.01:
            continue
        cx, cy = (x + w / 2) / width, (y + h / 2) / height
        score = area * (1.2 - 0.5 * math.hypot(cx - 0.5, cy - 0.48))
        candidates.append((score, x, y, w, h, area))
    if not candidates:
        return SubjectBox(x=0.25, y=0.15, width=0.5, height=0.7, confidence=0.15)
    _, x, y, w, h, area = max(candidates)
    pad_x, pad_y = int(w * 0.12), int(h * 0.12)
    x0, y0 = max(0, x - pad_x), max(0, y - pad_y)
    x1, y1 = min(width, x + w + pad_x), min(height, y + h + pad_y)
    return SubjectBox(
        x=x0 / width, y=y0 / height,
        width=(x1 - x0) / width, height=(y1 - y0) / height,
        confidence=_clamp(0.35 + area),
    )


def dominant_palette(image: np.ndarray) -> list[str]:
    small = cv2.resize(image, (64, 64), interpolation=cv2.INTER_AREA)
    rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
    quantized = (rgb // 32).reshape(-1, 3)
    colors, counts = np.unique(quantized, axis=0, return_counts=True)
    top = colors[np.argsort(counts)[-5:][::-1]]
    return ["#{:02x}{:02x}{:02x}".format(*(color.astype(int) * 32 + 16)) for color in top]


def _symmetry_score(region: np.ndarray) -> float:
    flipped = np.fliplr(region).astype(np.float32)
    original = region.astype(np.float32)
    diff = np.abs(original - flipped).mean()
    return float(np.clip(1.0 - diff / 90.0, 0.0, 1.0))


def _eye_blob_score(region: np.ndarray) -> float:
    upper_half = region[: max(1, region.shape[0] // 2), :]
    if upper_half.size == 0:
        return 0.0
    threshold = float(np.quantile(upper_half, 0.20))
    dark_mask = (upper_half <= threshold).astype(np.uint8) * 255
    contours, _ = cv2.findContours(dark_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    blobs = [c for c in contours if cv2.contourArea(c) >= 4]
    return float(np.clip(len(blobs) / 2.0, 0.0, 1.0))


def estimate_face(frame: np.ndarray) -> FaceEstimate:
    """Coarse frontal/eye estimate from the saliency subject box's upper
    portion. Deliberately low-confidence — never a substitute for a real
    detector, just enough signal for the Demo's own screen-language read."""
    height, width = frame.shape[:2]
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    box = subject_box(gray, hsv)
    if box.confidence < 0.25:
        return FaceEstimate(frontal_probability=0.0, eyes_visible_ratio=0.0, confidence=0.0)
    x0, y0 = int(box.x * width), int(box.y * height)
    x1, y1 = int((box.x + box.width) * width), int((box.y + box.height) * height)
    face_y1 = y0 + max(1, int((y1 - y0) * 0.6))
    region = gray[y0:face_y1, x0:x1]
    if region.size == 0:
        return FaceEstimate(frontal_probability=0.0, eyes_visible_ratio=0.0, confidence=0.0)
    return FaceEstimate(
        frontal_probability=_symmetry_score(region),
        eyes_visible_ratio=_eye_blob_score(region),
        confidence=min(0.35, 0.15 + 0.2 * box.confidence),
    )


__all__ = ["FaceEstimate", "SubjectBox", "dominant_palette", "estimate_face", "subject_box"]
