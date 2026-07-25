"""环境自检 —— 回答「现在能不能干活」。

覆盖 AGENTS.md §6 记录的全部实测坑位（P1–P5）。

注意：本模块**不直接** import Resolve API（AGENTS.md R1），
所有 Resolve 访问经 studio.execution.resolve.connection。
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

from studio.execution.resolve import connection

REPO = Path(__file__).resolve().parent.parent.parent

SUPPORTED_PYTHON = connection.SUPPORTED_PYTHON
RESOLVE_SCRIPT_API = connection.RESOLVE_SCRIPT_API
RESOLVE_SCRIPT_LIB = connection.RESOLVE_SCRIPT_LIB


def _check(name: str, ok: bool, detail: str) -> dict:
    return {"name": name, "ok": bool(ok), "detail": detail}


def python_version_ok() -> tuple[bool, str]:
    v = sys.version_info[:2]
    ok = SUPPORTED_PYTHON[0] <= v <= SUPPORTED_PYTHON[1]
    detail = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    if not ok:
        lo, hi = SUPPORTED_PYTHON
        detail += f" —— fusionscript 需 {lo[0]}.{lo[1]}–{hi[0]}.{hi[1]}，请使用 .venv"
    return ok, detail


def check_environment() -> dict:
    checks: list[dict] = []

    ok, detail = python_version_ok()
    checks.append(_check("python", ok, detail))

    checks.append(
        _check(
            "resolve_app",
            RESOLVE_SCRIPT_LIB.exists(),
            str(RESOLVE_SCRIPT_LIB) if RESOLVE_SCRIPT_LIB.exists() else "未找到 fusionscript.so",
        )
    )
    module = RESOLVE_SCRIPT_API / "Modules" / "DaVinciResolveScript.py"
    checks.append(
        _check("resolve_scripting_module", module.exists(), str(module))
    )

    running = connection.is_running()
    checks.append(
        _check(
            "resolve_running",
            running,
            "运行中" if running else "未运行 —— 请先启动 DaVinci Resolve（P2：未运行时 API 静默返回 None）",
        )
    )

    if running:
        try:
            got = connection.info()
            checks.append(
                _check(
                    "resolve_connect",
                    True,
                    f"版本 {got.version} / page={got.page} / project={got.project}",
                )
            )
        except connection.ResolveUnavailable as exc:
            checks.append(_check("resolve_connect", False, str(exc).splitlines()[0]))
        except Exception as exc:  # noqa: BLE001
            checks.append(_check("resolve_connect", False, f"{type(exc).__name__}: {exc}"))
    else:
        checks.append(_check("resolve_connect", False, "跳过（进程未运行）"))

    for tool in ("ffmpeg", "ffprobe"):
        path = shutil.which(tool)
        checks.append(_check(tool, bool(path), path or "未在 PATH 中找到"))

    caps = REPO / "config" / "resolve_capabilities.yaml"
    checks.append(_check("capabilities_yaml", caps.exists(), str(caps)))

    return {"ready": all(c["ok"] for c in checks), "checks": checks}
