"""Resolve 能力矩阵读取 —— AGENTS.md R3 的执行点。

规则：
    probed  = 方法存在
    verified = 实测调用成功且效果达标

只有 verified 的能力才允许被 EditSpec 生成器使用。
"""
from __future__ import annotations

import tomllib  # noqa: F401  (占位，避免误用 toml 读 yaml)
from functools import lru_cache
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
CAPABILITIES_PATH = REPO / "config" / "resolve_capabilities.yaml"


class CapabilityError(RuntimeError):
    """请求了一个未验证的能力。"""


@lru_cache
def load_capabilities(path: Path | None = None) -> dict:
    import yaml

    target = path or CAPABILITIES_PATH
    if not target.exists():
        raise FileNotFoundError(f"能力矩阵缺失: {target}")
    return yaml.safe_load(target.read_text())


def get(name: str, caps: dict | None = None) -> dict:
    caps = caps or load_capabilities()
    entry = (caps.get("capabilities") or {}).get(name)
    if entry is None:
        raise CapabilityError(f"能力 {name!r} 未在 resolve_capabilities.yaml 中登记")
    return entry


def is_verified(name: str, caps: dict | None = None) -> bool:
    try:
        return bool(get(name, caps).get("verified"))
    except CapabilityError:
        return False


def is_unavailable(name: str, caps: dict | None = None) -> bool:
    """已实测判定为不可用（区别于「尚未测」）。"""
    try:
        return get(name, caps).get("available") is False
    except CapabilityError:
        return False


def require(name: str, caps: dict | None = None) -> dict:
    """能力未验证则拒绝执行，并给出 fallback 提示。

    这是 AGENTS.md R3「未 verified 的能力禁止生成对应指令」的强制点。
    错误消息区分「已实测不可用」与「尚未验证」—— 二者的后续动作完全不同：
    前者要改用 fallback 方案，后者只需补一次验证。
    """
    entry = get(name, caps)
    if entry.get("verified"):
        return entry

    fallback = entry.get("fallback")
    hint = f"；应改用 fallback: {fallback}" if fallback else "；且无 fallback"

    if entry.get("available") is False:
        raise CapabilityError(
            f"能力 {name!r} 已实测判定为**不可用**"
            f"（证据: {entry.get('evidence', '见 capabilities.yaml')}）{hint}"
        )
    raise CapabilityError(
        f"能力 {name!r} 尚未验证（probed={entry.get('probed')}）"
        f"，禁止生成对应指令{hint}"
    )


def summarize(caps: dict | None = None) -> dict:
    """把能力分成四类。

    「已实测不可用」必须与「尚未验证」分开 —— 前者需要换方案，后者只需补测试。
    """
    caps = caps or load_capabilities()
    verified, unavailable, probed_unverified, unprobed = [], [], [], []
    for name, entry in (caps.get("capabilities") or {}).items():
        if entry.get("verified"):
            verified.append(name)
        elif entry.get("available") is False:
            unavailable.append(name)
        elif entry.get("probed"):
            probed_unverified.append(name)
        else:
            unprobed.append(name)
    return {
        "resolve_version_tested": caps.get("resolve_version_tested"),
        "edition": caps.get("edition"),
        "verified": sorted(verified),
        "unavailable": sorted(unavailable),
        "probed_unverified": sorted(probed_unverified),
        "unprobed": sorted(unprobed),
        "fallbacks": {
            name: entry.get("fallback")
            for name, entry in (caps.get("capabilities") or {}).items()
            if entry.get("available") is False
        },
        "pitfalls": [p["id"] for p in caps.get("pitfalls", [])],
    }
