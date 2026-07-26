"""Phase 3 analysis orchestration; individual analyzers remain independently testable."""
from __future__ import annotations

from pathlib import Path

from studio.asset_intelligence.audio import analyze_pending_audio
from studio.asset_intelligence.embeddings import OpenClipBackend, encode_pending
from studio.asset_intelligence.motion import analyze_pending_motion
from studio.asset_intelligence.visual import (
    WDTaggerBackend,
    analyze_cutability,
    analyze_pending,
    hydrate_legacy_tags,
    tag_pending,
)
from studio.core.database import DEFAULT_V2_DB, connect

REPO = Path(__file__).resolve().parents[2]
DEFAULT_CACHE = REPO / "library" / "cache"


def analyze_assets(
    *,
    asset_id: str | None = None,
    database: Path = DEFAULT_V2_DB,
    cache_root: Path = DEFAULT_CACHE,
    include_models: bool = True,
) -> dict:
    conn = connect(database)
    report: dict = {}
    try:
        report["legacy_tags"] = hydrate_legacy_tags(conn) if asset_id is None else 0
        report["motion"] = analyze_pending_motion(
            conn, cache_root=cache_root, asset_id=asset_id
        )
        if include_models:
            report["embeddings"] = encode_pending(
                conn,
                cache_root=cache_root,
                backend=OpenClipBackend(
                    REPO / "kit" / "models" / "sa_0_4_vit_b_32_linear.pth"
                ),
                asset_id=asset_id,
            )
            report["tags"] = tag_pending(
                conn,
                cache_root=cache_root,
                backend=WDTaggerBackend(),
                asset_id=asset_id,
            )
        report["visual"] = analyze_pending(
            conn, cache_root=cache_root, asset_id=asset_id
        )
        report["cutability"] = analyze_cutability(
            conn, cache_root=cache_root, asset_id=asset_id
        )
        report["audio"] = analyze_pending_audio(
            conn, cache_root=cache_root, asset_id=asset_id
        )
        return report
    finally:
        conn.close()


__all__ = ["DEFAULT_CACHE", "analyze_assets"]
