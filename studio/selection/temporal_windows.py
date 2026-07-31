"""Generate precise ShotWindow candidates inside one Shot (REFACTOR.md §8.3).

Two coarse passes locate *where inside the shot* something worth cutting on
happens, then each located span becomes one or more concrete ``ShotWindow``
candidates with full technical/portrait/action/editability profiles:

- Portrait spans: continuous stretches of a real, stable frontal/eye-open
  face (min 0.20-0.35s — a single detected frame never becomes
  ``direct_gaze``).
- Action spans: built around the existing whole-frame ``ActionPeakDetector``
  maxima, expanded into the three offset windows REFACTOR.md §8.3
  specifies, plus exact-length variants for any requested slot durations.

If neither pass finds anything, one ``generic`` span covering the whole
shot is still produced — REFACTOR.md's hard gate must still see and reject
bad material rather than a slot silently going empty because nothing
"interesting" was found.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from studio.asset_intelligence.motion.action_peak import ActionPeakDetector
from studio.selection.action_analyzer import analyze_action
from studio.selection.backends.protocols import AnimeFaceBackend
from studio.selection.config import SelectionThresholds, load_thresholds
from studio.selection.editability import compute_editability
from studio.selection.portrait_analyzer import analyze_portrait
from studio.selection.schemas import (
    ActionProfile,
    MotionDirection,
    PortraitProfile,
    ShotWindow,
    WindowKind,
)
from studio.selection.technical_gate import compute_technical_profile

TEMPORAL_WINDOWS_VERSION = "temporal-windows-1.0.0"
MIN_STABLE_SEC = 0.20
DEFAULT_MAX_LENGTH_SEC = 1.20
PORTRAIT_SAMPLE_HZ = 6.0
_ACTION_OFFSETS = ((0.35, 0.20), (0.20, 0.35), (0.10, 0.50))
_FRONTAL_THRESHOLD = 0.60
_EYES_OPEN_THRESHOLD = 0.50


@dataclass(frozen=True)
class WindowSpan:
    start_sec: float
    end_sec: float
    anchor_sec: float
    kind: WindowKind


def _clip(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _portrait_frame_series(
    media: Path, *, start_sec: float, end_sec: float, face_backend: AnimeFaceBackend,
    sample_hz: float = PORTRAIT_SAMPLE_HZ,
) -> list[tuple[float, float, float]]:
    """Return (sec, frontal_probability, eyes_visible_ratio) per sampled frame."""
    if not face_backend.status.available:
        return []
    capture = cv2.VideoCapture(str(media))
    if not capture.isOpened():
        return []
    series: list[tuple[float, float, float]] = []
    try:
        for sec in np.arange(start_sec, end_sec, 1 / sample_hz):
            capture.set(cv2.CAP_PROP_POS_MSEC, float(sec) * 1000)
            ok, frame = capture.read()
            if not ok:
                continue
            faces = face_backend.detect(frame)
            best = max(faces, key=lambda face: face.confidence, default=None)
            if best is None:
                series.append((float(sec), 0.0, 0.0))
            else:
                series.append((float(sec), best.frontal_probability, best.eyes_visible_ratio))
    finally:
        capture.release()
    return series


def _runs(flags: list[bool]) -> list[tuple[int, int]]:
    runs, start = [], None
    for index, flag in enumerate(flags + [False]):
        if flag and start is None:
            start = index
        elif not flag and start is not None:
            runs.append((start, index - 1))
            start = None
    return runs


def portrait_spans(
    media: Path, *, start_sec: float, end_sec: float, face_backend: AnimeFaceBackend,
    min_stable_sec: float = MIN_STABLE_SEC, max_length_sec: float = DEFAULT_MAX_LENGTH_SEC,
) -> list[WindowSpan]:
    series = _portrait_frame_series(media, start_sec=start_sec, end_sec=end_sec, face_backend=face_backend)
    if len(series) < 2:
        return []
    frontal_flags = [frontal >= _FRONTAL_THRESHOLD for _, frontal, _ in series]
    eyes_flags = [eyes >= _EYES_OPEN_THRESHOLD for _, _, eyes in series]
    spans: list[WindowSpan] = []

    for run_start, run_end in _runs(frontal_flags):
        span_start, span_end = series[run_start][0], series[run_end][0]
        if span_end - span_start < min_stable_sec:
            continue
        span_end = min(span_end, span_start + max_length_sec)
        # A run that starts mid-series (preceded by a sampled non-frontal
        # frame) captured an actual non-frontal -> frontal transition;
        # widen the span to include the turn itself. A run already frontal
        # at the very first sampled frame is a held pose, not a turn.
        if run_start > 0:
            kind: WindowKind = "turn_to_camera"
            span_start = max(start_sec, series[run_start - 1][0])
        else:
            kind = "direct_gaze"
        spans.append(
            WindowSpan(span_start, span_end, (span_start + span_end) / 2, kind)
        )

    for run_start, run_end in _runs(eyes_flags):
        if run_start > 0 and eyes_flags[run_start - 1]:
            continue  # not a closed->open transition, already inside an open run
        span_start, span_end = series[run_start][0], series[run_end][0]
        if span_end - span_start < min_stable_sec:
            continue
        span_end = min(span_end, span_start + max_length_sec)
        spans.append(WindowSpan(span_start, span_end, span_start, "eye_reveal"))

    return spans


def action_spans(
    media: Path, *, start_sec: float, end_sec: float, target_durations: list[float] | None = None,
    detector: ActionPeakDetector | None = None,
) -> list[WindowSpan]:
    detector = detector or ActionPeakDetector()
    peaks = detector.detect(media, start_sec=start_sec, end_sec=end_sec)
    spans: list[WindowSpan] = []
    for peak in peaks:
        anchor = start_sec + peak.sec
        for before, after in _ACTION_OFFSETS:
            span_start = _clip(anchor - before, start_sec, end_sec)
            span_end = _clip(anchor + after, start_sec, end_sec)
            if span_end - span_start >= MIN_STABLE_SEC:
                spans.append(WindowSpan(span_start, span_end, anchor, "action_peak"))
        for duration in target_durations or []:
            span_start = _clip(anchor - duration / 2, start_sec, end_sec)
            span_end = _clip(span_start + duration, start_sec, end_sec)
            span_start = _clip(span_end - duration, start_sec, end_sec)
            if span_end - span_start >= MIN_STABLE_SEC:
                spans.append(WindowSpan(span_start, span_end, anchor, "action_peak"))
    return spans


def generate_spans(
    media: Path, *, start_sec: float, end_sec: float, face_backend: AnimeFaceBackend,
    target_durations: list[float] | None = None,
) -> list[WindowSpan]:
    spans = portrait_spans(media, start_sec=start_sec, end_sec=end_sec, face_backend=face_backend)
    spans += action_spans(media, start_sec=start_sec, end_sec=end_sec, target_durations=target_durations)
    if not spans:
        spans = [WindowSpan(start_sec, end_sec, (start_sec + end_sec) / 2, "generic")]
    return spans


_ACTION_KINDS = {
    "anticipation", "action_peak", "impact", "transformation", "hero_landing",
}


def build_shot_window(
    shot_id: str, asset_id: str, media: Path, span: WindowSpan, *,
    face_backend: AnimeFaceBackend,
    shot_start_sec: float, shot_end_sec: float,
    target_duration_sec: float | None = None,
    entry_motion: MotionDirection = "none",
    exit_motion: MotionDirection = "none",
    preferred_entry: MotionDirection = "none",
    preferred_exit: MotionDirection = "none",
    thresholds: SelectionThresholds | None = None,
) -> ShotWindow:
    thresholds = thresholds or load_thresholds()
    gate = compute_technical_profile(
        media, start_sec=span.start_sec, end_sec=span.end_sec, thresholds=thresholds,
        face_backend=face_backend,
    )
    portrait = (
        analyze_portrait(
            list(_decode_frames(media, span.start_sec, span.end_sec)),
            face_backend=face_backend,
        )
        if span.kind not in _ACTION_KINDS
        else None
    )
    action = analyze_action(media, start_sec=span.start_sec, end_sec=span.end_sec) if span.kind in _ACTION_KINDS else None

    duration = span.end_sec - span.start_sec
    editability = compute_editability(
        shot_start_sec=shot_start_sec, shot_end_sec=shot_end_sec,
        window_start_sec=span.start_sec, window_end_sec=span.end_sec,
        target_duration_sec=target_duration_sec or duration,
        safe_crop_ratio=gate.subject.safe_crop_ratio,
        action=action, entry_motion=entry_motion, exit_motion=exit_motion,
        preferred_entry=preferred_entry, preferred_exit=preferred_exit,
    )

    window_id = f"{shot_id}:{span.kind}:{span.start_sec:.3f}-{span.end_sec:.3f}"
    return ShotWindow(
        id=window_id, shot_id=shot_id, asset_id=asset_id,
        start_sec=span.start_sec, end_sec=span.end_sec, anchor_sec=span.anchor_sec,
        kind=span.kind,
        technical=gate.technical,
        subject=gate.subject,
        portrait=portrait or PortraitProfile(),
        action=action or ActionProfile(),
        editability=editability,
    )


def _decode_frames(media: Path, start_sec: float, end_sec: float, count: int = 6):
    capture = cv2.VideoCapture(str(media))
    if not capture.isOpened():
        return
    try:
        for sec in np.linspace(start_sec, end_sec, count):
            capture.set(cv2.CAP_PROP_POS_MSEC, float(sec) * 1000)
            ok, frame = capture.read()
            if ok:
                yield frame
    finally:
        capture.release()


__all__ = [
    "TEMPORAL_WINDOWS_VERSION",
    "WindowSpan",
    "action_spans",
    "build_shot_window",
    "generate_spans",
    "portrait_spans",
]
