"""Auditable Recipe-plan refresh after owner acceptance."""
from __future__ import annotations

import os
import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path

import yaml

from studio.core.assets import DatabaseResolver, DatabaseShotResolver
from studio.creative.director import DirectorPlan
from studio.creative.preference import record_diff_feedback
from studio.editing.sequence import apply_recipe_plan
from studio.editing.music import MusicMotionMap
from studio.editspec.diff import apply_diff, diff_specs
from studio.editspec.schema import EditSpec
from studio.editspec.validator import validate


@dataclass(frozen=True)
class RecipeRefreshResult:
    project_id: str
    from_version: int
    to_version: int
    operations: int
    changed_clip_ids: tuple[str, ...]
    spec_path: Path


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


def refresh_recipe_plan(
    conn: sqlite3.Connection,
    *,
    spec_path: Path,
    plan_path: Path,
    database_path: Path,
) -> RecipeRefreshResult:
    before = EditSpec.model_validate_json(spec_path.read_text(encoding="utf-8"))
    plan = DirectorPlan.model_validate(
        yaml.safe_load(plan_path.read_text(encoding="utf-8"))
    )
    motion_path = spec_path.parent / "music_motion_map.json"
    music_motion = (
        MusicMotionMap.model_validate_json(motion_path.read_text(encoding="utf-8"))
        if motion_path.is_file() else None
    )
    proposed = apply_recipe_plan(
        before,
        plan=plan,
        music_motion=music_motion,
    )
    patch = diff_specs(before, proposed, source="critic")
    if not patch.ops:
        return RecipeRefreshResult(
            before.id, before.revision, before.revision, 0, (), spec_path
        )
    revised = apply_diff(before, patch)
    validation = validate(
        revised,
        resolve_asset=DatabaseResolver(database_path, prefer_proxy=False),
        resolve_shot=DatabaseShotResolver(database_path),
    )
    validation.raise_if_failed()
    content = revised.model_dump_json(by_alias=True, indent=2)
    with conn:
        conn.execute(
            """
            INSERT INTO edit_spec_diffs(
              project_id,from_version,to_version,ops_json,source
            ) VALUES (?,?,?,?,?)
            """,
            (
                before.id, before.revision, revised.revision,
                patch.model_dump_json(), patch.source,
            ),
        )
        conn.execute(
            """
            INSERT INTO edit_specs(
              project_id,version,spec_json,parent_version,created_by
            ) VALUES (?,?,?,?,?)
            """,
            (
                before.id, revised.revision,
                revised.model_dump_json(by_alias=True),
                before.revision, "rule",
            ),
        )
        record_diff_feedback(
            conn,
            project_id=before.id,
            before=before,
            after=revised,
            diff=patch,
            source="critic",
        )
    _atomic_write(spec_path, content)
    changed = tuple(
        op.clip_id for op in patch.ops if getattr(op, "clip_id", None)
    )
    return RecipeRefreshResult(
        before.id, before.revision, revised.revision,
        len(patch.ops), changed, spec_path,
    )


__all__ = ["RecipeRefreshResult", "refresh_recipe_plan"]
