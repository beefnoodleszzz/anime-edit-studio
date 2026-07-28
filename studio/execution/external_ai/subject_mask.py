"""Self-hosted per-frame subject foreground masks (W4a).

Resolve's Magic Mask was visually disproved (A12), so 2.5D parallax and
occlusion-cut transitions need their own subject signal.  This module samples a
shot, segments the foreground per frame, and records the subject's coverage,
bounding box, and centroid trajectory.  That trajectory is exactly what the
later Fusion Recipes consume: parallax needs foreground-vs-background
separation, and an occlusion cut needs to know when the subject sweeps across
the frame to hide the join.

Segmentation is injectable.  Production uses rembg; tests pass a deterministic
stub so no model download is required.  This is analysis only — it does not
touch Resolve and it does not, by itself, make ``parallax_25d`` or
``occlusion_cut`` executable (those stay unverified until render-checked).
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import cv2
import numpy as np
from pydantic import BaseModel, ConfigDict, Field

SUBJECT_MASK_VERSION = "subject-mask-1.0.0"
MASK_THRESHOLD = 0.5


class ForegroundSegmenter(Protocol):
    def segment(self, frame_bgr: np.ndarray) -> np.ndarray:
        """Return an H×W alpha in [0,1]; 1 = foreground subject."""
        ...


class RembgSegmenter:
    """rembg-backed matte.  Imported lazily so the dependency is optional."""

    def __init__(self, model_name: str = "u2netp"):
        from rembg import new_session  # noqa: PLC0415

        self._session = new_session(model_name)

    def segment(self, frame_bgr: np.ndarray) -> np.ndarray:
        from rembg import remove  # noqa: PLC0415

        rgba = remove(
            cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB), session=self._session
        )
        alpha = np.asarray(rgba)[..., 3].astype(np.float64) / 255.0
        return alpha


class SubjectMaskSample(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sec: float
    coverage: float = Field(..., ge=0, le=1)
    # Normalized subject bounding box; None when the frame has no foreground.
    x: float | None = None
    y: float | None = None
    width: float | None = None
    height: float | None = None
    cx: float | None = None
    cy: float | None = None


class SubjectLayer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str = SUBJECT_MASK_VERSION
    sample_fps: float
    mean_coverage: float = Field(..., ge=0, le=1)
    # Horizontal sweep of the subject centroid across the shot (0..1).  A large
    # sweep is the signal an occlusion cut can hide behind.
    horizontal_sweep: float = Field(..., ge=0, le=1)
    samples: list[SubjectMaskSample] = Field(default_factory=list)


def _bbox_from_alpha(alpha: np.ndarray) -> SubjectMaskSample | None:
    height, width = alpha.shape
    mask = alpha >= MASK_THRESHOLD
    coverage = float(mask.mean())
    if coverage <= 0:
        return SubjectMaskSample(sec=0.0, coverage=0.0)
    rows = np.any(mask, axis=1)
    cols = np.any(mask, axis=0)
    y0, y1 = np.where(rows)[0][[0, -1]]
    x0, x1 = np.where(cols)[0][[0, -1]]
    bw = (x1 - x0 + 1) / width
    bh = (y1 - y0 + 1) / height
    ys, xs = np.nonzero(mask)
    return SubjectMaskSample(
        sec=0.0,
        coverage=round(coverage, 6),
        x=round(float(x0) / width, 6),
        y=round(float(y0) / height, 6),
        width=round(float(bw), 6),
        height=round(float(bh), 6),
        cx=round(float(xs.mean()) / width, 6),
        cy=round(float(ys.mean()) / height, 6),
    )


@dataclass
class SubjectMaskAnalyzer:
    segmenter: ForegroundSegmenter
    sample_fps: float = 6.0

    def analyze(self, media: Path, *, start_sec: float, end_sec: float) -> SubjectLayer:
        if end_sec <= start_sec:
            raise ValueError("end_sec 必须大于 start_sec")
        capture = cv2.VideoCapture(str(media))
        if not capture.isOpened():
            raise ValueError(f"无法打开媒体: {media}")
        times = np.arange(start_sec, end_sec, 1 / self.sample_fps)
        samples: list[SubjectMaskSample] = []
        try:
            for sec in times:
                capture.set(cv2.CAP_PROP_POS_MSEC, float(sec) * 1000)
                ok, frame = capture.read()
                if not ok:
                    continue
                alpha = self.segmenter.segment(frame)
                sample = _bbox_from_alpha(alpha)
                if sample is None:
                    continue
                samples.append(sample.model_copy(update={"sec": round(float(sec - start_sec), 6)}))
        finally:
            capture.release()
        if not samples:
            return SubjectLayer(sample_fps=self.sample_fps, mean_coverage=0.0, horizontal_sweep=0.0)
        centroids = [s.cx for s in samples if s.cx is not None]
        sweep = (max(centroids) - min(centroids)) if centroids else 0.0
        return SubjectLayer(
            sample_fps=self.sample_fps,
            mean_coverage=round(float(np.mean([s.coverage for s in samples])), 6),
            horizontal_sweep=round(float(sweep), 6),
            samples=samples,
        )


def _migration_ready(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS subject_layers (
            shot_id TEXT NOT NULL REFERENCES shots(id) ON DELETE CASCADE,
            version TEXT NOT NULL,
            sample_fps REAL NOT NULL,
            mean_coverage REAL NOT NULL,
            horizontal_sweep REAL NOT NULL,
            samples_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
            PRIMARY KEY(shot_id, version)
        )
        """
    )


def analyze_pending_subject_layers(
    conn: sqlite3.Connection,
    *,
    analyzer: SubjectMaskAnalyzer,
    asset_id: str | None = None,
    limit: int | None = None,
) -> dict:
    """Populate ``subject_layers`` for shots missing the current version."""
    conn.row_factory = sqlite3.Row
    _migration_ready(conn)
    where = (
        "WHERE s.id NOT IN (SELECT shot_id FROM subject_layers WHERE version=?)"
    )
    params: list[object] = [SUBJECT_MASK_VERSION]
    if asset_id:
        where += " AND s.asset_id=?"
        params.append(asset_id)
    sql = (
        "SELECT s.id,s.start_sec,s.end_sec,a.path,a.proxy_path "
        "FROM shots s JOIN assets a ON a.id=s.asset_id "
        f"{where} ORDER BY s.asset_id,s.idx"
    )
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    done, skipped, failed = 0, 0, []
    for row in rows:
        media = next(
            (Path(v) for v in (row["proxy_path"], row["path"]) if v and Path(v).is_file()),
            None,
        )
        if media is None:
            skipped += 1
            continue
        try:
            layer = analyzer.analyze(
                media, start_sec=float(row["start_sec"]), end_sec=float(row["end_sec"])
            )
            conn.execute(
                """
                INSERT INTO subject_layers(
                    shot_id,version,sample_fps,mean_coverage,horizontal_sweep,samples_json
                ) VALUES (?,?,?,?,?,?)
                ON CONFLICT(shot_id,version) DO UPDATE SET
                    sample_fps=excluded.sample_fps,
                    mean_coverage=excluded.mean_coverage,
                    horizontal_sweep=excluded.horizontal_sweep,
                    samples_json=excluded.samples_json,
                    created_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')
                """,
                (
                    row["id"], SUBJECT_MASK_VERSION, layer.sample_fps,
                    layer.mean_coverage, layer.horizontal_sweep,
                    json.dumps([s.model_dump() for s in layer.samples], separators=(",", ":")),
                ),
            )
            done += 1
        except Exception as exc:  # noqa: BLE001
            failed.append({"shot_id": row["id"], "error": str(exc)})
    conn.commit()
    return {
        "selected": len(rows),
        "analyzed": done,
        "skipped_no_media": skipped,
        "failed": failed,
        "version": SUBJECT_MASK_VERSION,
    }


__all__ = [
    "SUBJECT_MASK_VERSION",
    "ForegroundSegmenter",
    "RembgSegmenter",
    "SubjectMaskSample",
    "SubjectLayer",
    "SubjectMaskAnalyzer",
    "analyze_pending_subject_layers",
]
