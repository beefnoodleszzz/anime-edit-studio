"""Persisted A/B/C groups, review artifacts, and pairwise preferences."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import cv2
import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from studio.core.hashing import stable_hash
from studio.editing.ranking import RANKING_VERSION, RankedCandidate
from studio.execution.ffmpeg import create_shot_preview


class CandidateGroup(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    project_id: str
    role: str
    shot_ids: list[str] = Field(..., min_length=3, max_length=3)
    selected_shot_id: str | None = None
    selection_source: str | None = Field(default=None, pattern="^(human|ai)$")
    plan_revision: int = Field(default=1, ge=1)
    active: bool = True


def create_group(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    role: str,
    ranked: list[RankedCandidate],
    plan_revision: int = 1,
) -> CandidateGroup:
    if len(ranked) < 3:
        raise ValueError(f"{role} 至少需要 3 个候选")
    shot_ids = [item.shot_id for item in ranked[:3]]
    group_id = stable_hash(
        {
            "project_id": project_id,
            "role": role,
            "shots": shot_ids,
            "ranking_version": RANKING_VERSION,
        }
    )[:20]
    previous = conn.execute(
        """
        SELECT selected_shot_id,selection_source FROM candidate_groups
        WHERE project_id=? AND role=? AND active=1
        ORDER BY plan_revision DESC,created_at DESC LIMIT 1
        """,
        (project_id, role),
    ).fetchone()
    preserved_selection = (
        previous["selected_shot_id"]
        if previous and previous["selected_shot_id"] in shot_ids
        else None
    )
    preserved_source = previous["selection_source"] if preserved_selection else None
    with conn:
        conn.execute(
            "UPDATE candidate_groups SET active=0 WHERE project_id=? AND role=?",
            (project_id, role),
        )
        conn.execute(
            """
            INSERT INTO candidate_groups(
              id,project_id,role,shot_ids_json,selected_shot_id,selection_source,
              plan_revision,active
            ) VALUES (?,?,?,?,?,?,?,1)
            ON CONFLICT(id) DO UPDATE SET
              shot_ids_json=excluded.shot_ids_json,
              selected_shot_id=coalesce(
                candidate_groups.selected_shot_id,excluded.selected_shot_id
              ),
              selection_source=coalesce(
                candidate_groups.selection_source,excluded.selection_source
              ),
              plan_revision=excluded.plan_revision,
              active=1
            """,
            (
                group_id, project_id, role, json.dumps(shot_ids),
                preserved_selection, preserved_source, plan_revision,
            ),
        )
    return CandidateGroup(
        id=group_id,
        project_id=project_id,
        role=role,
        shot_ids=shot_ids,
        selected_shot_id=preserved_selection,
        selection_source=preserved_source,
        plan_revision=plan_revision,
    )


def choose_candidate(
    conn: sqlite3.Connection,
    *,
    group_id: str,
    shot_id: str,
    context: dict,
    project_style: str | None = None,
    selection_source: str = "human",
) -> CandidateGroup:
    if selection_source not in {"human", "ai"}:
        raise ValueError("selection_source 必须是 human 或 ai")
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM candidate_groups WHERE id=? AND active=1", (group_id,)
    ).fetchone()
    if row is None:
        raise ValueError(f"candidate group 不存在: {group_id}")
    shot_ids = json.loads(row["shot_ids_json"])
    if shot_id not in shot_ids:
        raise ValueError("只能选择 A/B/C 组内镜头")
    conflict = conn.execute(
        """
        SELECT role FROM candidate_groups
        WHERE project_id=? AND active=1 AND id<>? AND selected_shot_id=?
        LIMIT 1
        """,
        (row["project_id"], group_id, shot_id),
    ).fetchone()
    if conflict is not None:
        raise ValueError(f"该镜头已被 {conflict['role']} 角色选中，请选择另一候选")
    with conn:
        conn.execute(
            """
            UPDATE candidate_groups
            SET selected_shot_id=?,selection_source=? WHERE id=?
            """,
            (shot_id, selection_source, group_id),
        )
        conn.executemany(
            """
            INSERT INTO preference_pairs(
              winner_shot_id,loser_shot_id,context_json,project_style,project_id
            ) VALUES (?,?,?,?,?)
            """,
            [
                (
                    shot_id, loser, json.dumps(context, ensure_ascii=False, sort_keys=True),
                    project_style, row["project_id"],
                )
                for loser in shot_ids if loser != shot_id
            ],
        )
    return CandidateGroup(
        id=row["id"],
        project_id=row["project_id"],
        role=row["role"],
        shot_ids=shot_ids,
        selected_shot_id=shot_id,
        selection_source=selection_source,
        plan_revision=row["plan_revision"],
        active=bool(row["active"]),
    )


def precision_metrics(conn: sqlite3.Connection, project_id: str) -> dict:
    total, selected, human, delegated = conn.execute(
        """
        SELECT
          count(*),
          count(selected_shot_id),
          sum(CASE WHEN selection_source='human' THEN 1 ELSE 0 END),
          sum(CASE WHEN selection_source='ai' THEN 1 ELSE 0 END)
        FROM candidate_groups
        WHERE project_id=? AND active=1
        """,
        (project_id,),
    ).fetchone()
    return {
        "project_id": project_id,
        "groups": total,
        "accepted": selected,
        "human_accepted": human,
        "ai_delegated": delegated,
        "selection_completion": selected / total if total else None,
        "candidate_precision": human / total if total else None,
    }


def _contact_sheet(items: list[tuple[str, Path]], target: Path) -> Path:
    cells = []
    for label, path in items:
        image = cv2.imread(str(path))
        if image is None:
            raise ValueError(f"无法读取 contact sheet 图片: {path}")
        image = cv2.resize(image, (384, 216), interpolation=cv2.INTER_AREA)
        cv2.rectangle(image, (0, 0), (75, 34), (0, 0, 0), -1)
        cv2.putText(
            image, label, (12, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.7,
            (255, 255, 255), 2, cv2.LINE_AA,
        )
        cells.append(image)
    canvas = np.concatenate(cells, axis=1)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.partial.jpg")
    if not cv2.imwrite(str(temporary), canvas, [cv2.IMWRITE_JPEG_QUALITY, 92]):
        raise RuntimeError("contact sheet 写入失败")
    temporary.replace(target)
    return target


def generate_review_assets(
    conn: sqlite3.Connection,
    group: CandidateGroup,
    *,
    output_dir: Path,
) -> dict:
    conn.row_factory = sqlite3.Row
    placeholders = ",".join("?" for _ in group.shot_ids)
    rows = conn.execute(
        f"""
        SELECT s.id,s.start_sec,s.end_sec,s.keyframe,a.path,a.proxy_path
        FROM shots s JOIN assets a ON a.id=s.asset_id
        WHERE s.id IN ({placeholders})
        """,
        group.shot_ids,
    ).fetchall()
    by_id = {row["id"]: row for row in rows}
    if len(by_id) != 3:
        raise ValueError("候选镜头或素材缺失")
    root = output_dir / group.id
    previews, sheet_items = {}, []
    for label, shot_id in zip(("A", "B", "C"), group.shot_ids, strict=True):
        row = by_id[shot_id]
        media = next(
            (
                Path(value) for value in (row["proxy_path"], row["path"])
                if value and Path(value).is_file()
            ),
            None,
        )
        if media is None:
            raise FileNotFoundError(f"候选素材缺失: {shot_id}")
        preview = create_shot_preview(
            media,
            root / f"{label}_{shot_id}.mp4",
            start_sec=row["start_sec"],
            end_sec=row["end_sec"],
        )
        previews[label] = str(preview)
        sheet_items.append((label, Path(row["keyframe"])))
    sheet = _contact_sheet(sheet_items, root / "contact_sheet.jpg")
    manifest = {
        "group": group.model_dump(mode="json"),
        "previews": previews,
        "contact_sheet": str(sheet),
    }
    root.mkdir(parents=True, exist_ok=True)
    (root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifest


__all__ = [
    "CandidateGroup",
    "choose_candidate",
    "create_group",
    "generate_review_assets",
    "precision_metrics",
]
