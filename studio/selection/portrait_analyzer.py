"""Character-showcase (portrait) analysis over a sampled frame sequence (REFACTOR.md §10).

Real face detection (``AnimeFaceBackend``) drives this; WD Tagger tags such
as ``looking_at_viewer`` are auxiliary evidence only, never a replacement
for it (REFACTOR.md §7.1/§10). Gaze is reported as one of a small fixed set
of buckets, not a fabricated 3D angle.
"""
from __future__ import annotations

from studio.selection.backends.protocols import AnimeFaceBackend, FaceDetection
from studio.selection.schemas import GazeDirection, PortraitProfile

PORTRAIT_ANALYZER_VERSION = "portrait-analyzer-1.0.0"

_WD_GAZE_TAGS = ("looking_at_viewer",)
_WD_EXPRESSION_TAGS = (
    "smile", "open_mouth", "blush", "angry", "crying", "surprised",
    "expressionless", "frown", "smirk", "clenched_teeth",
)

_WEIGHTS = {
    "face_visible_ratio": 0.20,
    "frontal_probability": 0.15,
    "viewer_gaze_probability": 0.20,
    "eye_visible_ratio": 0.10,
    "expression_strength": 0.10,
    "composition_score": 0.10,
    "temporal_stability": 0.15,
}


def _best_face(faces: list[FaceDetection]) -> FaceDetection | None:
    return max(faces, key=lambda face: face.confidence, default=None)


def _rule_of_thirds_score(bbox) -> float:
    cx, cy = bbox.x + bbox.w / 2, bbox.y + bbox.h / 2
    targets = ((1 / 3, 1 / 3), (2 / 3, 1 / 3), (1 / 3, 2 / 3), (2 / 3, 2 / 3), (0.5, 0.4))
    best = min(((cx - tx) ** 2 + (cy - ty) ** 2) ** 0.5 for tx, ty in targets)
    return max(0.0, 1.0 - best / 0.5)


def analyze_portrait(
    frames: list,
    *,
    face_backend: AnimeFaceBackend,
    wd_general_tags: dict[str, float] | None = None,
) -> PortraitProfile:
    """``frames`` is a list of BGR ``np.ndarray`` samples spanning the window."""
    wd_general_tags = wd_general_tags or {}
    per_frame_faces = [(_best_face(face_backend.detect(frame)) if face_backend.status.available else None) for frame in frames]
    detected = [face for face in per_frame_faces if face is not None]

    if not detected:
        return PortraitProfile(gaze_direction="occluded")

    face_visible_ratio = len(detected) / len(frames)
    frontal_probability = sum(face.frontal_probability for face in detected) / len(detected)
    eye_visible_ratio = sum(face.eyes_visible_ratio for face in detected) / len(detected)

    wd_gaze_evidence = max((wd_general_tags.get(tag, 0.0) for tag in _WD_GAZE_TAGS), default=0.0)
    if wd_general_tags:
        viewer_gaze_probability = (
            0.5 * frontal_probability + 0.25 * eye_visible_ratio + 0.25 * wd_gaze_evidence
        )
    else:
        viewer_gaze_probability = 0.6 * frontal_probability + 0.4 * eye_visible_ratio

    gaze_votes: dict[str, int] = {}
    for face in detected:
        gaze_votes[face.gaze] = gaze_votes.get(face.gaze, 0) + 1
    gaze_direction: GazeDirection = max(gaze_votes, key=gaze_votes.get)  # type: ignore[arg-type]

    expression_strength = max(
        (wd_general_tags.get(tag, 0.0) for tag in _WD_EXPRESSION_TAGS), default=0.0
    )
    if not wd_general_tags:
        # No tagger evidence: fall back to how much the face's read varies
        # across the window, a weak but non-fabricated proxy for expression
        # change versus a held neutral pose.
        spread = max(face.frontal_probability for face in detected) - min(
            face.frontal_probability for face in detected
        )
        expression_strength = min(1.0, spread * 1.5)

    composition_score = sum(_rule_of_thirds_score(face.bbox) for face in detected) / len(detected)

    stable_transitions = 0
    for a, b in zip(per_frame_faces, per_frame_faces[1:]):
        if a is not None and b is not None and abs(a.frontal_probability - b.frontal_probability) < 0.3:
            stable_transitions += 1
    temporal_stability = (
        stable_transitions / (len(per_frame_faces) - 1) if len(per_frame_faces) > 1 else face_visible_ratio
    )

    profile_values = {
        "face_visible_ratio": face_visible_ratio,
        "frontal_probability": frontal_probability,
        "viewer_gaze_probability": viewer_gaze_probability,
        "eye_visible_ratio": eye_visible_ratio,
        "expression_strength": expression_strength,
        "composition_score": composition_score,
        "temporal_stability": temporal_stability,
    }
    portrait_score = sum(profile_values[key] * weight for key, weight in _WEIGHTS.items())

    return PortraitProfile(
        **profile_values,
        gaze_direction=gaze_direction,
        portrait_score=max(0.0, min(1.0, portrait_score)),
    )


__all__ = ["PORTRAIT_ANALYZER_VERSION", "analyze_portrait"]
