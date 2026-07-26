"""Persist explicit human Recipe commissioning decisions."""
from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict

from .registry import DEFAULT_REGISTRY, REPO

Decision = Literal["accepted", "rejected"]
_STATUS = re.compile(r"(?:Human status|Status):\s*\*\*(\w+)\*\*", re.I)


class RecipeReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    kind: str
    version: str
    status: Literal["pending", "accepted", "rejected"]
    verified: bool
    artifact: str
    preview: str
    acceptance: str


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except Exception:
        Path(temporary).unlink(missing_ok=True)
        raise


def _payload(config: Path) -> dict:
    value = yaml.safe_load(config.read_text(encoding="utf-8")) or {}
    if not isinstance(value.get("recipes"), list):
        raise ValueError("Recipe registry 缺少 recipes 列表")
    return value


def _status(path: Path) -> Literal["pending", "accepted", "rejected"]:
    if not path.is_file():
        return "pending"
    match = _STATUS.search(path.read_text(encoding="utf-8"))
    value = match.group(1).lower() if match else "pending"
    return value if value in {"accepted", "rejected"} else "pending"


def list_recipe_reviews(
    *, config: Path = DEFAULT_REGISTRY, root: Path = REPO
) -> list[RecipeReview]:
    reviews = []
    for row in _payload(config)["recipes"]:
        required = ("artifact", "preview", "acceptance")
        if any(not row.get(name) for name in required):
            raise ValueError(f"{row.get('id', '<unknown>')} 缺少验收路径")
        status = _status(root / row["acceptance"])
        verified = bool(row.get("verified"))
        if verified != (status == "accepted"):
            raise ValueError(
                f"{row['id']} 状态矛盾: verified={verified}, human={status}"
            )
        reviews.append(
            RecipeReview(
                id=row["id"],
                kind=row["kind"],
                version=str(row["version"]),
                status=status,
                verified=verified,
                artifact=row["artifact"],
                preview=row["preview"],
                acceptance=row["acceptance"],
            )
        )
    return reviews


def record_recipe_decision(
    recipe_id: str,
    *,
    reviewer: str,
    decision: Decision,
    notes: str,
    reviewed_at: str,
    config: Path = DEFAULT_REGISTRY,
    root: Path = REPO,
) -> RecipeReview:
    """Record a human decision; this function never invents the decision."""
    reviewer, notes, reviewed_at = (
        reviewer.strip(),
        notes.strip(),
        reviewed_at.strip(),
    )
    if not reviewer or not reviewed_at:
        raise ValueError("reviewer 与 reviewed_at 必填")
    payload = _payload(config)
    rows = [row for row in payload["recipes"] if row.get("id") == recipe_id]
    if len(rows) != 1:
        raise KeyError(recipe_id)
    row = rows[0]
    for name in ("artifact", "preview", "acceptance"):
        value = row.get(name)
        if not value or not (root / value).is_file():
            raise ValueError(f"{recipe_id} 缺少 {name}，禁止记录通过")

    acceptance = root / row["acceptance"]
    old_acceptance = acceptance.read_text(encoding="utf-8")
    old_config = config.read_text(encoding="utf-8")
    record = (
        f"# {recipe_id} acceptance\n\n"
        f"Human status: **{decision.upper()}**  \n"
        f"Reviewer: {reviewer}  \n"
        f"Reviewed at: {reviewed_at}  \n"
        f"Decision / notes: {notes or '(none)'}\n"
    )
    row["verified"] = decision == "accepted"
    try:
        _atomic_write(acceptance, record)
        _atomic_write(
            config,
            yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        )
    except Exception:
        _atomic_write(acceptance, old_acceptance)
        _atomic_write(config, old_config)
        raise
    return next(
        item
        for item in list_recipe_reviews(config=config, root=root)
        if item.id == recipe_id
    )


__all__ = ["RecipeReview", "list_recipe_reviews", "record_recipe_decision"]
