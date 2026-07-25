"""DaVinci Resolve 执行层。

AGENTS.md R1：本包是全仓**唯一**允许 import DaVinciResolveScript 的地方。
上层一律通过 ResolveAdapter 访问。
"""
from studio.execution.resolve.adapter import (  # noqa: F401
    MediaInfo,
    ResolveAdapter,
    ResolveOperationError,
)
from studio.execution.resolve.connection import ResolveUnavailable  # noqa: F401

__all__ = [
    "MediaInfo",
    "ResolveAdapter",
    "ResolveOperationError",
    "ResolveUnavailable",
]
