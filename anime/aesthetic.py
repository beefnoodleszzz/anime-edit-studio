"""LAION 美学评分:OpenAI CLIP ViT-B/32 向量 → 线性头 → 1..10 美学分。

用来在多帧候选里挑「最好看」的一帧,并给筛选/排序一个真正的画面质量信号,
替代 Laplacian 方差那种把繁杂纹理当清晰的伪度量。

注意:线性头 sa_0_4_vit_b_32_linear.pth 是在 **OpenAI** CLIP ViT-B/32(512维)上训练的,
与 embed.py 的 laion2b 版空间不同,故这里单独加载 openai 权重,检索向量仍用 laion2b。
权重缺失时抛 FileNotFoundError,调用方(shots/embed)回退到清晰度启发式。
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

from . import config

# OpenAI CLIP ViT-B/32 用 QuickGELU 激活;必须用 -quickgelu 变体,否则激活不匹配、
# embedding 偏离真 CLIP,美学头(在真 OpenAI 向量上训练)打分失准。
_MODEL = "ViT-B-32-quickgelu"
_PRETRAINED = "openai"
_cache: dict = {}


def _head_path() -> Path:
    raw = config.get("tools", "aesthetic_model",
                     str(config.ROOT / "kit" / "models" / "sa_0_4_vit_b_32_linear.pth"))
    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = config.ROOT / p
    if not p.exists():
        raise FileNotFoundError(f"美学评分头权重不存在: {p}")
    return p


def available() -> bool:
    try:
        _head_path()
        return True
    except FileNotFoundError:
        return False


def _load():
    if _cache:
        return (_cache["model"], _cache["preprocess"], _cache["head"],
                _cache["bias"], _cache["device"])
    import open_clip
    import torch

    device = "mps" if torch.backends.mps.is_available() else "cpu"
    model, _, preprocess = open_clip.create_model_and_transforms(_MODEL, pretrained=_PRETRAINED)
    model = model.to(device).eval()
    sd = torch.load(_head_path(), map_location=device)
    head = sd["weight"].to(device).float()   # [1, 512]
    bias = sd["bias"].to(device).float()      # [1]
    _cache.update(model=model, preprocess=preprocess, head=head, bias=bias, device=device)
    return model, preprocess, head, bias, device


def score_frames(paths: list[str], batch: int = 16) -> list[float]:
    """给一批关键帧打美学分(约 1..10)。权重缺失时抛 FileNotFoundError。"""
    import torch
    from PIL import Image

    model, preprocess, head, bias, device = _load()
    out: list[float] = []
    for i in range(0, len(paths), batch):
        chunk = paths[i:i + batch]
        imgs = [preprocess(Image.open(p).convert("RGB")) for p in chunk]
        with torch.no_grad():
            feats = model.encode_image(torch.stack(imgs).to(device)).float()
            feats = feats / feats.norm(dim=-1, keepdim=True)   # 线性头前先 L2 归一
            scores = feats @ head.t() + bias                    # [n, 1]
        out.extend(scores.squeeze(-1).cpu().numpy().astype("float32").tolist())
    return out
