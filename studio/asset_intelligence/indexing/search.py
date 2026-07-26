"""Composable Shot retrieval over intrinsic analysis dimensions.

This is filtering/retrieval, not Phase 4 contextual ranking.  It deliberately
does not collapse all dimensions into one aesthetic score.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True)
class SearchQuery:
    text: str | None = None
    asset_id: str | None = None
    character: str | None = None
    action: str | None = None
    motion_direction: str | None = None
    min_motion: float | None = None
    max_motion: float | None = None
    subtitle: bool | None = None
    min_face_visibility: float | None = None
    min_eye_visibility: float | None = None
    min_pose_quality: float | None = None
    min_visual_energy: float | None = None
    max_compression: float | None = None
    limit: int = 50

    def __post_init__(self):
        if not 1 <= self.limit <= 500:
            raise ValueError("limit 必须在 1..500")
        for name in (
            "min_face_visibility", "min_eye_visibility", "min_pose_quality",
            "min_visual_energy", "max_compression",
        ):
            value = getattr(self, name)
            if value is not None and not 0 <= value <= 1:
                raise ValueError(f"{name} 必须在 0..1")


_SELECT = """
SELECT
  s.id,s.asset_id,s.idx,s.start_sec,s.end_sec,s.keyframe,
  s.character,s.action,s.emotion,s.tags,s.motion_dir,s.motion_mag,
  s.shot_scale,s.shot_scale_confidence,
  s.subject_motion,s.subject_motion_confidence,
  s.pose_quality,s.pose_quality_confidence,
  s.face_visibility,s.face_visibility_confidence,
  s.eye_visibility,s.eye_visibility_confidence,
  s.visual_energy,s.visual_energy_confidence,
  s.compression_score,s.compression_score_confidence,
  s.subtitle_region,s.subtitle_region_confidence,
  s.color_palette,s.audio_energy,s.audio_energy_confidence,
  s.music_presence,s.music_presence_confidence,
  s.cutability,s.cutability_confidence,s.analysis_version
FROM shots s
"""


def search_shots(conn: sqlite3.Connection, query: SearchQuery) -> list[dict]:
    conn.row_factory = sqlite3.Row
    joins, where, params = [], [], []
    if query.text:
        joins.append("JOIN shots_fts f ON f.rowid=s.rowid")
        where.append("shots_fts MATCH ?")
        params.append(query.text)
    if query.asset_id:
        where.append("s.asset_id=?")
        params.append(query.asset_id)
    if query.character:
        where.append("(lower(coalesce(s.character,'')) LIKE ? OR lower(coalesce(s.tags,'')) LIKE ?)")
        needle = f"%{query.character.lower()}%"
        params.extend([needle, needle])
    if query.action:
        where.append("(lower(coalesce(s.action,'')) LIKE ? OR lower(coalesce(s.tags,'')) LIKE ?)")
        needle = f"%{query.action.lower()}%"
        params.extend([needle, needle])
    if query.motion_direction:
        where.append("s.motion_dir=?")
        params.append(query.motion_direction)
    if query.min_motion is not None:
        where.append("s.motion_mag>=?")
        params.append(query.min_motion)
    if query.max_motion is not None:
        where.append("s.motion_mag<=?")
        params.append(query.max_motion)
    if query.subtitle is not None:
        where.append("coalesce(json_extract(s.subtitle_region,'$.present'),0)=?")
        params.append(1 if query.subtitle else 0)
    for column, value in (
        ("face_visibility", query.min_face_visibility),
        ("eye_visibility", query.min_eye_visibility),
        ("pose_quality", query.min_pose_quality),
        ("visual_energy", query.min_visual_energy),
    ):
        if value is not None:
            where.append(f"s.{column}>=?")
            params.append(value)
    if query.max_compression is not None:
        where.append("s.compression_score<=?")
        params.append(query.max_compression)

    # Stable ordering only. Phase 4 owns contextual scoring/ranking.
    sql = _SELECT + " ".join(joins)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY s.asset_id,s.idx LIMIT ?"
    params.append(query.limit)
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


__all__ = ["SearchQuery", "search_shots"]
