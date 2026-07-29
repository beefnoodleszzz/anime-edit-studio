"""Measure how camera/subject motion flows through cuts in a finished video.

The reference (a.mp4) does not merely *move* — it moves *directionally*, and the
direction relates to the neighbouring shots: some cuts carry the motion straight
through (the drag that pulls you into the next shot), some slam it into a
reversal for accent, and the directions used vary across the body of the edit.

Median motion alone cannot tell those apart: a dumb uniform pan applied to every
shot scores a perfect carry rate with zero variety, and reads as a conveyor belt.
So this module measures three things together — how much of the picture is
actually moving, how often direction survives a cut, and how varied the
directions are — and only the three together describe the reference's method.

Deterministic and render-agnostic: it consumes any mp4, so the same measurement
applies to the reference and to our own output.
"""
from __future__ import annotations

import math
from pathlib import Path

import cv2
import numpy as np
from pydantic import BaseModel, ConfigDict, Field
from scenedetect import ContentDetector, SceneManager, open_video

CAMERA_FLOW_VERSION = "camera-flow-measure-1.1.0"

#: Below this median flow magnitude a shot reads as static, so its direction is
#: noise and must not be counted as a carry or a reversal.
DIRECTIONAL_MIN_MAGNITUDE = 0.45
#: Directions within this angle survive the cut as "the same push".
CARRY_ANGLE_DEG = 60.0
#: Beyond this angle the cut reads as a deliberate reversal, not a carry.
REVERSAL_ANGLE_DEG = 120.0

#: Analysis frames are scaled *isotropically* to this height.  A fixed WxH box
#: would squash one axis on anything that is not 16:9 — a 1080x1080 delivery
#: forced into 320x180 compresses vertical motion 1.8x more than horizontal, so
#: vertical camera moves measure as weaker than they are and the direction
#: histogram tilts sideways.  Scaling by height keeps both axes on one scale and
#: keeps flow comparable across sources, since every frame ends up the same
#: number of pixels tall.
_ANALYSIS_HEIGHT = 240
_DIRECTION_BINS = (
    "right", "down-right", "down", "down-left",
    "left", "up-left", "up", "up-right",
)


class ShotFlow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    index: int = Field(..., ge=0)
    start_sec: float = Field(..., ge=0)
    end_sec: float = Field(..., gt=0)
    vx: float
    vy: float
    magnitude: float = Field(..., ge=0)
    direction: str
    directional: bool


class CutCarry(BaseModel):
    sec: float = Field(..., ge=0)
    angle_delta_deg: float | None = Field(
        None, description="None 表示两侧至少一边是静止镜，方向无意义"
    )
    carried: bool
    reversed_: bool = Field(..., alias="reversed")

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class CameraFlowMeasurement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: str = CAMERA_FLOW_VERSION
    source: str
    duration_sec: float = Field(..., gt=0)
    shot_count: int = Field(..., ge=0)
    shots: list[ShotFlow]
    cuts: list[CutCarry]
    directional_shot_frac: float = Field(..., ge=0, le=1)
    carry_rate: float = Field(..., ge=0, le=1)
    reversal_rate: float = Field(..., ge=0, le=1)
    direction_entropy: float = Field(..., ge=0, le=1)
    median_magnitude: float = Field(..., ge=0)
    mean_abs_vx: float = Field(..., ge=0)


def _frame(capture: cv2.VideoCapture, sec: float) -> np.ndarray | None:
    capture.set(cv2.CAP_PROP_POS_MSEC, sec * 1000)
    ok, image = capture.read()
    if not ok:
        return None
    height, width = image.shape[:2]
    if height <= 0 or width <= 0:
        return None
    scale = _ANALYSIS_HEIGHT / height
    return cv2.resize(
        image,
        (max(2, round(width * scale)), _ANALYSIS_HEIGHT),
        interpolation=cv2.INTER_AREA,
    )


def _flow(first: np.ndarray, second: np.ndarray) -> tuple[float, float, float]:
    a = cv2.cvtColor(first, cv2.COLOR_BGR2GRAY)
    b = cv2.cvtColor(second, cv2.COLOR_BGR2GRAY)
    flow = cv2.calcOpticalFlowFarneback(a, b, None, 0.5, 3, 15, 3, 5, 1.2, 0)
    return (
        float(np.median(flow[..., 0])),
        float(np.median(flow[..., 1])),
        float(np.median(np.hypot(flow[..., 0], flow[..., 1]))),
    )


def _direction_name(vx: float, vy: float) -> str:
    angle = math.degrees(math.atan2(vy, vx))
    return _DIRECTION_BINS[int(((angle + 202.5) % 360) // 45)]


def _angle_delta(a: ShotFlow, b: ShotFlow) -> float:
    dot = a.vx * b.vx + a.vy * b.vy
    norm = math.hypot(a.vx, a.vy) * math.hypot(b.vx, b.vy)
    if norm <= 1e-9:
        return 180.0
    return math.degrees(math.acos(max(-1.0, min(1.0, dot / norm))))


def _normalized_entropy(directions: list[str]) -> float:
    """Shannon entropy over the eight direction bins, scaled to 0..1.

    A uniform pan collapses to a single bin (0.0); the reference spreads its
    pushes across several (well above 0).  This is the term that catches an
    edit that moves a lot but always the same way.
    """
    if not directions:
        return 0.0
    counts = [directions.count(name) for name in _DIRECTION_BINS]
    total = sum(counts)
    if total <= 0:
        return 0.0
    entropy = -sum(
        (value / total) * math.log(value / total)
        for value in counts if value > 0
    )
    return float(entropy / math.log(len(_DIRECTION_BINS)))


def _scene_bounds(path: Path, fps: float, duration: float) -> list[tuple[float, float]]:
    manager = SceneManager()
    manager.add_detector(ContentDetector(threshold=27, min_scene_len=6))
    manager.detect_scenes(open_video(str(path)))
    scenes = manager.get_scene_list()
    if not scenes:
        return [(0.0, duration)]
    return [(start.seconds, end.seconds) for start, end in scenes]


def measure_camera_flow(
    path: Path, *, samples_per_shot: int = 3
) -> CameraFlowMeasurement:
    """Measure per-shot flow direction and how it survives each cut."""
    if samples_per_shot < 1:
        raise ValueError("samples_per_shot 必须 >= 1")
    if not path.is_file():
        raise FileNotFoundError(f"视频不存在: {path}")
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise ValueError(f"无法打开视频: {path}")
    try:
        fps = capture.get(cv2.CAP_PROP_FPS) or 24.0
        frame_count = capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0
        duration = float(frame_count / fps) if fps > 0 else 0.0
        if duration <= 0:
            raise ValueError(f"视频时长无效: {path}")
        bounds = _scene_bounds(path, fps, duration)
        step = 1.0 / fps
        shots: list[ShotFlow] = []
        for index, (start_sec, end_sec) in enumerate(bounds):
            length = max(1e-3, end_sec - start_sec)
            # Sample strictly inside the shot: a pair straddling the cut would
            # measure the cut itself, not the shot's movement.
            offsets = [
                start_sec + length * (0.2 + 0.6 * position / max(1, samples_per_shot - 1))
                if samples_per_shot > 1
                else start_sec + length * 0.5
                for position in range(samples_per_shot)
            ]
            vectors = []
            for offset in offsets:
                if offset + step >= end_sec:
                    continue
                first = _frame(capture, offset)
                second = _frame(capture, offset + step)
                if first is None or second is None:
                    continue
                vectors.append(_flow(first, second))
            if vectors:
                vx = float(np.mean([item[0] for item in vectors]))
                vy = float(np.mean([item[1] for item in vectors]))
                magnitude = float(np.mean([item[2] for item in vectors]))
            else:
                vx = vy = magnitude = 0.0
            shots.append(
                ShotFlow(
                    index=index,
                    start_sec=round(start_sec, 6),
                    end_sec=round(max(end_sec, start_sec + 1e-3), 6),
                    vx=vx,
                    vy=vy,
                    magnitude=magnitude,
                    direction=(
                        _direction_name(vx, vy)
                        if magnitude >= DIRECTIONAL_MIN_MAGNITUDE
                        else "static"
                    ),
                    directional=magnitude >= DIRECTIONAL_MIN_MAGNITUDE,
                )
            )
    finally:
        capture.release()

    cuts: list[CutCarry] = []
    for left, right in zip(shots, shots[1:]):
        if not (left.directional and right.directional):
            cuts.append(
                CutCarry(sec=right.start_sec, angle_delta_deg=None,
                         carried=False, reversed=False)
            )
            continue
        delta = _angle_delta(left, right)
        cuts.append(
            CutCarry(
                sec=right.start_sec,
                angle_delta_deg=round(delta, 3),
                carried=delta <= CARRY_ANGLE_DEG,
                reversed=delta >= REVERSAL_ANGLE_DEG,
            )
        )

    directional = [shot for shot in shots if shot.directional]
    return CameraFlowMeasurement(
        source=str(path),
        duration_sec=duration,
        shot_count=len(shots),
        shots=shots,
        cuts=cuts,
        directional_shot_frac=(len(directional) / len(shots)) if shots else 0.0,
        carry_rate=(
            sum(cut.carried for cut in cuts) / len(cuts) if cuts else 0.0
        ),
        reversal_rate=(
            sum(cut.reversed_ for cut in cuts) / len(cuts) if cuts else 0.0
        ),
        direction_entropy=_normalized_entropy(
            [shot.direction for shot in directional]
        ),
        median_magnitude=(
            float(np.median([shot.magnitude for shot in shots])) if shots else 0.0
        ),
        mean_abs_vx=(
            float(np.mean([abs(shot.vx) for shot in shots])) if shots else 0.0
        ),
    )


class CameraFlowComparison(BaseModel):
    """How far our cut sits from the reference on each directional term."""

    model_config = ConfigDict(extra="forbid")
    version: str = CAMERA_FLOW_VERSION
    reference: str
    subject: str
    metrics: dict[str, dict[str, float]]
    passed: bool
    failures: list[str]


#: Tolerances are asymmetric on purpose, and the asymmetry follows the measured
#: reference rather than intuition.  a.mp4 scores carry 0.150 / reversal 0.200 /
#: entropy 0.901: it does *not* slide every shot the same way.  So carry rate is
#: banded on both sides (too low is a slideshow, too high is a conveyor belt),
#: while entropy and reversal only have floors — a uniform pan lands at entropy
#: 0.0, carry 1.0, reversal 0.0 and fails three checks at once, which is exactly
#: the failure this gate exists to catch.  Being *more* directional or moving
#: *more* than the reference is not a defect, so those have floors only.
#: The reversal floor is tighter than the reference's own value so that zero
#: reversals cannot squeak through: an edit that never turns is the conveyor
#: belt again, just measured from the other side.
_TOLERANCES: dict[str, tuple[float | None, float | None]] = {
    "directional_shot_frac": (-0.15, None),
    "carry_rate": (-0.15, 0.20),
    "reversal_rate": (-0.12, None),
    "direction_entropy": (-0.15, None),
}
#: Magnitude is compared as a ratio, not a difference: an absolute delta is
#: meaningless across differently scaled footage.
_MIN_MAGNITUDE_RATIO = 0.65


def compare_camera_flow(
    subject: CameraFlowMeasurement, reference: CameraFlowMeasurement
) -> CameraFlowComparison:
    metrics: dict[str, dict[str, float]] = {}
    failures: list[str] = []
    for name, (low, high) in _TOLERANCES.items():
        got = float(getattr(subject, name))
        want = float(getattr(reference, name))
        delta = got - want
        metrics[name] = {"subject": got, "reference": want, "delta": delta}
        if low is not None and delta < low:
            failures.append(f"{name} 低于参考容差: {got:.3f} vs {want:.3f}")
        elif high is not None and delta > high:
            failures.append(f"{name} 高于参考容差: {got:.3f} vs {want:.3f}")
    ratio = (
        subject.median_magnitude / reference.median_magnitude
        if reference.median_magnitude > 1e-6 else 1.0
    )
    metrics["median_magnitude"] = {
        "subject": subject.median_magnitude,
        "reference": reference.median_magnitude,
        "ratio": ratio,
    }
    if ratio < _MIN_MAGNITUDE_RATIO:
        failures.append(
            f"median_magnitude 不足参考的 {_MIN_MAGNITUDE_RATIO:.0%}: "
            f"{subject.median_magnitude:.3f} vs {reference.median_magnitude:.3f}"
        )
    return CameraFlowComparison(
        reference=reference.source,
        subject=subject.source,
        metrics=metrics,
        passed=not failures,
        failures=failures,
    )


__all__ = [
    "CAMERA_FLOW_VERSION",
    "CameraFlowComparison",
    "CameraFlowMeasurement",
    "CutCarry",
    "ShotFlow",
    "compare_camera_flow",
    "measure_camera_flow",
]
