"""Technical hard gate for ShotWindow candidates (REFACTOR.md §9).

Independent of aesthetic/portrait/action scoring: a window that fails here
must never enter the final cut, no matter how high its other scores are.
Extends the existing multi-frame sampling pattern from
``studio.asset_intelligence.visual.temporal_quality`` (sampling loop,
bad-frame ratio, quantile aggregation, crop-fitness) with the fields that
pattern does not cover yet: watermark, split black/white clipping, longest
run of consecutive unusable frames, subject-tracking continuity and
(optionally, when an AnimeFaceBackend is supplied) face-crop safety.

A dark shot is not a defect; only measured black/white *clipping* is
(REFACTOR.md §9: "暗调镜头不是缺陷，真正的黑白截断才是缺陷").
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from studio.asset_intelligence.visual.analyzer import VisualAnalyzer
from studio.asset_intelligence.visual.temporal_quality import _square_crop_fitness
from studio.selection.backends.protocols import AnimeFaceBackend
from studio.selection.config import SelectionThresholds, load_thresholds
from studio.selection.schemas import BoundingBox, SubjectProfile, TechnicalProfile

TECHNICAL_GATE_VERSION = "technical-gate-1.0.0"
_SUBJECT_VISIBLE_CONFIDENCE = 0.35
_TRACK_IOU_THRESHOLD = 0.20
_BAD_FRAME_SHARPNESS = 0.30
_BAD_FRAME_COMPRESSION = 0.48
_BAD_FRAME_CLIPPED = 0.42
_SUBTITLE_MIN_BOX_WIDTH = 0.20
_WATERMARK_CORNER_FRACTION = 0.12
_WATERMARK_EDGE_BAND = (0.05, 0.35)
_WATERMARK_MAX_VARIANCE = 0.015


@dataclass(frozen=True)
class FrameSample:
    sec: float
    sharpness: float
    compression: float
    black_clip: float
    white_clip: float
    subtitle_present: bool
    subject_box: BoundingBox
    subject_confidence: float
    safe_crop: float
    corner_edges: tuple[float, float, float, float]
    bad: bool


@dataclass(frozen=True)
class TechnicalGateResult:
    technical: TechnicalProfile
    subject: SubjectProfile
    samples: list[FrameSample]


def _corner_edge_density(gray: np.ndarray) -> tuple[float, float, float, float]:
    height, width = gray.shape
    cw, ch = max(1, int(width * _WATERMARK_CORNER_FRACTION)), max(
        1, int(height * _WATERMARK_CORNER_FRACTION)
    )
    edges = cv2.Canny(gray, 60, 160)
    corners = (
        edges[:ch, :cw],
        edges[:ch, width - cw :],
        edges[height - ch :, :cw],
        edges[height - ch :, width - cw :],
    )
    return tuple(float(np.mean(corner) / 255.0) for corner in corners)  # type: ignore[return-value]


def _frame_sample(frame: np.ndarray, sec: float, target_aspect: float) -> FrameSample:
    height, width = frame.shape[:2]
    scale = min(1.0, 640.0 / max(width, 1))
    if scale < 1.0:
        frame = cv2.resize(frame, (round(width * scale), round(height * scale)))
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    sharp_score = 1.0 - math.exp(-sharpness / 115.0)
    compression = VisualAnalyzer._blockiness(gray)
    black_clip = float(np.mean(gray <= 3))
    white_clip = float(np.mean(gray >= 252))
    clipped = black_clip + white_clip
    subtitle = VisualAnalyzer._subtitle_region(gray)
    max_box_width = max((box[2] for box in subtitle["boxes"]), default=0.0)
    subtitle_present = bool(subtitle["present"]) and max_box_width >= _SUBTITLE_MIN_BOX_WIDTH
    box = VisualAnalyzer._subject_box(gray, hsv)
    subject_box = BoundingBox(x=box.x, y=box.y, w=box.width, h=box.height)
    safe_crop = _square_crop_fitness(box, width / max(height, 1), target_aspect)
    bad = sharp_score < _BAD_FRAME_SHARPNESS or compression > _BAD_FRAME_COMPRESSION or clipped > _BAD_FRAME_CLIPPED
    return FrameSample(
        sec=sec,
        sharpness=sharpness,
        compression=compression,
        black_clip=black_clip,
        white_clip=white_clip,
        subtitle_present=subtitle_present,
        subject_box=subject_box,
        subject_confidence=box.confidence,
        safe_crop=safe_crop,
        corner_edges=_corner_edge_density(gray),
        bad=bad,
    )


def _iou(a: BoundingBox, b: BoundingBox) -> float:
    ax1, ay1, ax2, ay2 = a.x, a.y, a.x + a.w, a.y + a.h
    bx1, by1, bx2, by2 = b.x, b.y, b.x + b.w, b.y + b.h
    ix = max(0.0, min(ax2, bx2) - max(ax1, bx1))
    iy = max(0.0, min(ay2, by2) - max(ay1, by1))
    intersection = ix * iy
    union = a.w * a.h + b.w * b.h - intersection
    return intersection / union if union > 0 else 0.0


def _longest_run(flags: list[bool]) -> int:
    longest = current = 0
    for flag in flags:
        current = current + 1 if flag else 0
        longest = max(longest, current)
    return longest


def _watermark_probability(samples: list[FrameSample]) -> float:
    """A watermark is a small decal that sits in the same corner, at roughly
    constant edge density, across the whole window — unlike scene content,
    which moves. Low variance in a corner's edge density across samples,
    with a plausible-magnitude edge density, is the signature used here."""
    if len(samples) < 3:
        return 0.0
    per_corner = np.array([sample.corner_edges for sample in samples])  # (n, 4)
    means = per_corner.mean(axis=0)
    variances = per_corner.var(axis=0)
    scores = []
    low, high = _WATERMARK_EDGE_BAND
    for mean, variance in zip(means, variances):
        if low <= mean <= high and variance <= _WATERMARK_MAX_VARIANCE:
            scores.append(float(np.clip(1.0 - variance / _WATERMARK_MAX_VARIANCE, 0.0, 1.0)))
        else:
            scores.append(0.0)
    return float(max(scores))


def _face_crop_ratio(frames: list[np.ndarray], face_backend: AnimeFaceBackend | None) -> float | None:
    if face_backend is None or not getattr(face_backend, "status", None) or not face_backend.status.available:
        return None
    cropped = 0
    detected = 0
    for frame in frames:
        faces = face_backend.detect(frame)
        for face in faces:
            detected += 1
            if face.touches_frame_edge:
                cropped += 1
    return cropped / detected if detected else 0.0


def compute_technical_profile(
    media: Path,
    *,
    start_sec: float,
    end_sec: float,
    sample_count: int = 7,
    target_aspect: float = 1.0,
    face_backend: AnimeFaceBackend | None = None,
    thresholds: SelectionThresholds | None = None,
) -> TechnicalGateResult:
    thresholds = thresholds or load_thresholds()
    gate = thresholds.technical_gate
    duration = end_sec - start_sec
    failure_reasons: list[str] = []
    if duration < gate.minimum_window_sec:
        failure_reasons.append("window_too_short")

    capture = cv2.VideoCapture(str(media))
    if not capture.isOpened():
        raise ValueError(f"无法打开媒体: {media}")
    margin = min(0.08, duration * 0.08) if duration > 0 else 0.0
    times = np.linspace(start_sec + margin, max(start_sec + margin, end_sec - margin), sample_count)
    samples: list[FrameSample] = []
    frames: list[np.ndarray] = []
    try:
        for sec in times:
            capture.set(cv2.CAP_PROP_POS_MSEC, float(sec) * 1000)
            ok, frame = capture.read()
            if ok:
                samples.append(_frame_sample(frame, float(sec), target_aspect))
                frames.append(frame)
    finally:
        capture.release()

    if not samples:
        failure_reasons.append("undecodable")
        technical = TechnicalProfile(passed=False, failure_reasons=failure_reasons)
        return TechnicalGateResult(technical=technical, subject=SubjectProfile(), samples=[])

    bad_flags = [sample.bad for sample in samples]
    bad_frame_ratio = sum(bad_flags) / len(bad_flags)
    subtitle_frame_ratio = sum(sample.subtitle_present for sample in samples) / len(samples)
    watermark_probability = _watermark_probability(samples)
    black_clip_ratio = float(np.mean([sample.black_clip for sample in samples]))
    white_clip_ratio = float(np.mean([sample.white_clip for sample in samples]))
    sharpness_p10 = float(np.quantile([sample.sharpness for sample in samples], 0.10))
    compression_score = float(np.mean([sample.compression for sample in samples]))
    subject_visible_ratio = sum(
        sample.subject_confidence >= _SUBJECT_VISIBLE_CONFIDENCE for sample in samples
    ) / len(samples)
    safe_crop_ratio = float(np.quantile([sample.safe_crop for sample in samples], 0.20))
    consecutive_unusable = _longest_run(bad_flags)

    track_confidence = 0.5
    if len(samples) >= 2:
        ious = [
            _iou(samples[i].subject_box, samples[i + 1].subject_box)
            for i in range(len(samples) - 1)
        ]
        track_confidence = sum(iou >= _TRACK_IOU_THRESHOLD for iou in ious) / len(ious)

    face_crop_ratio = _face_crop_ratio(frames, face_backend)

    if bad_frame_ratio > gate.maximum_bad_frame_ratio:
        failure_reasons.append("bad_frame_ratio")
    if subtitle_frame_ratio > gate.maximum_subtitle_frame_ratio:
        failure_reasons.append("subtitle")
    if watermark_probability > gate.maximum_watermark_probability:
        failure_reasons.append("watermark")
    if black_clip_ratio > gate.maximum_black_clip_ratio:
        failure_reasons.append("black_clip")
    if white_clip_ratio > gate.maximum_white_clip_ratio:
        failure_reasons.append("white_clip")
    if sharpness_p10 < gate.minimum_sharpness_p10:
        failure_reasons.append("low_sharpness")
    if compression_score > gate.maximum_compression_score:
        failure_reasons.append("compression_damage")
    if subject_visible_ratio < gate.minimum_subject_visible_ratio:
        failure_reasons.append("subject_not_visible")
    if safe_crop_ratio < gate.minimum_safe_crop_ratio:
        failure_reasons.append("unsafe_crop")
    if consecutive_unusable > gate.maximum_consecutive_unusable_frames:
        failure_reasons.append("consecutive_unusable_frames")
    if face_crop_ratio is not None and face_crop_ratio > 0.5:
        failure_reasons.append("face_cropped")

    technical = TechnicalProfile(
        passed=not failure_reasons,
        failure_reasons=failure_reasons,
        bad_frame_ratio=bad_frame_ratio,
        subtitle_frame_ratio=subtitle_frame_ratio,
        watermark_probability=watermark_probability,
        black_clip_ratio=black_clip_ratio,
        white_clip_ratio=white_clip_ratio,
        sharpness_p10=sharpness_p10,
        compression_score=compression_score,
        subject_visible_ratio=subject_visible_ratio,
    )
    mean_box = samples[len(samples) // 2].subject_box
    subject = SubjectProfile(
        bbox_at_anchor=mean_box,
        mean_bbox=mean_box,
        track_confidence=track_confidence,
        safe_crop_ratio=safe_crop_ratio,
    )
    return TechnicalGateResult(technical=technical, subject=subject, samples=samples)


__all__ = [
    "TECHNICAL_GATE_VERSION",
    "FrameSample",
    "TechnicalGateResult",
    "compute_technical_profile",
]
