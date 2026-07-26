"""Anime-specific WD tagger with versioned inference metadata."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from studio.core.cache import JsonCache
from studio.core.hashing import analysis_cache_key, file_sha256

MODEL = "wdtagger/SmilingWolf/wd-swinv2-tagger-v3"
MODEL_VERSION = "wd-swinv2-tagger-v3"
PIPELINE_VERSION = "wd-tagger-1.0.0"
GENERAL_THRESHOLD = 0.35
CHARACTER_THRESHOLD = 0.9
ACTION_TAGS = {
    "fighting", "running", "walking", "jumping", "falling", "flying",
    "kicking", "punching", "holding_sword", "looking_back", "sitting",
    "standing", "crying", "smiling", "shouting", "talking",
}
EMOTION_TAGS = {
    "angry", "sad", "happy", "scared", "surprised", "serious",
    "expressionless", "crying", "smile", "grin",
}


@dataclass(frozen=True)
class TagResult:
    characters: dict[str, float]
    general: dict[str, float]
    rating: dict[str, float]

    def to_dict(self) -> dict:
        return {
            "characters": self.characters,
            "general": self.general,
            "rating": self.rating,
        }


class TagBackend(Protocol):
    def tag(self, paths: list[Path]) -> list[TagResult]: ...


class WDTaggerBackend:
    def __init__(self):
        import torch
        from wdtagger import Tagger

        self._tagger = Tagger(model_repo="SmilingWolf/wd-swinv2-tagger-v3")
        self._mps = False
        # wdtagger currently recognises CUDA only and otherwise hard-codes CPU.
        # On Apple Silicon the same torch model runs on MPS and is substantially
        # faster for a full anime library. Keep the third-party public API while
        # correcting device selection at our adapter boundary.
        if torch.backends.mps.is_available():
            self._tagger.torch_device = torch.device("mps")
            self._tagger.model = self._tagger.model.to("mps").eval()
            self._mps = True

    def tag(self, paths: list[Path]) -> list[TagResult]:
        if self._mps:
            import torch
            import torch.nn.functional as functional
            from wdtagger import (
                Result,
                get_tags,
                pil_ensure_rgb,
                pil_pad_square,
                to_pil,
            )

            images = [pil_pad_square(pil_ensure_rgb(to_pil(path))) for path in paths]
            inputs = torch.stack(
                [self._tagger.transform(image) for image in images]
            )[:, [2, 1, 0]].to("mps")
            with torch.inference_mode(), torch.autocast(
                device_type="mps", dtype=torch.float16
            ):
                outputs = functional.sigmoid(self._tagger.model(inputs))
            raw = [
                Result(
                    *get_tags(
                        probs=output.float().cpu(),
                        labels=self._tagger.labels,
                        gen_threshold=GENERAL_THRESHOLD,
                        char_threshold=CHARACTER_THRESHOLD,
                    )
                )
                for output in outputs
            ]
        else:
            raw = self._tagger.tag(
                paths,
                general_threshold=GENERAL_THRESHOLD,
                character_threshold=CHARACTER_THRESHOLD,
            )
        values = list(raw) if isinstance(raw, (list, tuple)) else [raw]
        return [
            TagResult(
                characters=dict(value.character_tag_data),
                general=dict(value.general_tag_data),
                rating=dict(value.rating_data),
            )
            for value in values
        ]


def hydrate_legacy_tags(conn: sqlite3.Connection) -> int:
    """Reuse thresholded v1 WD tags without inventing lost probabilities."""
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT id,tags FROM shots
        WHERE character IS NULL AND tags IS NOT NULL AND tags!=''
        """
    ).fetchall()
    updates = []
    for row in rows:
        tags = [value.strip() for value in row["tags"].split(",") if value.strip()]
        action = next((tag for tag in tags if tag in ACTION_TAGS), "")
        emotion = next((tag for tag in tags if tag in EMOTION_TAGS), "")
        updates.append(
            (
                "",
                0.0,
                action,
                GENERAL_THRESHOLD if action else 0.0,
                emotion,
                GENERAL_THRESHOLD if emotion else 0.0,
                row["id"],
            )
        )
    with conn:
        conn.executemany(
            """
            UPDATE shots SET
              character=?,character_confidence=?,
              action=?,action_confidence=?,
              emotion=?,emotion_confidence=?
            WHERE id=?
            """,
            updates,
        )
    return len(updates)


def tag_pending(
    conn: sqlite3.Connection,
    *,
    cache_root: Path,
    backend: TagBackend,
    asset_id: str | None = None,
    batch_size: int = 64,
    limit: int | None = None,
) -> int:
    conn.row_factory = sqlite3.Row
    where = "WHERE s.character IS NULL AND s.keyframe IS NOT NULL"
    params: list[object] = []
    if asset_id:
        where += " AND s.asset_id=?"
        params.append(asset_id)
    sql = (
        "SELECT s.rowid,s.id,s.keyframe,a.sha256 FROM shots s "
        f"JOIN assets a ON a.id=s.asset_id {where} ORDER BY s.asset_id,s.idx"
    )
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    cache = JsonCache(cache_root)
    written = 0

    for offset in range(0, len(rows), batch_size):
        chunk = rows[offset : offset + batch_size]
        results: list[TagResult | None] = []
        missing_paths: list[Path] = []
        missing_indices: list[int] = []
        keys = []
        for index, row in enumerate(chunk):
            path = Path(row["keyframe"])
            key = analysis_cache_key(
                asset_hash=row["sha256"],
                model=MODEL,
                model_version=MODEL_VERSION,
                pipeline_version=PIPELINE_VERSION,
                parameters={
                    "shot_id": row["id"],
                    "keyframe_sha256": file_sha256(path),
                    "general_threshold": GENERAL_THRESHOLD,
                    "character_threshold": CHARACTER_THRESHOLD,
                },
            )
            keys.append(key)
            cached = cache.get("wd-tags-v2", key)
            if cached:
                results.append(
                    TagResult(cached["characters"], cached["general"], cached["rating"])
                )
            else:
                results.append(None)
                missing_paths.append(path)
                missing_indices.append(index)
        if missing_paths:
            inferred = backend.tag(missing_paths)
            if len(inferred) != len(missing_paths):
                raise RuntimeError("tagger 返回数量与输入不一致")
            for index, result in zip(missing_indices, inferred, strict=True):
                results[index] = result
                cache.put("wd-tags-v2", keys[index], result.to_dict())

        with conn:
            for row, result in zip(chunk, results, strict=True):
                assert result is not None
                character = next(iter(result.characters), "")
                character_confidence = (
                    result.characters[character] if character else 0.0
                )
                action = next((tag for tag in result.general if tag in ACTION_TAGS), "")
                emotion = next((tag for tag in result.general if tag in EMOTION_TAGS), "")
                tags = ",".join([*result.characters, *result.general])
                conn.execute(
                    """
                    UPDATE shots SET
                      character=?,character_confidence=?,
                      action=?,action_confidence=?,
                      emotion=?,emotion_confidence=?,tags=?
                    WHERE id=?
                    """,
                    (
                        character, character_confidence,
                        action, result.general.get(action, 0.0),
                        emotion, result.general.get(emotion, 0.0),
                        tags, row["id"],
                    ),
                )
                # Newly detected shots have no FTS row. Explicit rowid preserves
                # the join contract used by deterministic search.
                exists = conn.execute(
                    "SELECT 1 FROM shots_fts WHERE rowid=?", (row["rowid"],)
                ).fetchone()
                if not exists:
                    conn.execute(
                        "INSERT INTO shots_fts(rowid,shot_id,character,tags) VALUES (?,?,?,?)",
                        (row["rowid"], row["id"], character, tags),
                    )
                written += 1
    return written


__all__ = [
    "CHARACTER_THRESHOLD",
    "GENERAL_THRESHOLD",
    "PIPELINE_VERSION",
    "TagResult",
    "WDTaggerBackend",
    "hydrate_legacy_tags",
    "tag_pending",
]
