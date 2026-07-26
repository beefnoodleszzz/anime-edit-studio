"""Resolve 连接层 —— 全仓唯一允许 import DaVinciResolveScript 的地方。

AGENTS.md R1：`studio/execution/resolve/` 之外禁止直接访问 Resolve API。
由 tests/test_architecture_rules.py::test_r1_resolve_api_only_via_adapter 强制。

实测坑位（AGENTS.md §6）：
    P1  进程名是 Resolve，不是 DaVinci Resolve
    P2  未运行时 scriptapp() 静默返回 None，不抛异常
    P3  fusionscript 与 Python 版本绑定，系统 3.14 不兼容
    P5  必须前台运行，无真 headless
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

RESOLVE_SCRIPT_API = Path(
    "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting"
)
RESOLVE_SCRIPT_LIB = Path(
    "/Applications/DaVinci Resolve/DaVinci Resolve.app"
    "/Contents/Libraries/Fusion/fusionscript.so"
)
RESOLVE_PROCESS_NAME = "Resolve"          # P1
RESOLVE_APP_NAME = "DaVinci Resolve"      # open -a 用的是显示名
SUPPORTED_PYTHON = ((3, 9), (3, 13))      # P3


class ResolveUnavailable(RuntimeError):
    """无法连接 Resolve。消息必须可操作，不能只说「失败」。"""


@dataclass(frozen=True)
class ResolveInfo:
    version: str
    page: str
    project: str | None


def check_python_version() -> None:
    """P3：版本不对时早失败，别等到 import 出神秘错误。"""
    v = sys.version_info[:2]
    if not (SUPPORTED_PYTHON[0] <= v <= SUPPORTED_PYTHON[1]):
        lo, hi = SUPPORTED_PYTHON
        raise ResolveUnavailable(
            f"Python {v[0]}.{v[1]} 与 Resolve 的 fusionscript 不兼容"
            f"（需 {lo[0]}.{lo[1]}–{hi[0]}.{hi[1]}）。请使用项目 .venv 运行。"
        )


def is_running() -> bool:
    """P1：用真实进程名检测。"""
    try:
        return subprocess.run(
            ["pgrep", "-x", RESOLVE_PROCESS_NAME], capture_output=True
        ).returncode == 0
    except FileNotFoundError:  # pragma: no cover
        return False


def _inject_env() -> None:
    os.environ.setdefault("RESOLVE_SCRIPT_API", str(RESOLVE_SCRIPT_API))
    os.environ.setdefault("RESOLVE_SCRIPT_LIB", str(RESOLVE_SCRIPT_LIB))
    modules = str(RESOLVE_SCRIPT_API / "Modules")
    if modules not in sys.path:
        sys.path.append(modules)


def launch(timeout_sec: float = 120.0, poll_sec: float = 3.0) -> bool:
    """P5：启动 Resolve 并等待其可连接。已在运行则直接返回。"""
    if is_running():
        return True
    subprocess.Popen(
        ["open", "-a", RESOLVE_APP_NAME],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        time.sleep(poll_sec)
        if is_running() and _try_scriptapp() is not None:
            return True
    return False


def _try_scriptapp():
    """返回 Resolve 对象或 None。不抛异常，供轮询使用。"""
    try:
        check_python_version()
        _inject_env()
        import DaVinciResolveScript as dvr  # noqa: N813

        return dvr.scriptapp("Resolve")
    except Exception:  # noqa: BLE001
        return None


def connect(*, auto_launch: bool = False):
    """获取 Resolve 对象。失败时抛出**可操作**的错误（P2）。"""
    check_python_version()

    if not RESOLVE_SCRIPT_LIB.exists():
        raise ResolveUnavailable(f"未找到 fusionscript.so: {RESOLVE_SCRIPT_LIB}")

    if not is_running():
        if not auto_launch:
            raise ResolveUnavailable(
                "DaVinci Resolve 未运行。请先启动它，"
                "或使用 auto_launch=True / `aes doctor env --launch`。"
            )
        if not launch():
            raise ResolveUnavailable("已尝试启动 Resolve，但在超时内未能连接。")

    _inject_env()
    try:
        import DaVinciResolveScript as dvr  # noqa: N813
    except ImportError as exc:
        raise ResolveUnavailable(
            f"无法导入 DaVinciResolveScript（检查 {RESOLVE_SCRIPT_API}/Modules）：{exc}"
        ) from exc

    resolve = dvr.scriptapp("Resolve")
    if resolve is None:
        # P2：这是最常见的坑，必须明确解释
        raise ResolveUnavailable(
            "scriptapp('Resolve') 返回 None。常见原因：\n"
            "  1. Resolve 正在启动中，尚未就绪\n"
            "  2. Preferences → System → General → External scripting using 设为 None\n"
            "  3. Python 版本与 fusionscript 不匹配"
        )
    return resolve


def info(*, auto_launch: bool = False) -> ResolveInfo:
    """轻量健康检查，不改变 Resolve 状态。"""
    resolve = connect(auto_launch=auto_launch)
    project = resolve.GetProjectManager().GetCurrentProject()
    return ResolveInfo(
        version=resolve.GetVersionString(),
        page=resolve.GetCurrentPage(),
        project=project.GetName() if project else None,
    )
