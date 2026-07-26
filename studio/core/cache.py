"""Atomic, versioned caches for JSON and numerical model outputs."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import numpy as np


class JsonCache:
    def __init__(self, root: Path):
        self.root = root

    def path_for(self, namespace: str, key: str) -> Path:
        if not namespace or "/" in namespace or ".." in namespace:
            raise ValueError(f"非法 cache namespace: {namespace!r}")
        if len(key) != 64 or any(char not in "0123456789abcdef" for char in key):
            raise ValueError("cache key 必须是 sha256 hex")
        return self.root / namespace / f"{key}.json"

    def get(self, namespace: str, key: str) -> dict | list | None:
        path = self.path_for(namespace, key)
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def put(self, namespace: str, key: str, value: dict | list) -> Path:
        path = self.path_for(namespace, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(value, stream, ensure_ascii=False, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        except Exception:
            Path(temporary).unlink(missing_ok=True)
            raise
        return path


class ArrayCache:
    """Atomic ``float32`` array cache using the same validated identity rules."""

    def __init__(self, root: Path):
        self.root = root

    def path_for(self, namespace: str, key: str) -> Path:
        if not namespace or "/" in namespace or ".." in namespace:
            raise ValueError(f"非法 cache namespace: {namespace!r}")
        if len(key) != 64 or any(char not in "0123456789abcdef" for char in key):
            raise ValueError("cache key 必须是 sha256 hex")
        return self.root / namespace / f"{key}.npy"

    def get(self, namespace: str, key: str) -> np.ndarray | None:
        path = self.path_for(namespace, key)
        if not path.exists():
            return None
        return np.load(path, allow_pickle=False).astype(np.float32, copy=False)

    def put(self, namespace: str, key: str, value: np.ndarray) -> Path:
        path = self.path_for(namespace, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".npy", dir=path.parent
        )
        os.close(fd)
        try:
            np.save(temporary, np.asarray(value, dtype=np.float32), allow_pickle=False)
            with open(temporary, "rb") as stream:
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        except Exception:
            Path(temporary).unlink(missing_ok=True)
            raise
        return path


__all__ = ["ArrayCache", "JsonCache"]
