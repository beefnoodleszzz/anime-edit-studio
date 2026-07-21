"""读取 config.toml,展开工具路径。"""
from __future__ import annotations

import shutil
import tomllib
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.toml"

LIBRARY = ROOT / "library"
PROXIES = LIBRARY / "proxies"
KEYFRAMES = LIBRARY / "keyframes"
CACHE = LIBRARY / "cache"
ASSETS = LIBRARY / "assets"
DB_PATH = LIBRARY / "engine.sqlite"
PROJECTS = ROOT / "projects"
RENDERER = ROOT / "renderer"


@lru_cache
def load() -> dict:
    with CONFIG_PATH.open("rb") as f:
        return tomllib.load(f)


def _expand(value: str) -> str:
    return str(Path(value).expanduser()) if value.startswith("~") else value


def tool(name: str) -> str:
    """返回工具的可执行路径;不存在则抛错。"""
    raw = load()["tools"][name]
    path = _expand(raw)
    if Path(path).is_absolute():
        if not Path(path).exists():
            raise FileNotFoundError(f"工具 {name} 不存在: {path}")
        return path
    resolved = shutil.which(path)
    if not resolved:
        raise FileNotFoundError(f"工具 {name} 未在 PATH 中找到: {path}")
    return resolved


def tool_optional(name: str) -> str | None:
    try:
        return tool(name)
    except (FileNotFoundError, KeyError):
        return None


def get(section: str, key: str, default=None):
    return load().get(section, {}).get(key, default)


def ensure_dirs() -> None:
    for d in (LIBRARY, PROXIES, KEYFRAMES, CACHE, ASSETS, PROJECTS):
        d.mkdir(parents=True, exist_ok=True)
