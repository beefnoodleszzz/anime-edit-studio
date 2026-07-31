"""Auxiliary video-quality backend (DOVER-shaped, REFACTOR.md §7.4).

Whatever backend answers here is auxiliary evidence only — it must never be
allowed to override the technical hard gate, character/series identity, or
a missing stable landing (REFACTOR.md §7.4). There is no pip-installable
``dover`` package with a stable API this codebase can depend on, so:

- ``DoverVideoQualityBackend`` is a real-backend slot: available only if a
  ``dover`` import succeeds (an operator-vendored install), never assumed.
- ``HeuristicVideoQualityBackend`` is the always-available fallback: cheap
  classical technical/aesthetic proxies, explicitly labelled as such so
  nothing downstream mistakes it for a DOVER score.
"""
from __future__ import annotations

import math

import cv2
import numpy as np

from studio.selection.backends.protocols import BackendStatus

VIDEO_QUALITY_VERSION = "video-quality-1.0.0"


class DoverVideoQualityBackend:
    def __init__(self) -> None:
        fallback: str | None = None
        available = False
        try:
            import dover  # noqa: F401

            available = True
        except Exception as exc:  # noqa: BLE001
            fallback = f"{type(exc).__name__}: {exc}"
        self.status = BackendStatus(
            backend="video_quality_dover", available=available,
            version=VIDEO_QUALITY_VERSION, fallback=fallback,
        )

    def score(self, frames: list[np.ndarray]) -> dict[str, float]:
        if not self.status.available:
            raise RuntimeError("DoverVideoQualityBackend unavailable; check .status first")
        raise NotImplementedError(
            "wire this to the vendored DOVER inference API once provisioned; "
            "use HeuristicVideoQualityBackend until then"
        )


class HeuristicVideoQualityBackend:
    """Cheap technical/aesthetic proxy, not a DOVER substitute."""

    def __init__(self) -> None:
        self.status = BackendStatus(
            backend="video_quality_heuristic", available=True,
            version=VIDEO_QUALITY_VERSION,
            fallback="DOVER not installed; using sharpness/colorfulness heuristic aux scores",
        )

    def score(self, frames: list[np.ndarray]) -> dict[str, float]:
        if not frames:
            return {"technical_quality_aux": 0.0, "aesthetic_quality_aux": 0.0}
        sharpness_scores, colorfulness_scores = [], []
        for frame in frames:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
            sharpness_scores.append(1.0 - math.exp(-sharpness / 150.0))
            b, g, r = frame[..., 0].astype(np.float32), frame[..., 1].astype(np.float32), frame[..., 2].astype(np.float32)
            rg, yb = r - g, 0.5 * (r + g) - b
            colorfulness = math.sqrt(rg.std() ** 2 + yb.std() ** 2) + 0.3 * math.sqrt(
                rg.mean() ** 2 + yb.mean() ** 2
            )
            colorfulness_scores.append(min(1.0, colorfulness / 100.0))
        return {
            "technical_quality_aux": float(np.mean(sharpness_scores)),
            "aesthetic_quality_aux": float(np.mean(colorfulness_scores)),
        }


def create_video_quality_backend():
    dover = DoverVideoQualityBackend()
    if dover.status.available:
        return dover
    return HeuristicVideoQualityBackend()


__all__ = [
    "VIDEO_QUALITY_VERSION",
    "DoverVideoQualityBackend",
    "HeuristicVideoQualityBackend",
    "create_video_quality_backend",
]
