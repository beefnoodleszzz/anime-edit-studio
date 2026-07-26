"""Forward-only migrations for v2 EditSpec documents.

This module intentionally has no v1 importer.  v1 belongs to the archived
Remotion system and must never leak compatibility concepts into the v2 IR.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Callable

from studio.editspec.schema import SPEC_VERSION, EditSpec


class MigrationError(ValueError):
    pass


Migration = Callable[[dict], dict]


def _dev_to_200(payload: dict) -> dict:
    result = deepcopy(payload)
    result.setdefault("revision", 1)
    result.setdefault("captions", [])
    result["spec_version"] = "2.0.0"
    return result


def _200_to_210(payload: dict) -> dict:
    result = deepcopy(payload)
    result.setdefault("motion_phrases", [])
    result["spec_version"] = "2.1.0"
    return result


_MIGRATIONS: dict[str, tuple[str, Migration]] = {
    "2.0.0-dev": ("2.0.0", _dev_to_200),
    "2.0.0": ("2.1.0", _200_to_210),
}


def migrate_payload(payload: dict, *, target: str = SPEC_VERSION) -> dict:
    result = deepcopy(payload)
    version = str(result.get("spec_version", ""))
    if not version.startswith("2."):
        raise MigrationError(
            f"只支持 v2.x 内部迁移；收到 {version or 'missing'}。"
            "v1 EditSpec 不得迁入 v2。"
        )
    seen: set[str] = set()
    while version != target:
        if version in seen:
            raise MigrationError(f"迁移图出现循环: {version}")
        seen.add(version)
        step = _MIGRATIONS.get(version)
        if step is None:
            raise MigrationError(f"没有从 {version} 到 {target} 的迁移路径")
        next_version, fn = step
        result = fn(result)
        if result.get("spec_version") != next_version:
            raise MigrationError(f"迁移 {version} 未产出声明版本 {next_version}")
        version = next_version
    return result


def load_migrated(payload: dict) -> EditSpec:
    return EditSpec.model_validate(migrate_payload(payload))


__all__ = ["MigrationError", "load_migrated", "migrate_payload"]
