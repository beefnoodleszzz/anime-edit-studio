"""Sound fallback; public Fairlight automation remains unavailable."""
from __future__ import annotations

from .adapter import ResolveAdapter


def append_prebaked_audio(adapter: ResolveAdapter, requests: list[dict]) -> list:
    return adapter.append_audio(requests)


__all__ = ["append_prebaked_audio"]
