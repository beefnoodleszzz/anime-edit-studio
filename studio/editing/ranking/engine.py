"""Intrinsic + contextual ranking with auditable component scores."""
from __future__ import annotations

import json
import math
import sqlite3

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

RANKING_VERSION = "candidate-ranking-1.2.0"


class CandidateContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str
    role: str
    target_energy: float = Field(0.5, ge=0, le=1)
    target_shot_scale: float | None = Field(None, ge=0, le=1)
    character: str | None = None
    action: str | None = None
    previous_shot_id: str | None = None
    selected_shot_ids: list[str] = Field(default_factory=list)
    music_fit_by_shot: dict[str, float] = Field(default_factory=dict)
    reference_fit_by_shot: dict[str, float] = Field(default_factory=dict)
    preference_by_shot: dict[str, float] = Field(default_factory=dict)


class RankedCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    shot_id: str
    intrinsic: float
    contextual: float
    total: float
    intrinsic_components: dict[str, float]
    contextual_components: dict[str, float]


def _bounded(value: object, default: float = 0.0) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return default


def _embedding(row: sqlite3.Row | None) -> np.ndarray | None:
    if row is None or row["embedding"] is None:
        return None
    vector = np.frombuffer(row["embedding"], dtype=np.float32)
    return vector if vector.size and np.isfinite(vector).all() else None


def _intrinsic(row: sqlite3.Row) -> tuple[float, dict[str, float]]:
    subtitle = json.loads(row["subtitle_region"] or "{}")
    tags = (row["tags"] or "").lower()
    contamination = (
        0.0
        if any(
            tag in tags
            for tag in (
                "subtitled",
                "english_text",
                "text_focus",
                "fake_screenshot",
                "parody",
                "chibi",
            )
        )
        else 1.0
    )
    components = {
        "composition": _bounded(row["composition_confidence"], 0.5)
        * (1.0 if row["composition"] else 0.5),
        "pose": _bounded(row["pose_quality"], 0.5),
        "face": _bounded(row["face_visibility"], 0.5),
        "image_quality": _bounded(row["image_quality"], 0.5),
        "compression": 1.0 - _bounded(row["compression_score"]),
        "cutability": _bounded(row["cutability"], 0.5),
        "subtitle_clean": 0.0 if subtitle.get("present") else 1.0,
        "aesthetic": _bounded((row["aesthetic"] or 5.0) / 10.0, 0.5),
        "production_clean": contamination,
    }
    weights = {
        "composition": 0.11, "pose": 0.11, "face": 0.12,
        "image_quality": 0.18, "compression": 0.08, "cutability": 0.12,
        "subtitle_clean": 0.08, "aesthetic": 0.06, "production_clean": 0.14,
    }
    return sum(components[key] * weights[key] for key in weights), components


def _continuity(current: sqlite3.Row, previous: sqlite3.Row | None) -> float:
    if previous is None:
        return 0.5
    character = 1.0 if (
        current["character"] and current["character"] == previous["character"]
    ) else 0.45
    scale = 1.0 - min(
        1.0,
        abs(_bounded(current["shot_scale"], 0.5) - _bounded(previous["shot_scale"], 0.5)),
    )
    directions = (current["motion_dir"], previous["motion_dir"])
    motion = 0.7 if None in directions else (
        1.0 if directions[0] == directions[1] else 0.45
    )
    return 0.4 * character + 0.35 * scale + 0.25 * motion


def _novelty(current: sqlite3.Row, selected: list[sqlite3.Row]) -> float:
    vector = _embedding(current)
    other = [_embedding(row) for row in selected]
    other = [value for value in other if value is not None]
    if vector is None or not other:
        return 0.5
    similarities = [
        float(np.dot(vector, value) / (np.linalg.norm(vector) * np.linalg.norm(value) + 1e-8))
        for value in other
    ]
    return _bounded(1.0 - max(similarities))


def _pool_novelty(
    current: sqlite3.Row,
    selected_ids: list[str],
    by_id: dict[str, sqlite3.Row],
) -> float:
    return _novelty(current, [by_id[value] for value in selected_ids if value in by_id])


def rank_candidates(
    conn: sqlite3.Connection,
    shot_ids: list[str],
    context: CandidateContext,
    *,
    limit: int = 50,
) -> list[RankedCandidate]:
    if not 1 <= limit <= 200:
        raise ValueError("精排输出必须在 1..200")
    if not shot_ids:
        return []
    conn.row_factory = sqlite3.Row
    placeholders = ",".join("?" for _ in shot_ids)
    rows = conn.execute(
        f"SELECT * FROM shots WHERE id IN ({placeholders})", shot_ids
    ).fetchall()
    by_id = {row["id"]: row for row in rows}
    previous = (
        conn.execute("SELECT * FROM shots WHERE id=?", (context.previous_shot_id,)).fetchone()
        if context.previous_shot_id else None
    )
    selected = (
        conn.execute(
            f"SELECT * FROM shots WHERE id IN ({','.join('?' for _ in context.selected_shot_ids)})",
            context.selected_shot_ids,
        ).fetchall()
        if context.selected_shot_ids else []
    )
    ranked = []
    for shot_id in shot_ids:
        row = by_id.get(shot_id)
        if row is None:
            continue
        intrinsic, intrinsic_components = _intrinsic(row)
        energy = _bounded(row["visual_energy"], 0.5)
        sequence_fit = 1.0 - abs(energy - context.target_energy)
        if context.target_shot_scale is not None:
            sequence_fit = 0.6 * sequence_fit + 0.4 * (
                1.0 - abs(_bounded(row["shot_scale"], 0.5) - context.target_shot_scale)
            )
        semantic = 0.5
        if context.character:
            character = context.character.lower()
            tags = (row["tags"] or "").lower()
            if character == (row["character"] or "").lower():
                semantic += 0.30
            elif character in tags:
                semantic += 0.12
            else:
                semantic -= 0.25
            semantic += 0.08 if "solo" in tags else 0.0
            semantic -= 0.08 if any(
                value in tags for value in ("multiple_boys", "multiple_girls")
            ) else 0.0
        if context.action:
            semantic += 0.25 if context.action.lower() in (
                (row["action"] or "") + "," + (row["tags"] or "")
            ).lower() else -0.25
        components = {
            "sequence_fit": _bounded(0.6 * sequence_fit + 0.4 * semantic),
            "music_fit": _bounded(context.music_fit_by_shot.get(shot_id, sequence_fit)),
            "reference_fit": _bounded(context.reference_fit_by_shot.get(shot_id, 0.5)),
            "continuity": _continuity(row, previous),
            "novelty": _novelty(row, selected),
            "preference": _bounded(context.preference_by_shot.get(shot_id, 0.5)),
        }
        weights = {
            "sequence_fit": 0.27, "music_fit": 0.18, "reference_fit": 0.17,
            "continuity": 0.15, "novelty": 0.13, "preference": 0.1,
        }
        contextual = sum(components[key] * weights[key] for key in weights)
        total = 0.42 * intrinsic + 0.58 * contextual
        ranked.append(
            RankedCandidate(
                shot_id=shot_id,
                intrinsic=intrinsic,
                contextual=contextual,
                total=total,
                intrinsic_components=intrinsic_components,
                contextual_components=components,
            )
        )
    # Maximal-marginal-relevance pass. This is part of contextual suitability,
    # not an intrinsic quality mutation: A/B/C must not be near-duplicates.
    remaining = {item.shot_id: item for item in ranked}
    diverse: list[RankedCandidate] = []
    while remaining and len(diverse) < limit:
        rescored = []
        selected_ids = [item.shot_id for item in diverse]
        for item in remaining.values():
            novelty = (
                _pool_novelty(by_id[item.shot_id], selected_ids, by_id)
                if selected_ids else 0.5
            )
            components = dict(item.contextual_components)
            components["novelty"] = novelty
            contextual = (
                components["sequence_fit"] * 0.27
                + components["music_fit"] * 0.18
                + components["reference_fit"] * 0.17
                + components["continuity"] * 0.15
                + components["novelty"] * 0.13
                + components["preference"] * 0.1
            )
            total = 0.42 * item.intrinsic + 0.58 * contextual
            rescored.append(
                item.model_copy(
                    update={
                        "contextual_components": components,
                        "contextual": contextual,
                        "total": total,
                    }
                )
            )
        rescored.sort(
            key=lambda item: (
                -(0.72 * item.total + 0.28 * item.contextual_components["novelty"]),
                item.shot_id,
            )
        )
        chosen = rescored[0]
        diverse.append(chosen)
        remaining.pop(chosen.shot_id)
    ranked = diverse
    with conn:
        for item in ranked:
            conn.execute(
                """
                INSERT INTO shot_scores(shot_id,version,components_json,composite)
                VALUES (?,?,?,?)
                ON CONFLICT(shot_id,version) DO UPDATE SET
                  components_json=excluded.components_json,composite=excluded.composite
                """,
                (
                    item.shot_id,
                    RANKING_VERSION,
                    json.dumps(item.intrinsic_components, sort_keys=True),
                    item.intrinsic,
                ),
            )
            conn.execute(
                """
                INSERT INTO candidate_scores(
                  project_id,role,shot_id,version,components_json,contextual,total
                ) VALUES (?,?,?,?,?,?,?)
                ON CONFLICT(project_id,role,shot_id,version) DO UPDATE SET
                  components_json=excluded.components_json,
                  contextual=excluded.contextual,total=excluded.total
                """,
                (
                    context.project_id, context.role, item.shot_id, RANKING_VERSION,
                    json.dumps(item.contextual_components, sort_keys=True),
                    item.contextual, item.total,
                ),
            )
    return ranked


__all__ = [
    "RANKING_VERSION",
    "CandidateContext",
    "RankedCandidate",
    "rank_candidates",
]
