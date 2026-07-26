"""Content-addressed hashes used by every deterministic/model analysis cache."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


def file_sha256(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def stable_hash(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def analysis_cache_key(
    *,
    asset_hash: str,
    model: str,
    model_version: str,
    pipeline_version: str,
    parameters: dict,
) -> str:
    """R6/§51 mandatory cache identity; no component may be omitted."""
    if not all((asset_hash, model, model_version, pipeline_version)):
        raise ValueError("cache key 必须包含 asset/model/model_version/pipeline_version")
    return stable_hash(
        {
            "asset_hash": asset_hash,
            "model": model,
            "model_version": model_version,
            "pipeline_version": pipeline_version,
            "parameters": parameters,
        }
    )


__all__ = ["analysis_cache_key", "file_sha256", "stable_hash"]
