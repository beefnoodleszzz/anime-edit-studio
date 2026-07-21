"""CLIP 语义嵌入:镜头关键帧向量化 + 文本查询向量,支撑自然语言检索。

本地 open-clip(ViT-B-32,MPS),免费离线。向量存 shots.embedding(float32 BLOB)。
"""
from __future__ import annotations

import numpy as np

from . import db

_MODEL = "ViT-B-32"
_PRETRAINED = "laion2b_s34b_b79k"
_cache: dict = {}


def _load():
    if _cache:
        return _cache["model"], _cache["preprocess"], _cache["tokenizer"], _cache["device"]
    import open_clip
    import torch

    device = "mps" if torch.backends.mps.is_available() else "cpu"
    model, _, preprocess = open_clip.create_model_and_transforms(_MODEL, pretrained=_PRETRAINED)
    tokenizer = open_clip.get_tokenizer(_MODEL)
    model = model.to(device).eval()
    _cache.update(model=model, preprocess=preprocess, tokenizer=tokenizer, device=device)
    return model, preprocess, tokenizer, device


def embed_asset(asset_id: str, batch: int = 16, force: bool = False) -> int:
    import torch
    from PIL import Image

    conn = db.connect()
    asset = db.asset_by_id(conn, asset_id)
    if not asset:
        conn.close()
        raise ValueError(f"未找到素材: {asset_id}")
    if not force and conn.execute(
            "SELECT COUNT(*) c FROM shots WHERE asset_id=? AND embedding IS NOT NULL",
            (asset["id"],)).fetchone()["c"]:  # 缓存:已向量化则跳过
        conn.close()
        return 0
    rows = conn.execute(
        "SELECT id, keyframe FROM shots WHERE asset_id=? AND keyframe IS NOT NULL", (asset["id"],)
    ).fetchall()
    model, preprocess, _, device = _load()

    n = 0
    for i in range(0, len(rows), batch):
        chunk = rows[i:i + batch]
        imgs = [preprocess(Image.open(r["keyframe"]).convert("RGB")) for r in chunk]
        with torch.no_grad():
            feats = model.encode_image(torch.stack(imgs).to(device))
            feats = feats / feats.norm(dim=-1, keepdim=True)
        vecs = feats.cpu().numpy().astype("float32")
        for r, v in zip(chunk, vecs):
            conn.execute("UPDATE shots SET embedding=? WHERE id=?", (v.tobytes(), r["id"]))
            n += 1
    conn.commit()
    conn.close()
    return n


def embed_text(text: str) -> np.ndarray:
    import torch

    model, _, tokenizer, device = _load()
    with torch.no_grad():
        feat = model.encode_text(tokenizer([text]).to(device))
        feat = feat / feat.norm(dim=-1, keepdim=True)
    return feat.cpu().numpy()[0].astype("float32")
