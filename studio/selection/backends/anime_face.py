"""Anime face detection backend (REFACTOR.md §7.1).

Two implementations behind the same Protocol:

- ``OnnxAnimeFaceBackend``: real detector via onnxruntime, kept Python-3.11
  compatible (no Resolve environment upgrade). Needs a weights file the
  operator supplies locally (``kit/models/`` or ``AES_ANIME_FACE_MODEL``);
  never downloaded automatically at runtime.
- ``HeuristicAnimeFaceBackend``: dependency-free fallback used whenever the
  ONNX backend/weights are unavailable. It reuses the existing saliency
  subject-box operator (``VisualAnalyzer._subject_box``) as a face-region
  proxy and derives a coarse frontal/eye estimate from pixel symmetry — this
  is intentionally low-confidence and must never be reported as a real face
  detector's output (REFACTOR.md §7.1: "无模型时必须明确降级，并降低置信度").
"""
from __future__ import annotations

import os
from pathlib import Path

import cv2
import numpy as np

from studio.selection.backends.protocols import BackendStatus, FaceDetection
from studio.selection.schemas import BoundingBox

ANIME_FACE_VERSION = "anime-face-1.0.0"
REPO = Path(__file__).resolve().parents[3]
DEFAULT_WEIGHTS = REPO / "kit" / "models" / "anime_face" / "model.onnx"
_EDGE_MARGIN = 0.02


def _weights_path() -> Path:
    override = os.environ.get("AES_ANIME_FACE_MODEL")
    return Path(override) if override else DEFAULT_WEIGHTS


def _touches_edge(box: BoundingBox) -> bool:
    return (
        box.x <= _EDGE_MARGIN
        or box.y <= _EDGE_MARGIN
        or box.x + box.w >= 1.0 - _EDGE_MARGIN
        or box.y + box.h >= 1.0 - _EDGE_MARGIN
    )


class OnnxAnimeFaceBackend:
    """Real anime face detector. ``status.available`` is False without weights."""

    def __init__(self, weights_path: Path | None = None) -> None:
        path = weights_path or _weights_path()
        self._session = None
        fallback: str | None = None
        try:
            if not path.is_file():
                raise FileNotFoundError(str(path))
            import onnxruntime as ort  # noqa: PLC0415

            self._session = ort.InferenceSession(
                str(path), providers=["CPUExecutionProvider"]
            )
        except Exception as exc:  # noqa: BLE001
            fallback = f"{type(exc).__name__}: {exc}"
        self.status = BackendStatus(
            backend="anime_face_onnx",
            available=self._session is not None,
            version=ANIME_FACE_VERSION,
            device="cpu",
            weights_path=str(path),
            fallback=fallback,
        )

    def detect(self, frame: np.ndarray) -> list[FaceDetection]:
        if self._session is None:
            raise RuntimeError("OnnxAnimeFaceBackend unavailable; check .status first")
        # Real inference path: left for an operator-supplied model. Input/
        # output layout depends on the chosen anime-face ONNX export, so this
        # intentionally raises rather than guessing a wrong contract.
        raise NotImplementedError(
            "wire this to the specific anime-face ONNX export's input/output "
            "contract once weights are provisioned; use HeuristicAnimeFaceBackend "
            "until then"
        )


class HeuristicAnimeFaceBackend:
    """Zero-weight fallback: coarse frontal/eye estimate from pixel symmetry."""

    def __init__(self) -> None:
        self.status = BackendStatus(
            backend="anime_face_heuristic",
            available=True,
            version=ANIME_FACE_VERSION,
            device="cpu",
            weights_path=None,
            fallback="no ONNX weights provisioned; using saliency+symmetry heuristic",
        )

    def detect(self, frame: np.ndarray) -> list[FaceDetection]:
        from studio.asset_intelligence.visual.analyzer import VisualAnalyzer

        height, width = frame.shape[:2]
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        box = VisualAnalyzer._subject_box(gray, hsv)
        if box.confidence < 0.25:
            return []
        x0, y0 = int(box.x * width), int(box.y * height)
        x1, y1 = int((box.x + box.width) * width), int((box.y + box.height) * height)
        # A face sits in the upper portion of a saliency-detected subject box.
        face_y1 = y0 + max(1, int((y1 - y0) * 0.6))
        region = gray[y0:face_y1, x0:x1]
        if region.size == 0:
            return []
        frontal_probability = self._symmetry_score(region)
        eyes_visible_ratio = self._eye_blob_score(region)
        bbox = BoundingBox(
            x=x0 / width, y=y0 / height,
            w=max((x1 - x0) / width, 1e-3), h=max((face_y1 - y0) / height, 1e-3),
        )
        return [
            FaceDetection(
                bbox=bbox,
                frontal_probability=frontal_probability,
                eyes_visible_ratio=eyes_visible_ratio,
                gaze="viewer" if frontal_probability >= 0.6 else "uncertain",
                touches_frame_edge=_touches_edge(bbox),
                # Heuristic path is a deliberate degradation: cap confidence
                # well below what a real face detector would report.
                confidence=min(0.35, 0.15 + 0.2 * box.confidence),
            )
        ]

    @staticmethod
    def _symmetry_score(region: np.ndarray) -> float:
        flipped = np.fliplr(region).astype(np.float32)
        original = region.astype(np.float32)
        diff = np.abs(original - flipped).mean()
        return float(np.clip(1.0 - diff / 90.0, 0.0, 1.0))

    @staticmethod
    def _eye_blob_score(region: np.ndarray) -> float:
        upper_half = region[: max(1, region.shape[0] // 2), :]
        if upper_half.size == 0:
            return 0.0
        threshold = float(np.quantile(upper_half, 0.20))
        dark_mask = (upper_half <= threshold).astype(np.uint8) * 255
        contours, _ = cv2.findContours(dark_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        blobs = [c for c in contours if cv2.contourArea(c) >= 4]
        return float(np.clip(len(blobs) / 2.0, 0.0, 1.0))


def create_anime_face_backend(weights_path: Path | None = None):
    """Prefer the real ONNX detector; fall back without ever raising."""
    onnx_backend = OnnxAnimeFaceBackend(weights_path)
    if onnx_backend.status.available:
        return onnx_backend
    return HeuristicAnimeFaceBackend()


__all__ = [
    "ANIME_FACE_VERSION",
    "HeuristicAnimeFaceBackend",
    "OnnxAnimeFaceBackend",
    "create_anime_face_backend",
]
