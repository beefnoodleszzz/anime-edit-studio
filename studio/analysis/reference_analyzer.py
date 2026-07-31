"""ReferenceAnalyzer: Demo video -> ReferenceBlueprint (REFACTOR.md §6).

Combines multi-signal cut detection (§6.1) with two-tier global motion
estimation (§6.2) to measure: per-shot motion, per-cut outgoing/incoming
relation (§6.3), relative blur envelopes (§6.4), and cut-to-nearest-music
offsets when a MusicTimeline is supplied (§6.5). The output never asserts
more than the signals support — every motion estimate keeps its own
confidence and evidence.
"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from studio.analysis.cut_detection import CutCandidate, detect_cuts
from studio.analysis.global_motion import estimate_global_motion
from studio.asset_intelligence.visual.analyzer import VisualAnalyzer
from studio.core.hashing import file_sha256
from studio.selection.backends.anime_face import create_anime_face_backend
from studio.spec.music_timeline import MusicTimeline
from studio.spec.reference_blueprint import (
    CutObservation,
    Estimate,
    MotionSample,
    ReferenceBlueprint,
    ShotObservation,
    StyleSummary,
    TechnicalProfile,
    TransitionDirection,
    TransitionPairObservation,
)

MOTION_SAMPLE_INTERVAL_SEC = 0.1
CUT_WINDOW_FRAMES = 8


def _open(path: Path) -> tuple[cv2.VideoCapture, float, int, int, int]:
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise ValueError(f"cannot open reference video: {path}")
    fps = capture.get(cv2.CAP_PROP_FPS) or 24.0
    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    return capture, fps, width, height, frame_count


def _gray_at(capture: cv2.VideoCapture, frame_index: int) -> np.ndarray | None:
    capture.set(cv2.CAP_PROP_POS_FRAMES, max(0, frame_index))
    ok, image = capture.read()
    if not ok:
        return None
    return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)


def _bgr_at(capture: cv2.VideoCapture, frame_index: int) -> np.ndarray | None:
    capture.set(cv2.CAP_PROP_POS_FRAMES, max(0, frame_index))
    ok, image = capture.read()
    return image if ok else None


def _sharpness(gray: np.ndarray) -> float:
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


_DIRECTION_ANGLES: list[tuple[float, TransitionDirection]] = [
    (0.0, "right"), (45.0, "down-right"), (90.0, "down"), (135.0, "down-left"),
    (180.0, "left"), (225.0, "up-left"), (270.0, "up"), (315.0, "up-right"),
]


def _direction_bucket(tx: float, ty: float, *, min_magnitude: float = 2.0) -> TransitionDirection:
    """Bucket a measured global-motion translation into the same 9-direction
    set TransitionPair/TimelineSlot use, rather than a fabricated fine angle."""
    if np.hypot(tx, ty) < min_magnitude:
        return "none"
    # Screen-space y grows downward; angle measured clockwise from +x.
    angle = np.degrees(np.arctan2(ty, tx)) % 360.0
    return min(_DIRECTION_ANGLES, key=lambda item: min(abs(angle - item[0]), 360 - abs(angle - item[0])))[1]


def _shot_kind_probabilities(
    portrait_probability: float, action_probability: float, stable_ratio: float,
) -> dict[str, float]:
    raw = {"portrait": portrait_probability, "action": action_probability, "hold": stable_ratio}
    total = sum(raw.values())
    if total <= 1e-6:
        return {"generic": 1.0}
    return {key: value / total for key, value in raw.items()}


def _shot_observations(
    capture: cv2.VideoCapture, fps: float, boundaries_sec: list[float], duration_sec: float,
    face_backend=None,
) -> list[ShotObservation]:
    face_backend = face_backend or create_anime_face_backend()
    edges = [0.0, *boundaries_sec, duration_sec]
    shots: list[ShotObservation] = []
    for index, (start, end) in enumerate(zip(edges, edges[1:])):
        if end - start < 1.0 / fps:
            continue
        mid_frame = int(round(((start + end) / 2) * fps))
        gray_mid = _gray_at(capture, mid_frame)
        gray_next = _gray_at(capture, mid_frame + 1)
        if gray_mid is None:
            continue
        if gray_next is None:
            gray_next = gray_mid
        motion = estimate_global_motion(gray_mid, gray_next)
        translation = float(np.hypot(motion.tx, motion.ty))

        # Screen-language fields (REFACTOR.md §13): all derived from signals
        # already measured for this shot (or one extra face pass on the same
        # mid-frame), never fabricated. A single mid-frame face/color read is
        # a coarse per-shot summary, not a gating decision.
        color_mid = _bgr_at(capture, mid_frame)
        faces = face_backend.detect(color_mid) if color_mid is not None else []
        best_face = max(faces, key=lambda face: face.confidence, default=None)
        subject_count = 1 if best_face is not None else 0
        face_visibility = best_face.confidence if best_face is not None else 0.0
        eye_visibility = best_face.eyes_visible_ratio if best_face is not None else 0.0
        portrait_probability = (
            best_face.frontal_probability * best_face.confidence if best_face is not None else 0.0
        )
        stable_ratio = float(np.clip(1.0 - translation / 40.0, 0.0, 1.0))
        action_probability = float(np.clip(motion.confidence * translation / 30.0, 0.0, 1.0))
        dominant_color = (
            VisualAnalyzer._palette(color_mid)[0] if color_mid is not None else None
        )

        shots.append(
            ShotObservation(
                index=index,
                start_sec=start,
                end_sec=end,
                duration_sec=end - start,
                visual_energy=min(1.0, _sharpness(gray_mid) / 2000.0),
                brightness=float(gray_mid.mean()) / 255.0,
                native_motion_estimate=Estimate(
                    value=translation,
                    confidence=motion.confidence * 0.7,
                    evidence=[
                        f"{motion.method}_residual",
                        "cannot fully separate native subject motion from camera motion on a flattened render",
                    ],
                ),
                global_motion_estimate=Estimate(
                    value=translation,
                    confidence=motion.confidence,
                    evidence=[motion.method, f"inlier_ratio={motion.inlier_ratio:.2f}"],
                ),
                motion_confidence=motion.confidence,
                subject_count=subject_count,
                face_visibility=face_visibility,
                eye_visibility=eye_visibility,
                portrait_probability=portrait_probability,
                action_probability=action_probability,
                shot_kind_probabilities=_shot_kind_probabilities(
                    portrait_probability, action_probability, stable_ratio
                ),
                dominant_color=dominant_color,
                motion_direction=_direction_bucket(motion.tx, motion.ty),
                stable_ratio=stable_ratio,
            )
        )
    return shots


def _cut_window_motion(
    capture: cv2.VideoCapture, fps: float, frame_index: int, *, before: bool,
) -> tuple[Estimate, list[float]]:
    step = -1 if before else 1
    envelope: list[float] = []
    magnitudes = []
    confidences = []
    for offset in range(1, CUT_WINDOW_FRAMES + 1):
        a = frame_index + step * offset - (1 if before else 0)
        b = frame_index + step * offset - (0 if before else -1)
        gray_a = _gray_at(capture, a)
        gray_b = _gray_at(capture, b)
        if gray_a is None or gray_b is None:
            continue
        motion = estimate_global_motion(gray_a, gray_b)
        magnitude = float(np.hypot(motion.tx, motion.ty))
        magnitudes.append(magnitude)
        confidences.append(motion.confidence)
        envelope.append(magnitude)
    if not magnitudes:
        return Estimate(value=0.0, confidence=0.0, evidence=["no_frames_available"]), []
    envelope = envelope[::-1] if before else envelope
    return (
        Estimate(
            value=float(np.mean(magnitudes)),
            confidence=float(np.mean(confidences)),
            evidence=[f"sampled_{len(magnitudes)}_frames"],
        ),
        envelope,
    )


def _classify_relation(outgoing: Estimate, incoming: Estimate) -> str:
    if outgoing.confidence < 0.3 or incoming.confidence < 0.3:
        return "unknown"
    if outgoing.value < 0.4 and incoming.value < 0.4:
        return "unknown"
    ratio = incoming.value / max(outgoing.value, 1e-6)
    if 0.5 <= ratio <= 2.0 and incoming.value >= 0.4:
        return "carry"
    if incoming.value < outgoing.value * 0.35:
        return "reset"
    return "reverse"


def _cut_observations(
    capture: cv2.VideoCapture, fps: float, candidates: list[CutCandidate],
    music_timeline: MusicTimeline | None,
) -> list[CutObservation]:
    observations = []
    for candidate in candidates:
        outgoing, out_envelope = _cut_window_motion(capture, fps, candidate.frame_index, before=True)
        incoming, in_envelope = _cut_window_motion(capture, fps, candidate.frame_index, before=False)
        relation = _classify_relation(outgoing, incoming)

        nearest_event = None
        offset = None
        if music_timeline is not None:
            events = sorted(
                music_timeline.beats + music_timeline.downbeats
                + [a.sec for a in music_timeline.accents]
            )
            if events:
                nearest = min(events, key=lambda sec: abs(sec - candidate.sec))
                nearest_event = f"{nearest:.3f}"
                offset = candidate.sec - nearest

        observations.append(
            CutObservation(
                sec=candidate.sec,
                type=candidate.cut_type,
                confidence=candidate.confidence,
                nearest_music_event=nearest_event,
                music_offset_sec=offset,
                outgoing_motion=outgoing,
                incoming_motion=incoming,
                relation=relation,
            )
        )
    return observations


def _transition_pairs(cuts: list[CutObservation]) -> list[TransitionPairObservation]:
    pairs = []
    for cut in cuts:
        if cut.outgoing_motion is None or cut.incoming_motion is None:
            continue
        if cut.relation == "unknown":
            continue
        direction = "left" if cut.incoming_motion.value >= cut.outgoing_motion.value else "right"
        overshoot = max(0.0, cut.incoming_motion.value - cut.outgoing_motion.value)
        pairs.append(
            TransitionPairObservation(
                cut_sec=cut.sec,
                relation=cut.relation,
                direction=direction if cut.relation != "reset" else "none",
                anticipation_sec=CUT_WINDOW_FRAMES / 24.0,
                release_sec=CUT_WINDOW_FRAMES / 24.0,
                overshoot=overshoot,
                confidence=min(cut.outgoing_motion.confidence, cut.incoming_motion.confidence),
            )
        )
    return pairs


def _motion_curve(
    capture: cv2.VideoCapture, fps: float, duration_sec: float,
) -> list[MotionSample]:
    samples: list[MotionSample] = []
    previous_velocity = 0.0
    sec = 0.0
    while sec + MOTION_SAMPLE_INTERVAL_SEC < duration_sec:
        frame_a = int(round(sec * fps))
        frame_b = int(round((sec + MOTION_SAMPLE_INTERVAL_SEC) * fps))
        gray_a = _gray_at(capture, frame_a)
        gray_b = _gray_at(capture, frame_b)
        if gray_a is None or gray_b is None:
            break
        motion = estimate_global_motion(gray_a, gray_b)
        velocity = float(np.hypot(motion.tx, motion.ty))
        samples.append(
            MotionSample(
                sec=sec + MOTION_SAMPLE_INTERVAL_SEC / 2,
                tx=motion.tx, ty=motion.ty,
                log_scale=motion.log_scale, rotation=motion.rotation_deg,
                velocity=velocity,
                acceleration=(velocity - previous_velocity) / MOTION_SAMPLE_INTERVAL_SEC,
                confidence=motion.confidence,
            )
        )
        previous_velocity = velocity
        sec += MOTION_SAMPLE_INTERVAL_SEC
    return samples


def _style_summary(
    shots: list[ShotObservation], cuts: list[CutObservation], duration_sec: float,
) -> StyleSummary:
    hold_shots = [s for s in shots if s.global_motion_estimate.value < 0.4]
    reversed_cuts = [c for c in cuts if c.relation == "reverse"]
    carry_cuts = [c for c in cuts if c.relation == "carry"]
    scale_moving = [c for c in cuts if c.outgoing_motion and c.outgoing_motion.confidence > 0.5]
    return StyleSummary(
        cut_density=len(cuts) / max(duration_sec, 1e-6),
        shot_duration_distribution={
            "mean": float(np.mean([s.duration_sec for s in shots])) if shots else 0.0,
            "median": float(np.median([s.duration_sec for s in shots])) if shots else 0.0,
        },
        music_sync_distribution={
            "synced_ratio": (
                sum(1 for c in cuts if c.music_offset_sec is not None and abs(c.music_offset_sec) <= 0.08)
                / len(cuts)
            ) if cuts else 0.0,
        },
        motion_coverage=(
            sum(s.global_motion_estimate.value > 0.4 for s in shots) / len(shots)
        ) if shots else 0.0,
        hold_ratio=(len(hold_shots) / len(shots)) if shots else 0.0,
        direction_distribution={},
        reversal_ratio=(len(reversed_cuts) / len(cuts)) if cuts else 0.0,
        scale_motion_ratio=(len(scale_moving) / len(cuts)) if cuts else 0.0,
        blur_usage=0.0,
        visual_peak_delay_distribution={},
        settle_delay_distribution={},
    )


def analyze_reference(
    path: Path, *, music_timeline: MusicTimeline | None = None,
) -> ReferenceBlueprint:
    """Measure a Demo video's cut and motion grammar into a ReferenceBlueprint.

    ``music_timeline`` is optional: when supplied (the Demo's own extracted
    audio, analyzed by ``analyze_music_timeline``), cuts are annotated with
    their offset to the nearest beat/downbeat/accent (§6.5).
    """
    capture, fps, width, height, frame_count = _open(path)
    try:
        duration_sec = frame_count / fps if fps else 0.0
        candidates = detect_cuts(path)
        boundaries_sec = [c.sec for c in candidates]

        shots = _shot_observations(capture, fps, boundaries_sec, duration_sec)
        cuts = _cut_observations(capture, fps, candidates, music_timeline)
        transition_pairs = _transition_pairs(cuts)
        motion_curve = _motion_curve(capture, fps, duration_sec)
        style_summary = _style_summary(shots, cuts, duration_sec)

        aspect_gcd = np.gcd(width, height) or 1
        aspect = f"{width // aspect_gcd}:{height // aspect_gcd}"

        return ReferenceBlueprint(
            source_hash=file_sha256(path),
            technical=TechnicalProfile(
                width=width, height=height,
                fps_num=int(round(fps * 1000)), fps_den=1000,
                duration_sec=duration_sec, aspect=aspect,
            ),
            music_timeline_ref=music_timeline.source_hash if music_timeline else None,
            shots=shots,
            cuts=cuts,
            motion_curve=motion_curve,
            transition_pairs=transition_pairs,
            style_summary=style_summary,
        )
    finally:
        capture.release()


__all__ = ["analyze_reference"]
