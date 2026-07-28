"""Versioned multi-frame character evidence without mutating shot labels."""
from __future__ import annotations

import sqlite3
import json
import os
import tempfile
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from studio.asset_intelligence.visual.tagger import TagResult

CHARACTER_EVIDENCE_VERSION = "character-evidence-1.0.0"


class CharacterEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str = CHARACTER_EVIDENCE_VERSION
    shot_id: str
    character: str
    confidence: float = Field(..., ge=0, le=1)
    matching_frames: int = Field(..., ge=0)
    sampled_frames: int = Field(..., ge=1)
    representative_frame: str | None = None


def candidate_character_frames(
    conn: sqlite3.Connection,
    *,
    character: str,
    neighbor_radius: int = 6,
) -> dict[str, list[Path]]:
    """Return multi-frame candidates near shots already known to contain a character."""
    if neighbor_radius < 0:
        raise ValueError("neighbor_radius 必须 >= 0")
    conn.row_factory = sqlite3.Row
    known = conn.execute(
        """
        SELECT asset_id,idx FROM shots
        WHERE lower(coalesce(character,'') || ',' || coalesce(tags,'')) LIKE ?
        """,
        (f"%{character.lower()}%",),
    ).fetchall()
    windows: dict[str, set[int]] = {}
    for row in known:
        windows.setdefault(row["asset_id"], set()).update(
            range(
                max(0, int(row["idx"]) - neighbor_radius),
                int(row["idx"]) + neighbor_radius + 1,
            )
        )
    output: dict[str, list[Path]] = {}
    for asset_id, indices in windows.items():
        placeholders = ",".join("?" for _ in indices)
        rows = conn.execute(
            f"""
            SELECT id,keyframe FROM shots
            WHERE asset_id=? AND idx IN ({placeholders}) AND keyframe IS NOT NULL
            """,
            (asset_id, *sorted(indices)),
        ).fetchall()
        for row in rows:
            keyframe = Path(row["keyframe"])
            prefix = keyframe.stem.rsplit("_c", 1)[0]
            output[row["id"]] = (
                sorted(keyframe.parent.glob(f"{prefix}_c*.jpg")) or [keyframe]
            )
    return output


def infer_character_evidence(
    frames_by_shot: dict[str, list[Path]],
    *,
    character: str,
    tag: Callable[[list[Path]], list[TagResult]],
    batch_size: int = 64,
    minimum_confidence: float = 0.9,
) -> list[CharacterEvidence]:
    """Infer evidence in bounded batches and retain exact frame provenance."""
    if batch_size < 1:
        raise ValueError("batch_size 必须 >= 1")
    flat = [
        (shot_id, path)
        for shot_id, paths in sorted(frames_by_shot.items())
        for path in paths
    ]
    results: list[tuple[str, Path, TagResult]] = []
    for offset in range(0, len(flat), batch_size):
        batch = flat[offset : offset + batch_size]
        tagged = tag([path for _, path in batch])
        if len(tagged) != len(batch):
            raise ValueError("角色证据模型返回数量不匹配")
        results.extend(
            (shot_id, path, result)
            for (shot_id, path), result in zip(batch, tagged, strict=True)
        )
    by_shot: dict[str, list[tuple[Path, float]]] = {}
    for shot_id, path, result in results:
        confidence = float(result.characters.get(character, 0.0))
        by_shot.setdefault(shot_id, []).append((path, confidence))
    evidence = []
    for shot_id, values in sorted(by_shot.items()):
        matches = [(path, score) for path, score in values if score >= minimum_confidence]
        if not matches:
            continue
        best_path, best_score = max(matches, key=lambda item: (item[1], str(item[0])))
        evidence.append(
            CharacterEvidence(
                shot_id=shot_id,
                character=character,
                confidence=best_score,
                matching_frames=len(matches),
                sampled_frames=len(values),
                representative_frame=str(best_path),
            )
        )
    return evidence


def write_character_evidence(
    path: Path, evidence: list[CharacterEvidence]
) -> None:
    """Persist an auditable evidence artifact; database truth remains unchanged."""
    path.parent.mkdir(parents=True, exist_ok=True)
    value = json.dumps(
            {
                "version": CHARACTER_EVIDENCE_VERSION,
                "items": [item.model_dump(mode="json") for item in evidence],
            },
            ensure_ascii=False,
            indent=2,
    )
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except Exception:
        Path(temporary).unlink(missing_ok=True)
        raise


__all__ = [
    "CHARACTER_EVIDENCE_VERSION",
    "CharacterEvidence",
    "candidate_character_frames",
    "infer_character_evidence",
    "write_character_evidence",
]
