"""Local source rights registry backed by SQLite and library/sources.toml."""
from __future__ import annotations

from pathlib import Path

from . import config, decision_loop

_PATH = config.LIBRARY / "sources.toml"


def set_rights(asset_id: str, *, source: str | None = None, license: str | None = None,
               notes: str | None = None, commercial: bool | None = None) -> dict:
    record = decision_loop.upsert_source_record(
        asset_id,
        {
            "source_url": source,
            "license": license,
            "notes": notes,
            "commercial_allowed": commercial,
            "status": "approved" if commercial else "review",
        },
    )
    _export_sources_toml()
    return record


def get_rights(asset_id: str) -> dict:
    for row in decision_loop.list_sources():
        if row["asset_id"] == asset_id:
            return row
    return {}


def all_rights() -> dict:
    return {row["asset_id"]: row for row in decision_loop.list_sources()}


def _export_sources_toml() -> Path:
    _PATH.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for asset_id, row in all_rights().items():
        lines.append(f'[{asset_id}]')
        for key in ("source_type", "source_url", "creator", "title", "license", "license_url",
                    "commercial_allowed", "modification_allowed", "attribution_required",
                    "attribution_text", "permission_proof_path", "acquired_at", "license_checked_at",
                    "expires_at", "status", "notes"):
            value = row.get(key)
            if value is None:
                continue
            if isinstance(value, bool):
                lines.append(f"{key} = {'true' if value else 'false'}")
            elif isinstance(value, (int, float)):
                lines.append(f"{key} = {value}")
            else:
                escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
                lines.append(f'{key} = "{escaped}"')
        lines.append("")
    _PATH.write_text("\n".join(lines).strip() + "\n" if lines else "")
    return _PATH
