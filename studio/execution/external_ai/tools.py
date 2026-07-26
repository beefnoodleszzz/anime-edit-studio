"""Optional high-cost tools; never part of pre-lock preview generation."""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


class ExternalToolError(RuntimeError):
    pass


@dataclass(frozen=True)
class ExternalAITool:
    id: Literal["rife", "real_esrgan", "rembg", "whisper"]
    executable: str | None
    available: bool
    lock_picture_required: bool = True


_EXECUTABLES = {
    "rife": ("rife-ncnn-vulkan",),
    "real_esrgan": ("realesrgan-ncnn-vulkan",),
    "rembg": ("rembg",),
    "whisper": ("whisper-cli", "whisper"),
}


def discover_external_tools() -> dict[str, ExternalAITool]:
    tools = {}
    for tool_id, candidates in _EXECUTABLES.items():
        executable = next(
            (value for candidate in candidates if (value := shutil.which(candidate))),
            None,
        )
        tools[tool_id] = ExternalAITool(
            id=tool_id, executable=executable, available=executable is not None
        )
    return tools


def run_external_tool(
    tool_id: str,
    arguments: list[str],
    *,
    picture_locked: bool,
    timeout_sec: float = 3600,
) -> subprocess.CompletedProcess[str]:
    """Execute one explicit tool call after the caller proves picture lock."""
    tools = discover_external_tools()
    if tool_id not in tools:
        raise ExternalToolError(f"未登记 external AI tool: {tool_id}")
    tool = tools[tool_id]
    if not picture_locked:
        raise ExternalToolError(
            f"{tool_id} 仅允许在 Lock Picture 后执行，避免无效高成本处理"
        )
    if not tool.available or not tool.executable:
        raise ExternalToolError(f"{tool_id} 当前未安装")
    result = subprocess.run(
        [tool.executable, *arguments],
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout_sec,
    )
    if result.returncode:
        raise ExternalToolError(
            f"{tool_id} 失败 ({result.returncode}): {result.stderr[-2000:]}"
        )
    return result
