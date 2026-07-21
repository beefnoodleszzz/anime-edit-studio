"""内容寻址缓存:sha256 与缓存 key。"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from . import config


def sha256_file(path: str | Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while block := f.read(chunk):
            h.update(block)
    return h.hexdigest()


def key(*parts) -> str:
    """把多段参数(源哈希/时间范围/模型版本/参数…)合成稳定缓存 key。"""
    blob = json.dumps(parts, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def cache_path(namespace: str, cache_key: str, suffix: str) -> Path:
    d = config.CACHE / namespace
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{cache_key}{suffix}"
