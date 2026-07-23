"""最优帧选择 + CLIP 语义嵌入。

本步在 pipeline 里先于 analyze/tag 运行,负责敲定每镜最终 `keyframe`:
- 从 shots 抽出的 K 个候选帧里,用 LAION 美学分挑「最好看」的一帧(权重缺失则回退清晰度启发式);
- 把该帧存为代表 keyframe、写美学分 aesthetic、并用 laion2b ViT-B-32 编码为检索向量。

检索向量用 laion2b(开放词表更强);美学评分用 OpenAI 版(见 aesthetic.py),两者互不影响。
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

from . import config, db, shots as shots_mod

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


def _sharpness(path: str) -> float:
    """降级用:Laplacian 方差 × 非过暗守卫,避免选中糊帧/黑帧。"""
    import cv2

    img = cv2.imread(path)
    if img is None:
        return -1.0
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    lap = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    brightness = float(gray.mean()) / 255
    return lap * (0.2 if brightness < 0.06 else 1.0)


def _pick_best_frames(cand_lists: list[list[str]]) -> tuple[list[int], list[float | None]]:
    """对每镜的候选帧列表选最优帧下标 + 其美学分(降级时分数为 None)。"""
    from . import aesthetic

    if aesthetic.available():
        flat = [p for cands in cand_lists for p in cands]
        scores = aesthetic.score_frames(flat) if flat else []
        best_idx, best_score, cur = [], [], 0
        for cands in cand_lists:
            seg = scores[cur:cur + len(cands)]
            cur += len(cands)
            bi = int(np.argmax(seg)) if seg else 0
            best_idx.append(bi)
            best_score.append(round(float(seg[bi]), 4) if seg else None)
        return best_idx, best_score
    # 降级:清晰度启发式
    best_idx: list[int] = []
    for cands in cand_lists:
        sharps = [_sharpness(p) for p in cands]
        best_idx.append(int(np.argmax(sharps)) if sharps else 0)
    return best_idx, [None] * len(cand_lists)


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
        "SELECT id, idx, keyframe FROM shots WHERE asset_id=? AND keyframe IS NOT NULL ORDER BY idx",
        (asset["id"],),
    ).fetchall()
    if not rows:
        conn.close()
        return 0

    kf_dir = config.KEYFRAMES / asset["id"]
    cand_lists = []
    for r in rows:
        cands = [str(p) for p in shots_mod.candidate_frames(kf_dir, r["idx"])]
        cand_lists.append(cands or [r["keyframe"]])
    best_idx, best_score = _pick_best_frames(cand_lists)

    # 敲定每镜代表帧:更新 keyframe + aesthetic,删除落选候选帧省磁盘。
    best_frames: list[str] = []
    for r, cands, bi, score in zip(rows, cand_lists, best_idx, best_score):
        best = cands[bi]
        best_frames.append(best)
        conn.execute("UPDATE shots SET keyframe=?, aesthetic=? WHERE id=?",
                     (best, score, r["id"]))
        for p in cands:
            if p != best:
                Path(p).unlink(missing_ok=True)
    conn.commit()

    model, preprocess, _, device = _load()
    n = 0
    for i in range(0, len(rows), batch):
        chunk = list(zip(rows[i:i + batch], best_frames[i:i + batch]))
        imgs = [preprocess(Image.open(kf).convert("RGB")) for _, kf in chunk]
        with torch.no_grad():
            feats = model.encode_image(torch.stack(imgs).to(device))
            feats = feats / feats.norm(dim=-1, keepdim=True)
        vecs = feats.cpu().numpy().astype("float32")
        for (r, _), v in zip(chunk, vecs):
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
