"""Recoverable natural-language feedback → validated EditSpec revision."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from studio.agents import StructuredProvider
from studio.core.assets import DatabaseResolver, DatabaseShotResolver
from studio.creative.preference import record_diff_feedback
from studio.critic.creative import (
    parse_feedback,
    proposal_to_diff,
    select_replacement_from_db,
)
from studio.editspec.diff import apply_diff
from studio.editspec.schema import EditSpec
from studio.editspec.validator import validate


@dataclass(frozen=True)
class RevisionResult:
    project_id: str
    from_version: int
    to_version: int
    operations: int
    changed_clip_ids: tuple[str, ...]
    spec_path: Path
    run_id: int


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def recover_revision_files(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    spec_path: Path,
) -> EditSpec:
    """Materialize the latest committed DB revision after an interrupted write."""
    row = conn.execute(
        """
        SELECT spec_json FROM edit_specs
        WHERE project_id=? ORDER BY version DESC LIMIT 1
        """,
        (project_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"project {project_id!r} 没有可恢复的 EditSpec")
    spec = EditSpec.model_validate_json(row[0])
    content = spec.model_dump_json(by_alias=True, indent=2)
    _atomic_write(spec_path.with_name(f"editspec.r{spec.revision}.json"), content)
    _atomic_write(spec_path, content)
    return spec


def revise_from_feedback(
    conn: sqlite3.Connection,
    *,
    provider: StructuredProvider,
    feedback: str,
    spec_path: Path,
    database_path: Path,
) -> RevisionResult:
    """Run one auditable revision attempt and persist only an executable diff."""
    spec = EditSpec.model_validate_json(spec_path.read_text(encoding="utf-8"))
    previous_attempt = conn.execute(
        """
        SELECT coalesce(max(attempt),0) FROM revision_runs
        WHERE project_id=? AND from_version=? AND feedback=?
        """,
        (spec.id, spec.revision, feedback),
    ).fetchone()[0]
    cursor = conn.execute(
        """
        INSERT INTO revision_runs(
          project_id,from_version,source,feedback,status,attempt
        ) VALUES (?,?,'user',?,'running',?)
        """,
        (spec.id, spec.revision, feedback, int(previous_attempt) + 1),
    )
    run_id = int(cursor.lastrowid)
    conn.commit()
    try:
        review, call = parse_feedback(provider, feedback=feedback, spec=spec)
        with conn:
            conn.execute(
                """
                INSERT INTO llm_calls(
                  project_id,stage,provider,model,request_id,duration_ms,cost_usd,
                  schema_name,status
                ) VALUES (?,?,?,?,?,?,?,?, 'complete')
                """,
                (
                    spec.id, "feedback_parse", call.provider, call.model,
                    call.request_id, call.duration_ms, call.cost_usd,
                    "CreativeReview",
                ),
            )
        patch = proposal_to_diff(
            review,
            spec,
            source="user",
            select_replacement=lambda clip, requirements: select_replacement_from_db(
                conn, clip, requirements
            ),
        )
        if not patch.ops:
            raise ValueError("反馈没有产生可执行 Revision 操作")
        revised = apply_diff(spec, patch)
        validation = validate(
            revised,
            resolve_asset=DatabaseResolver(database_path, prefer_proxy=False),
            resolve_shot=DatabaseShotResolver(database_path),
        )
        validation.raise_if_failed()
        changed = tuple(
            sorted(
                {
                    getattr(op, "clip_id")
                    for op in patch.ops
                    if getattr(op, "clip_id", None)
                }
            )
        )
        revision_path = spec_path.with_name(f"editspec.r{revised.revision}.json")
        content = revised.model_dump_json(by_alias=True, indent=2)
        # The immutable revision snapshot is written first. If DB commit fails it
        # is harmless; the mutable "latest" pointer is advanced only after commit.
        _atomic_write(revision_path, content)
        with conn:
            conn.execute(
                """
                INSERT INTO edit_spec_diffs(
                  project_id,from_version,to_version,ops_json,source
                ) VALUES (?,?,?,?,?)
                """,
                (
                    spec.id, patch.from_version, patch.to_version,
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
                    spec.id, revised.revision,
                    revised.model_dump_json(by_alias=True),
                    spec.revision, "user",
                ),
            )
            record_diff_feedback(
                conn,
                project_id=spec.id,
                before=spec,
                after=revised,
                diff=patch,
                source="user",
            )
            conn.execute(
                """
                UPDATE revision_runs SET
                  to_version=?,review_json=?,diff_json=?,status='complete',
                  finished_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')
                WHERE id=?
                """,
                (
                    revised.revision,
                    review.model_dump_json(),
                    patch.model_dump_json(),
                    run_id,
                ),
            )
        _atomic_write(spec_path, content)
        return RevisionResult(
            project_id=spec.id,
            from_version=spec.revision,
            to_version=revised.revision,
            operations=len(patch.ops),
            changed_clip_ids=changed,
            spec_path=revision_path,
            run_id=run_id,
        )
    except Exception as exc:
        with conn:
            conn.execute(
                """
                UPDATE revision_runs SET status='failed',error_json=?,
                  finished_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')
                WHERE id=?
                """,
                (json.dumps({"error": str(exc)}, ensure_ascii=False), run_id),
            )
        raise


__all__ = [
    "RevisionResult",
    "recover_revision_files",
    "revise_from_feedback",
]
