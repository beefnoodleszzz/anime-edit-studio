"""asset_id → 媒体路径 的解析。

Phase 1 用文件系统的最小实现（代理目录 + 素材库）。
Phase 2 起替换为 Asset DB 查询；调用方注入 resolver，因此替换时
compiler / validator 均无需改动。
"""
from __future__ import annotations

from pathlib import Path
from typing import Protocol

REPO = Path(__file__).resolve().parent.parent.parent
PROXY_DIR = REPO / "library" / "proxies"

VIDEO_SUFFIXES = (".mov", ".mp4", ".mkv", ".m4v")


class AssetResolver(Protocol):
    def __call__(self, asset_id: str) -> Path | None: ...


class FilesystemResolver:
    """按 asset_id 在若干目录下查找媒体文件。

    查找顺序体现「预览优先用代理，成片用母版」的意图：
    prefer_proxy=True 时先找代理，否则先找母版。
    """

    def __init__(self, search_dirs: list[Path] | None = None, *, prefer_proxy: bool = True):
        self.search_dirs = search_dirs or [PROXY_DIR]
        self.prefer_proxy = prefer_proxy
        self._cache: dict[str, Path | None] = {}

    def __call__(self, asset_id: str) -> Path | None:
        if asset_id in self._cache:
            return self._cache[asset_id]
        found = self._lookup(asset_id)
        self._cache[asset_id] = found
        return found

    def _lookup(self, asset_id: str) -> Path | None:
        for directory in self.search_dirs:
            if not directory.exists():
                continue
            # 精确匹配
            for suffix in VIDEO_SUFFIXES:
                candidate = directory / f"{asset_id}{suffix}"
                if candidate.exists():
                    return candidate
            # 前缀匹配（v1 的 asset_id 是 sha256 前 12 位，允许简写）
            matches = sorted(
                p for p in directory.iterdir()
                if p.suffix.lower() in VIDEO_SUFFIXES and p.stem.startswith(asset_id)
            )
            if matches:
                return matches[0]
        return None

    def available_ids(self) -> list[str]:
        ids: set[str] = set()
        for directory in self.search_dirs:
            if directory.exists():
                ids.update(
                    p.stem for p in directory.iterdir()
                    if p.suffix.lower() in VIDEO_SUFFIXES
                )
        return sorted(ids)


__all__ = ["AssetResolver", "FilesystemResolver", "PROXY_DIR"]
