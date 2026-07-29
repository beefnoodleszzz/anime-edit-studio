"""Identify a character the tagger cannot name, from a few confirmed seeds.

The WD tagger names characters from a fixed vocabulary.  Akaza is not in it —
the only labels containing "akaza" are ``akaza_akari`` (Yuru Yuri) and
``yuri_sakazaki`` (KOF), and the model instead reads him as
``ryoumen_sukuna_(jujutsu_kaisen)`` at 0.09–0.40.  So ``shots.character`` is
empty for him and always will be, no matter how the threshold is tuned.  Any
retrieval that asks for him by name gets nothing, and — worse, as this module
exists to prevent — a loose fallback like "pink hair" silently returns a
different show's character that the model also confuses him with.

Recovering him needs three signals together, because no one of them holds:

1. **Scope is a hard gate, not a score.**  A character appears in specific
   episodes.  Restricting to those removes every look-alike from other works
   outright, which is the failure that motivated this module: a tag query pulled
   Jujutsu Kaisen footage into a Kimetsu edit.  No similarity score is allowed
   to override scope.
2. **Similarity to the nearest seed, not to their centroid.**  A character is
   shot close, wide, from behind, mid-swing; averaging those into one vector
   produces something that matches none of them.  Max-to-any-seed keeps each
   confirmed look as its own anchor.
3. **Negative seeds.**  Scope cannot separate a character from his co-stars —
   Rengoku is in exactly the same episodes.  Requiring the shot to sit closer to
   our seeds than to other *named* characters' shots is what does that, and the
   available embedding is scene-level (colour, composition) rather than an
   identity embedding, so without this margin the separation is far too weak:
   measured, positives median 0.846 against other-Kimetsu p95 0.784.

Thresholds are calibrated from measured separation and recorded in ``evidence``,
not guessed.  Deterministic throughout (AGENTS.md R6).
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

APPEARANCE_CATALOG_VERSION = "appearance-catalog-1.0.0"

#: Calibrated on the Akaza seed set: below this the shot is not the same look at
#: all, and admitting it only adds co-stars and background plates.
DEFAULT_MIN_SIMILARITY = 0.70
#: How much closer to our seeds than to any other named character the shot must
#: sit.  At 0.00 the selection still admitted Douma and a recap collage; every
#: observed false positive sat at margin <= +0.01.
DEFAULT_MIN_MARGIN = 0.02


class AppearanceMatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    shot_id: str
    similarity: float = Field(..., ge=-1, le=1)
    margin: float
    confidence: float = Field(..., ge=0, le=1)


class AppearanceCatalog(BaseModel):
    """A versioned, reproducible answer to "which shots are this character?"."""

    model_config = ConfigDict(extra="forbid")
    version: str = APPEARANCE_CATALOG_VERSION
    character_id: str
    canonical_name: str
    aliases: list[str] = Field(default_factory=list)
    scope_asset_patterns: list[str]
    seed_shot_ids: list[str]
    negative_character_ids: list[str]
    min_similarity: float
    min_margin: float
    matches: list[AppearanceMatch]
    evidence: dict


def _embeddings(conn: sqlite3.Connection, shot_ids: list[str]) -> np.ndarray:
    if not shot_ids:
        raise ValueError("需要至少一个 shot_id")
    placeholders = ",".join("?" for _ in shot_ids)
    vectors = [
        np.frombuffer(row["embedding"], dtype=np.float32)
        for row in conn.execute(
            f"SELECT embedding FROM shots "
            f"WHERE id IN ({placeholders}) AND embedding IS NOT NULL",
            shot_ids,
        )
    ]
    if not vectors:
        raise ValueError("给定 shot 均无 embedding")
    return np.stack(vectors)


def _scope_clause(patterns: list[str]) -> tuple[str, list[str]]:
    if not patterns:
        raise ValueError("scope_asset_patterns 不能为空——范围是硬门禁")
    return (
        "(" + " OR ".join("a.path LIKE ?" for _ in patterns) + ")",
        list(patterns),
    )


def build_appearance_catalog(
    conn: sqlite3.Connection,
    *,
    character_id: str,
    canonical_name: str,
    seed_shot_ids: list[str],
    scope_asset_patterns: list[str],
    negative_character_ids: list[str],
    aliases: list[str] | None = None,
    min_similarity: float = DEFAULT_MIN_SIMILARITY,
    min_margin: float = DEFAULT_MIN_MARGIN,
    negative_limit: int = 400,
) -> AppearanceCatalog:
    """Expand a handful of confirmed shots into the character's full shot set."""
    if not seed_shot_ids:
        raise ValueError("需要人工确认的种子镜头")
    if not negative_character_ids:
        raise ValueError(
            "需要负种子：同剧集的其他角色无法靠范围区分，只能靠相对距离"
        )
    positive = _embeddings(conn, seed_shot_ids)
    negative_ids = [
        row["id"]
        for row in conn.execute(
            "SELECT id FROM shots WHERE character IN "
            f"({','.join('?' for _ in negative_character_ids)}) "
            "AND embedding IS NOT NULL ORDER BY id LIMIT ?",
            [*negative_character_ids, negative_limit],
        )
    ]
    negative = _embeddings(conn, negative_ids)

    clause, params = _scope_clause(scope_asset_patterns)
    matches: list[AppearanceMatch] = []
    scanned = 0
    for row in conn.execute(
        "SELECT s.id, s.embedding FROM shots s JOIN assets a ON a.id=s.asset_id "
        f"WHERE {clause} AND s.embedding IS NOT NULL ORDER BY s.id",
        params,
    ):
        scanned += 1
        vector = np.frombuffer(row["embedding"], dtype=np.float32)
        similarity = float((positive @ vector).max())
        margin = similarity - float((negative @ vector).max())
        if similarity < min_similarity or margin < min_margin:
            continue
        matches.append(
            AppearanceMatch(
                shot_id=row["id"],
                similarity=round(similarity, 6),
                margin=round(margin, 6),
                # Margin, not raw similarity, is what actually separated the
                # character from his co-stars, so it is what the confidence
                # should track.
                confidence=round(min(1.0, max(0.0, 0.5 + margin * 4)), 6),
            )
        )
    return AppearanceCatalog(
        character_id=character_id,
        canonical_name=canonical_name,
        aliases=sorted(aliases or []),
        scope_asset_patterns=list(scope_asset_patterns),
        seed_shot_ids=sorted(seed_shot_ids),
        negative_character_ids=sorted(negative_character_ids),
        min_similarity=min_similarity,
        min_margin=min_margin,
        matches=matches,
        evidence={
            "shots_in_scope": scanned,
            "seed_count": int(positive.shape[0]),
            "negative_count": int(negative.shape[0]),
            "matched": len(matches),
            "method": "max_cosine_to_seed minus max_cosine_to_named_others",
        },
    )


def persist_appearance_catalog(
    conn: sqlite3.Connection, catalog: AppearanceCatalog
) -> int:
    """Write the catalog and stamp ``shots.character`` for every match."""
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO characters"
            "(id,canonical_name,aliases_json,reference_images_json,created_at) "
            "VALUES (?,?,?,?,?)",
            (
                catalog.character_id,
                catalog.canonical_name,
                json.dumps(catalog.aliases, ensure_ascii=False),
                json.dumps(catalog.seed_shot_ids, ensure_ascii=False),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.executemany(
            "UPDATE shots SET character=?, character_confidence=? WHERE id=?",
            [
                (catalog.character_id, match.confidence, match.shot_id)
                for match in catalog.matches
            ],
        )
    return len(catalog.matches)


__all__ = [
    "APPEARANCE_CATALOG_VERSION",
    "DEFAULT_MIN_MARGIN",
    "DEFAULT_MIN_SIMILARITY",
    "AppearanceCatalog",
    "AppearanceMatch",
    "build_appearance_catalog",
    "persist_appearance_catalog",
]
