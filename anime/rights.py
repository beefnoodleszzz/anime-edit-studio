"""素材版权与来源记录(charter 商业边界):每个素材登记来源/授权/商用状态。

存 library/rights.json。二次剪辑不自动等于授权;公开发布/商业合作需分别评估。
"""
from __future__ import annotations

import json
from pathlib import Path

from . import config

_PATH = config.LIBRARY / "rights.json"


def _load() -> dict:
    return json.loads(_PATH.read_text()) if _PATH.exists() else {}


def _save(d: dict) -> None:
    config.LIBRARY.mkdir(parents=True, exist_ok=True)
    _PATH.write_text(json.dumps(d, ensure_ascii=False, indent=2))


def set_rights(asset_id: str, *, source: str | None = None, license: str | None = None,
               notes: str | None = None, commercial: bool | None = None) -> dict:
    d = _load()
    r = d.get(asset_id, {})
    if source is not None:
        r["source"] = source
    if license is not None:
        r["license"] = license
    if notes is not None:
        r["notes"] = notes
    if commercial is not None:
        r["commercial_cleared"] = commercial
    d[asset_id] = r
    _save(d)
    return r


def get_rights(asset_id: str) -> dict:
    return _load().get(asset_id, {})


def all_rights() -> dict:
    return _load()
