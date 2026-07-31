"""Dense embedding backends for ShotWindow similarity/novelty/identity (REFACTOR.md §7.2).

``OpenClipEmbeddingBackend`` reuses the same weights already used for shot
retrieval (``studio.asset_intelligence.embeddings.encoder``:
``open_clip/ViT-B-32`` + ``laion2b_s34b_b79k``) so a window's frame/subject/
face embeddings live in the same semantic space as ``shots.embedding``.
``Dinov2EmbeddingBackend`` is optional per REFACTOR.md §7.2 ("DINOv3 只作为
可选后端；权重不可用时不得阻塞系统") — it degrades to unavailable rather than
downloading weights at runtime or blocking the pipeline.
"""
from __future__ import annotations

import numpy as np

from studio.selection.backends.protocols import BackendStatus

DENSE_EMBEDDING_VERSION = "dense-embedding-1.0.0"
OPEN_CLIP_MODEL = "open_clip/ViT-B-32"
OPEN_CLIP_MODEL_VERSION = "laion2b_s34b_b79k"


class OpenClipEmbeddingBackend:
    """Same semantic space as ``shots.embedding``; lazy-loaded, CPU/MPS only."""

    def __init__(self) -> None:
        self._model = None
        fallback: str | None = None
        available = False
        try:
            import open_clip  # noqa: F401
            import torch  # noqa: F401

            available = True
        except Exception as exc:  # noqa: BLE001
            fallback = f"{type(exc).__name__}: {exc}"
        self.status = BackendStatus(
            backend="dense_embedding_openclip",
            available=available,
            version=f"{OPEN_CLIP_MODEL}:{OPEN_CLIP_MODEL_VERSION}",
            device="cpu",
            weights_path=None,
            fallback=fallback,
        )

    def _load(self):
        if self._model is None:
            import open_clip
            import torch

            model, _, preprocess = open_clip.create_model_and_transforms(
                "ViT-B-32", pretrained=OPEN_CLIP_MODEL_VERSION
            )
            device = "mps" if torch.backends.mps.is_available() else "cpu"
            self._model = (model.to(device).eval(), preprocess, device)
        return self._model

    def embed(self, frame: np.ndarray) -> np.ndarray | None:
        if not self.status.available:
            return None
        import cv2
        import torch
        from PIL import Image

        model, preprocess, device = self._load()
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        image = preprocess(Image.fromarray(rgb)).unsqueeze(0).to(device)
        with torch.inference_mode():
            features = model.encode_image(image).float()
            features = features / features.norm(dim=-1, keepdim=True)
        return features.squeeze(0).cpu().numpy().astype(np.float32)


class Dinov2EmbeddingBackend:
    """Optional composition/identity backend. Never downloads at runtime."""

    def __init__(self) -> None:
        self._model = None
        fallback: str | None = None
        available = False
        try:
            import torch  # noqa: F401

            hub_dir = torch.hub.get_dir()
            from pathlib import Path

            cached = list(Path(hub_dir).glob("facebookresearch_dinov2_*"))
            available = bool(cached)
            if not available:
                fallback = "no locally-cached DINOv2 weights under torch.hub cache"
        except Exception as exc:  # noqa: BLE001
            fallback = f"{type(exc).__name__}: {exc}"
        self.status = BackendStatus(
            backend="dense_embedding_dinov2",
            available=available,
            version="dinov2_vits14",
            device="cpu",
            weights_path=None,
            fallback=fallback,
        )

    def _load(self):
        if self._model is None:
            import torch

            self._model = torch.hub.load(
                "facebookresearch/dinov2", "dinov2_vits14", trust_repo=True, source="local"
            ).eval()
        return self._model

    def embed(self, frame: np.ndarray) -> np.ndarray | None:
        if not self.status.available:
            return None
        import cv2
        import torch
        import torchvision.transforms as T
        from PIL import Image

        model = self._load()
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        preprocess = T.Compose(
            [T.Resize((224, 224)), T.ToTensor(), T.Normalize([0.5] * 3, [0.5] * 3)]
        )
        image = preprocess(Image.fromarray(rgb)).unsqueeze(0)
        with torch.inference_mode():
            features = model(image).float()
            features = features / features.norm(dim=-1, keepdim=True)
        return features.squeeze(0).numpy().astype(np.float32)


class NullEmbeddingBackend:
    """Neither backend available: never blocks the pipeline, always degrades."""

    def __init__(self, fallback: str = "no embedding backend available") -> None:
        self.status = BackendStatus(
            backend="dense_embedding_null", available=False, fallback=fallback,
        )

    def embed(self, frame: np.ndarray) -> np.ndarray | None:
        return None


def create_dense_embedding_backend(prefer_dinov2: bool = False):
    if prefer_dinov2:
        dinov2 = Dinov2EmbeddingBackend()
        if dinov2.status.available:
            return dinov2
    openclip = OpenClipEmbeddingBackend()
    if openclip.status.available:
        return openclip
    return NullEmbeddingBackend()


__all__ = [
    "DENSE_EMBEDDING_VERSION",
    "Dinov2EmbeddingBackend",
    "NullEmbeddingBackend",
    "OpenClipEmbeddingBackend",
    "create_dense_embedding_backend",
]
