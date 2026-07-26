"""DaVinci Resolve 执行层。

AGENTS.md R1：本包是全仓**唯一**允许 import DaVinciResolveScript 的地方。
上层一律通过 ResolveAdapter 访问。
"""
from studio.execution.resolve.adapter import (  # noqa: F401
    MediaInfo,
    RenderResult,
    ResolveAdapter,
    ResolveOperationError,
)
from studio.execution.resolve.connection import ResolveUnavailable  # noqa: F401
from studio.execution.resolve.color import apply_color_recipe
from studio.execution.resolve.fairlight import append_prebaked_audio
from studio.execution.resolve.fusion import (
    apply_fusion_recipe,
    apply_speed_ramp_recipe,
    apply_whip_blur_side,
)

__all__ = [
    "MediaInfo",
    "RenderResult",
    "ResolveAdapter",
    "ResolveOperationError",
    "ResolveUnavailable",
    "append_prebaked_audio",
    "apply_color_recipe",
    "apply_fusion_recipe",
    "apply_speed_ramp_recipe",
    "apply_whip_blur_side",
]
