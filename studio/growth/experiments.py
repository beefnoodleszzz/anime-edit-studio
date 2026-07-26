"""Versioned growth experiments; retention is a signal, never a hard rule."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from studio.editspec.schema import EditSpec


class GrowthMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")
    views: int = Field(..., ge=0)
    likes: int = Field(0, ge=0)
    comments: int = Field(0, ge=0)
    shares: int = Field(0, ge=0)
    follows: int = Field(0, ge=0)
    retention_2s: float | None = Field(None, ge=0, le=1)
    retention_3s: float | None = Field(None, ge=0, le=1)
    completion_rate: float | None = Field(None, ge=0, le=1)
    avg_watch_sec: float | None = Field(None, ge=0)
    retention_curve: list[tuple[float, float]] = Field(default_factory=list)
    external_post_id: str | None = None


def create_experiment(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    name: str,
    base_spec_path: Path,
    platform: str,
    variants: list[tuple[str, str, str, Path]],
) -> int:
    if len(variants) < 2:
        raise ValueError("增长实验至少需要 A/B 两个 variant")
    labels = [item[0] for item in variants]
    if len(labels) != len(set(labels)):
        raise ValueError("variant label 必须唯一")
    with conn:
        cursor = conn.execute(
            """
            INSERT INTO growth_experiments(
              project_id,name,base_spec_path,platform,status
            ) VALUES (?,?,?,?,'draft')
            """,
            (project_id, name, str(base_spec_path), platform),
        )
        experiment_id = int(cursor.lastrowid)
        conn.executemany(
            """
            INSERT INTO growth_variants(
              experiment_id,label,hook_text,hook_sub,editspec_path
            ) VALUES (?,?,?,?,?)
            """,
            [
                (experiment_id, label, hook, sub, str(path))
                for label, hook, sub, path in variants
            ],
        )
    return experiment_id


def _retention_at(curve: list[tuple[float, float]], sec: float) -> float | None:
    if not curve:
        return None
    ordered = sorted(curve)
    prior = ordered[0][1]
    for point_sec, value in ordered:
        if point_sec > sec:
            break
        prior = value
    return prior


def ingest_metrics(
    conn: sqlite3.Connection,
    *,
    variant_id: int,
    metrics: GrowthMetrics,
    spec: EditSpec,
) -> dict:
    with conn:
        conn.execute(
            """
            UPDATE growth_variants SET
              views=?,likes=?,comments=?,shares=?,follows=?,
              retention_2s=?,retention_3s=?,completion_rate=?,avg_watch_sec=?,
              retention_curve_json=?,external_post_id=?,
              updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')
            WHERE id=?
            """,
            (
                metrics.views, metrics.likes, metrics.comments, metrics.shares,
                metrics.follows, metrics.retention_2s, metrics.retention_3s,
                metrics.completion_rate, metrics.avg_watch_sec,
                json.dumps(metrics.retention_curve), metrics.external_post_id,
                variant_id,
            ),
        )
        conn.execute("DELETE FROM shot_outcomes WHERE variant_id=?", (variant_id,))
        outcomes = []
        for clip in spec.clips:
            retention_in = _retention_at(metrics.retention_curve, clip.timeline.in_sec)
            retention_out = _retention_at(metrics.retention_curve, clip.timeline.out_sec)
            drop = (
                retention_in - retention_out
                if retention_in is not None and retention_out is not None else None
            )
            conn.execute(
                """
                INSERT INTO shot_outcomes(
                  variant_id,shot_id,start_sec,end_sec,retention_in,retention_out,
                  retention_drop,updated_at
                ) VALUES (?,?,?,?,?,?,?,strftime('%Y-%m-%dT%H:%M:%fZ','now'))
                """,
                (
                    variant_id, clip.shot_id or clip.id,
                    clip.timeline.in_sec, clip.timeline.out_sec,
                    retention_in, retention_out, drop,
                ),
            )
            outcomes.append(
                {"shot_id": clip.shot_id or clip.id, "retention_drop": drop}
            )
    return {"variant_id": variant_id, "outcomes": outcomes}


def retention_preferences(
    conn: sqlite3.Connection,
    *,
    experiment_id: int,
    min_views: int = 500,
    min_drop_delta: float = 0.05,
) -> int:
    """Turn clear retention differences into low-authority pairwise signals."""
    rows = conn.execute(
        """
        SELECT gv.id,gv.views,ge.project_id,so.shot_id,so.start_sec,so.retention_drop
        FROM growth_variants gv
        JOIN growth_experiments ge ON ge.id=gv.experiment_id
        JOIN shot_outcomes so ON so.variant_id=gv.id
        WHERE gv.experiment_id=? AND gv.views>=?
          AND so.retention_drop IS NOT NULL
        ORDER BY so.start_sec,so.retention_drop
        """,
        (experiment_id, min_views),
    ).fetchall()
    written = 0
    grouped: dict[int, list] = {}
    for row in rows:
        bucket = round(float(row["start_sec"]) * 2)
        grouped.setdefault(bucket, []).append(row)
    with conn:
        for bucket_rows in grouped.values():
            if len(bucket_rows) < 2:
                continue
            winner, loser = bucket_rows[0], bucket_rows[-1]
            delta = float(loser["retention_drop"]) - float(winner["retention_drop"])
            if winner["shot_id"] == loser["shot_id"] or delta < min_drop_delta:
                continue
            conn.execute(
                """
                INSERT INTO preference_pairs(
                  winner_shot_id,loser_shot_id,context_json,project_style,project_id
                ) VALUES (?,?,?,?,?)
                """,
                (
                    winner["shot_id"], loser["shot_id"],
                    json.dumps(
                        {
                            "source": "retention",
                            "drop_delta": delta,
                            "authority": "signal_only",
                        },
                        sort_keys=True,
                    ),
                    "growth_metrics",
                    winner["project_id"],
                ),
            )
            written += 1
    return written


__all__ = [
    "GrowthMetrics",
    "create_experiment",
    "ingest_metrics",
    "retention_preferences",
]
