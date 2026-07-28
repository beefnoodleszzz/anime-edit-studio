"""Deterministic broad retrieval; ranking is deliberately a separate stage."""
from __future__ import annotations

import sqlite3

from pydantic import BaseModel, ConfigDict, Field


class RetrievalQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset_ids: list[str] = Field(default_factory=list)
    project_id: str | None = None
    character: str | None = None
    action: str | None = None
    text: str | None = None
    min_motion: float | None = Field(None, ge=0)
    max_motion: float | None = Field(None, ge=0)
    subtitle_allowed: bool = True
    min_face: float | None = Field(None, ge=0, le=1)
    min_pose: float | None = Field(None, ge=0, le=1)
    min_eye: float | None = Field(None, ge=0, le=1)
    min_image_quality: float | None = Field(None, ge=0, le=1)
    min_cutability: float | None = Field(None, ge=0, le=1)
    min_aesthetic: float | None = Field(None, ge=0, le=10)
    required_any_tags: list[str] = Field(default_factory=list)
    excluded_tags: list[str] = Field(default_factory=list)
    min_duration_sec: float | None = Field(None, gt=0)
    max_duration_sec: float | None = Field(None, gt=0)
    limit: int = Field(200, ge=100, le=300)


def retrieve(conn: sqlite3.Connection, query: RetrievalQuery) -> list[str]:
    """Return stable Shot ids only; no contextual score is hidden here."""
    joins, where, params = [], [], []
    if query.text:
        joins.append("JOIN shots_fts f ON f.rowid=s.rowid")
        where.append("shots_fts MATCH ?")
        params.append(query.text)
    if query.asset_ids:
        placeholders = ",".join("?" for _ in query.asset_ids)
        where.append(f"s.asset_id IN ({placeholders})")
        params.extend(query.asset_ids)
    if query.project_id:
        where.append(
            "NOT EXISTS (SELECT 1 FROM review_decisions rd "
            "WHERE rd.project_id=? AND rd.shot_id=s.id AND rd.decision='reject')"
        )
        params.append(query.project_id)
    if query.character:
        needle = f"%{query.character.lower()}%"
        where.append(
            "(lower(coalesce(s.character,'')) LIKE ? "
            "OR lower(coalesce(s.tags,'')) LIKE ?)"
        )
        params.extend([needle, needle])
    if query.action:
        needle = f"%{query.action.lower()}%"
        where.append(
            "(lower(coalesce(s.action,'')) LIKE ? "
            "OR lower(coalesce(s.tags,'')) LIKE ?)"
        )
        params.extend([needle, needle])
    if query.min_motion is not None:
        where.append("coalesce(s.subject_motion,s.motion_mag,0)>=?")
        params.append(query.min_motion)
    if query.max_motion is not None:
        where.append("coalesce(s.subject_motion,s.motion_mag,0)<=?")
        params.append(query.max_motion)
    if not query.subtitle_allowed:
        where.append("coalesce(json_extract(s.subtitle_region,'$.present'),0)=0")
        # OCR is intentionally conservative and can miss stylised/burned text.
        # Tagger evidence is therefore a second deterministic cleanliness gate.
        for tag in (
            "subtitled",
            "english_text",
            "japanese_text",
            "korean_text",
            "chinese_text",
            "text_focus",
        ):
            where.append("lower(coalesce(s.tags,'')) NOT LIKE ?")
            params.append(f"%{tag}%")
    if query.min_face is not None:
        where.append("coalesce(s.face_visibility,0)>=?")
        params.append(query.min_face)
    if query.min_pose is not None:
        where.append("coalesce(s.pose_quality,0)>=?")
        params.append(query.min_pose)
    if query.min_eye is not None:
        where.append("coalesce(s.eye_visibility,0)>=?")
        params.append(query.min_eye)
    if query.min_image_quality is not None:
        where.append("coalesce(s.image_quality,0)>=?")
        params.append(query.min_image_quality)
    if query.min_cutability is not None:
        where.append("coalesce(s.cutability,0)>=?")
        params.append(query.min_cutability)
    if query.min_aesthetic is not None:
        where.append("coalesce(s.aesthetic,0)>=?")
        params.append(query.min_aesthetic)
    required = [tag.strip().lower() for tag in query.required_any_tags if tag.strip()]
    if required:
        where.append(
            "("
            + " OR ".join("lower(coalesce(s.tags,'')) LIKE ?" for _ in required)
            + ")"
        )
        params.extend(f"%{tag}%" for tag in required)
    for tag in query.excluded_tags:
        normalized = tag.strip().lower()
        if normalized:
            where.append("lower(coalesce(s.tags,'')) NOT LIKE ?")
            params.append(f"%{normalized}%")
    if query.min_duration_sec is not None:
        where.append("s.end_sec-s.start_sec>=?")
        params.append(query.min_duration_sec)
    if query.max_duration_sec is not None:
        where.append("s.end_sec-s.start_sec<=?")
        params.append(query.max_duration_sec)

    sql = "SELECT s.id FROM shots s " + " ".join(joins)
    if where:
        sql += " WHERE " + " AND ".join(where)
    # Broad retrieval order is deterministic and mildly quality-aware only to
    # make the limit stable. Contextual suitability belongs to Phase 4 ranking.
    sql += (
        " ORDER BY coalesce(s.image_quality,0) DESC,"
        "coalesce(s.face_visibility,0) DESC,s.asset_id,s.idx LIMIT ?"
    )
    params.append(query.limit)
    return [row[0] for row in conn.execute(sql, params)]


__all__ = ["RetrievalQuery", "retrieve"]
