"""CLIP embedding pipeline with explicit model identities and atomic caches.

The aesthetic head and semantic encoder intentionally use different CLIP
weights.  The linear aesthetic head was trained in the OpenAI ViT-B/32 space;
semantic retrieval uses LAION weights.  Mixing those spaces is invalid.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Protocol

import cv2
import numpy as np

from studio.core.cache import ArrayCache, JsonCache
from studio.core.hashing import analysis_cache_key, file_sha256

EMBEDDING_MODEL = "open_clip/ViT-B-32"
EMBEDDING_MODEL_VERSION = "laion2b_s34b_b79k"
EMBEDDING_PIPELINE_VERSION = "clip-embedding-1.0.0"
AESTHETIC_MODEL = "open_clip/ViT-B-32-quickgelu+aesthetic-linear"
AESTHETIC_MODEL_VERSION = "openai+sa_0_4_vit_b_32_linear"
AESTHETIC_PIPELINE_VERSION = "aesthetic-1.0.0"


class EmbeddingBackend(Protocol):
    def aesthetic_scores(self, paths: list[Path]) -> np.ndarray: ...
    def image_embeddings(self, paths: list[Path]) -> np.ndarray: ...


class OpenClipBackend:
    """Lazy model backend; importing this module never loads model weights."""

    def __init__(self, head_path: Path, *, batch_size: int = 64):
        if not head_path.exists():
            raise FileNotFoundError(f"美学评分头不存在: {head_path}")
        self.head_path = head_path
        self.batch_size = batch_size
        self._semantic = None
        self._aesthetic = None

    @staticmethod
    def _device(torch) -> str:
        return "mps" if torch.backends.mps.is_available() else "cpu"

    def _load_semantic(self):
        if self._semantic is None:
            import open_clip
            import torch

            device = self._device(torch)
            model, _, preprocess = open_clip.create_model_and_transforms(
                "ViT-B-32", pretrained="laion2b_s34b_b79k"
            )
            self._semantic = (model.to(device).eval(), preprocess, device)
        return self._semantic

    def _load_aesthetic(self):
        if self._aesthetic is None:
            import open_clip
            import torch

            device = self._device(torch)
            model, _, preprocess = open_clip.create_model_and_transforms(
                "ViT-B-32-quickgelu", pretrained="openai"
            )
            state = torch.load(self.head_path, map_location=device)
            self._aesthetic = (
                model.to(device).eval(),
                preprocess,
                state["weight"].to(device).float(),
                state["bias"].to(device).float(),
                device,
            )
        return self._aesthetic

    def aesthetic_scores(self, paths: list[Path]) -> np.ndarray:
        import torch
        from PIL import Image

        model, preprocess, weight, bias, device = self._load_aesthetic()
        output = []
        for offset in range(0, len(paths), self.batch_size):
            images = [
                preprocess(Image.open(path).convert("RGB"))
                for path in paths[offset : offset + self.batch_size]
            ]
            with torch.inference_mode():
                features = model.encode_image(torch.stack(images).to(device)).float()
                features = features / features.norm(dim=-1, keepdim=True)
                output.append((features @ weight.t() + bias).squeeze(-1).cpu().numpy())
        return np.concatenate(output).astype(np.float32) if output else np.empty(0, np.float32)

    def image_embeddings(self, paths: list[Path]) -> np.ndarray:
        import torch
        from PIL import Image

        model, preprocess, device = self._load_semantic()
        output = []
        for offset in range(0, len(paths), self.batch_size):
            images = [
                preprocess(Image.open(path).convert("RGB"))
                for path in paths[offset : offset + self.batch_size]
            ]
            with torch.inference_mode():
                features = model.encode_image(torch.stack(images).to(device)).float()
                features = features / features.norm(dim=-1, keepdim=True)
                output.append(features.cpu().numpy())
        return np.concatenate(output).astype(np.float32) if output else np.empty((0, 512), np.float32)


def _candidate_frames(keyframe: Path) -> list[Path]:
    stem = keyframe.stem
    prefix = stem.rsplit("_c", 1)[0] if "_c" in stem else stem
    candidates = sorted(keyframe.parent.glob(f"{prefix}_c*.jpg"))
    return candidates or [keyframe]


def _sharpness(path: Path) -> float:
    image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if image is None:
        return -1.0
    score = float(cv2.Laplacian(image, cv2.CV_64F).var())
    return score * (0.2 if image.mean() / 255 < 0.06 else 1.0)


def encode_pending(
    conn: sqlite3.Connection,
    *,
    cache_root: Path,
    backend: EmbeddingBackend,
    asset_id: str | None = None,
    limit: int | None = None,
) -> int:
    """Choose representatives and encode shots missing a semantic embedding.

    Model calls are batched across shots.  A previous implementation invoked
    both CLIP models once per shot, which made a full-library analysis spend
    most of its time moving tiny tensors through the accelerator.
    """
    conn.row_factory = sqlite3.Row
    where = "WHERE s.embedding IS NULL AND s.keyframe IS NOT NULL"
    params: list[object] = []
    if asset_id:
        where += " AND s.asset_id=?"
        params.append(asset_id)
    sql = (
        "SELECT s.id,s.keyframe,a.sha256 FROM shots s "
        f"JOIN assets a ON a.id=s.asset_id {where} ORDER BY s.asset_id,s.idx"
    )
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    arrays = ArrayCache(cache_root)
    metadata = JsonCache(cache_root)
    pending: list[dict] = []
    uncached_aesthetic_paths: list[Path] = []
    for row in rows:
        candidates = _candidate_frames(Path(row["keyframe"]))
        aesthetic_key = analysis_cache_key(
            asset_hash=row["sha256"],
            model=AESTHETIC_MODEL,
            model_version=AESTHETIC_MODEL_VERSION,
            pipeline_version=AESTHETIC_PIPELINE_VERSION,
            parameters={
                "shot_id": row["id"],
                "candidate_sha256": [file_sha256(path) for path in candidates],
            },
        )
        cached_meta = metadata.get("aesthetic-v2", aesthetic_key)
        if cached_meta:
            pending.append(
                {
                    "row": row,
                    "aesthetic_key": aesthetic_key,
                    "candidates": candidates,
                    "best": Path(cached_meta["keyframe"]),
                    "aesthetic": (
                        float(cached_meta["score"])
                        if cached_meta["score"] is not None
                        else None
                    ),
                }
            )
        else:
            start = len(uncached_aesthetic_paths)
            uncached_aesthetic_paths.extend(candidates)
            pending.append(
                {
                    "row": row,
                    "aesthetic_key": aesthetic_key,
                    "candidates": candidates,
                    "aesthetic_slice": slice(start, len(uncached_aesthetic_paths)),
                }
            )

    all_aesthetic_scores: np.ndarray | None = None
    if uncached_aesthetic_paths:
        try:
            all_aesthetic_scores = backend.aesthetic_scores(uncached_aesthetic_paths)
            if all_aesthetic_scores.shape != (len(uncached_aesthetic_paths),):
                raise ValueError("美学模型返回数量与候选帧不一致")
        except (FileNotFoundError, RuntimeError):
            all_aesthetic_scores = None

    for item in pending:
        if "best" not in item:
            candidates = item["candidates"]
            if all_aesthetic_scores is not None:
                scores = all_aesthetic_scores[item["aesthetic_slice"]]
                best_index = int(np.argmax(scores))
                aesthetic = float(scores[best_index])
                method = "openai_clip_linear_head"
            else:
                best_index = int(np.argmax([_sharpness(path) for path in candidates]))
                aesthetic = None
                method = "sharpness_fallback"
            best = candidates[best_index]
            metadata.put(
                "aesthetic-v2",
                item["aesthetic_key"],
                {"keyframe": str(best), "score": aesthetic, "method": method},
            )
            item["best"] = best
            item["aesthetic"] = aesthetic

    uncached_embedding_items: list[dict] = []
    for item in pending:
        row = item["row"]
        best = item["best"]
        embedding_key = analysis_cache_key(
            asset_hash=row["sha256"],
            model=EMBEDDING_MODEL,
            model_version=EMBEDDING_MODEL_VERSION,
            pipeline_version=EMBEDDING_PIPELINE_VERSION,
            parameters={
                "shot_id": row["id"],
                "keyframe_sha256": file_sha256(best),
            },
        )
        vector = arrays.get("clip-v2", embedding_key)
        if vector is None:
            item["embedding_key"] = embedding_key
            uncached_embedding_items.append(item)
        else:
            item["vector"] = vector

    if uncached_embedding_items:
        vectors = backend.image_embeddings(
            [item["best"] for item in uncached_embedding_items]
        )
        if vectors.shape != (len(uncached_embedding_items), 512):
            raise ValueError("CLIP 模型返回维度无效")
        for item, vector in zip(uncached_embedding_items, vectors, strict=True):
            norm = float(np.linalg.norm(vector))
            if vector.shape != (512,) or not np.isfinite(vector).all() or norm < 1e-8:
                raise ValueError(f"无效 CLIP embedding: {item['row']['id']}")
            vector = (vector / norm).astype(np.float32)
            arrays.put("clip-v2", item["embedding_key"], vector)
            item["vector"] = vector

    with conn:
        conn.executemany(
                "UPDATE shots SET keyframe=?,aesthetic=?,embedding=? WHERE id=?",
            [
                (
                    str(item["best"]),
                    item["aesthetic"],
                    item["vector"].astype(np.float32).tobytes(),
                    item["row"]["id"],
                )
                for item in pending
            ],
            )
    return len(pending)


__all__ = [
    "AESTHETIC_PIPELINE_VERSION",
    "EMBEDDING_PIPELINE_VERSION",
    "EmbeddingBackend",
    "OpenClipBackend",
    "encode_pending",
]
