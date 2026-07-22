"""Candidate screening backed by SQLite source records."""
from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime
from pathlib import Path

from . import db


def _subtitle_streams(path: Path) -> int:
    try:
        raw = subprocess.check_output(
            ["ffprobe", "-v", "error", "-select_streams", "s", "-show_entries", "stream=index", "-of", "json", str(path)],
            text=True,
            stderr=subprocess.DEVNULL,
        )
        return len(json.loads(raw).get("streams", []))
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError):
        return 0


def _family(path: str) -> str:
    stem = Path(path).stem.lower()
    stem = re.sub(r"(?:4k|1080p|2160p|60fps|120fps|23\.976|48fps|high|clean|no[-_ ]?credit)", "", stem)
    return re.sub(r"[_-]+", "_", stem).strip("_ ")


def list_candidates(*, min_height: int = 1080, limit: int = 100) -> list[dict]:
    conn = db.connect()
    rows = conn.execute(
        """
        SELECT a.*, sr.source_url, sr.status AS source_status, sr.commercial_allowed
        FROM assets a
        LEFT JOIN source_records sr ON sr.asset_id=a.id
        ORDER BY a.created_at DESC, a.id
        """
    ).fetchall()
    exact: dict[str, list[str]] = {}
    families: dict[str, list[str]] = {}
    for row in rows:
        exact.setdefault(row["sha256"], []).append(row["id"])
        families.setdefault(_family(row["path"]), []).append(row["id"])
    out = []
    for row in rows:
        path = Path(row["path"])
        short_edge = min(row["width"] or 0, row["height"] or 0)
        subtitle_tracks = _subtitle_streams(path) if path.exists() else 0
        reasons: list[str] = []
        if short_edge < min_height:
            reasons.append(f"低于 {min_height}p")
        if subtitle_tracks:
            reasons.append(f"存在 {subtitle_tracks} 条软字幕轨")
        if len(exact.get(row["sha256"], [])) > 1:
            reasons.append("文件重复")
        if len(families.get(_family(row["path"]), [])) > 1:
            reasons.append("同源/版本重复风险")
        source_status = row["source_status"] or "review"
        has_rights = bool(row["source_url"])
        if not has_rights:
            reasons.append("缺少来源记录")
        if source_status == "blocked":
            reasons.append("权利状态 blocked")
        hard_reject = short_edge < min_height or len(exact.get(row["sha256"], [])) > 1 or source_status == "blocked"
        status = "reject" if hard_reject else ("review" if subtitle_tracks or len(families.get(_family(row["path"]), [])) > 1 or source_status != "approved" else "ready")
        score = (
            (40 if short_edge >= 2160 else 30 if short_edge >= 1080 else 0)
            + (20 if row["fps"] and row["fps"] >= 50 else 10)
            + (20 if not subtitle_tracks else 0)
            + (15 if has_rights else 0)
            + (5 if source_status == "approved" else 0)
        )
        out.append(
            {
                "id": row["id"],
                "path": str(path),
                "filename": path.name,
                "width": row["width"],
                "height": row["height"],
                "short_edge": short_edge,
                "fps": row["fps"],
                "duration": row["duration"],
                "codec": row["codec"],
                "subtitle_tracks": subtitle_tracks,
                "rights_recorded": has_rights,
                "family": _family(row["path"]),
                "status": status,
                "score": score,
                "reasons": reasons,
                "source_status": source_status,
                "commercial_allowed": row["commercial_allowed"],
            }
        )
    conn.close()
    rank = {"ready": 0, "review": 1, "reject": 2}
    out.sort(key=lambda x: (rank[x["status"]], -x["score"], x["filename"]))
    return out[:limit]


def export_json(out: str | Path, *, min_height: int = 1080, limit: int = 100) -> dict:
    candidates = list_candidates(min_height=min_height, limit=limit)
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "policy": {"min_source_height": min_height, "reject_burned_subtitles_by_default": True},
        "candidates": candidates,
    }
    path = Path(out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return {
        "output": str(path),
        "count": len(candidates),
        "ready": sum(c["status"] == "ready" for c in candidates),
        "review": sum(c["status"] == "review" for c in candidates),
        "reject": sum(c["status"] == "reject" for c in candidates),
    }
