"""素材候选筛选：质量门槛、字幕轨、重复风险与来源记录。"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from . import config, db


def _subtitle_streams(path: Path) -> int:
    try:
        raw = subprocess.check_output(["ffprobe", "-v", "error", "-select_streams", "s", "-show_entries", "stream=index", "-of", "json", str(path)], text=True, stderr=subprocess.DEVNULL)
        return len(json.loads(raw).get("streams", []))
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError):
        return 0


def _family(path: str) -> str:
    stem = Path(path).stem.lower()
    stem = re.sub(r"(?:4k|1080p|2160p|60fps|120fps|23\.976|48fps|high|clean|no[-_ ]?credit)", "", stem)
    return re.sub(r"[_-]+", "_", stem).strip("_ ")


def list_candidates(*, min_height: int = 1080, limit: int = 100) -> list[dict]:
    conn = db.connect()
    rows = conn.execute("SELECT * FROM assets ORDER BY created_at DESC, id").fetchall()
    conn.close()
    rights_path = config.LIBRARY / "rights.json"
    rights_map = json.loads(rights_path.read_text()) if rights_path.exists() else {}
    overrides_path = config.LIBRARY / "candidate_overrides.json"
    overrides = json.loads(overrides_path.read_text()) if overrides_path.exists() else {}
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
        if short_edge < min_height: reasons.append(f"低于 {min_height}p")
        if subtitle_tracks: reasons.append(f"存在 {subtitle_tracks} 条软字幕轨")
        if len(exact.get(row["sha256"], [])) > 1: reasons.append("文件重复")
        if len(families.get(_family(row["path"]), [])) > 1: reasons.append("同源/版本重复风险")
        has_rights = row["id"] in rights_map and bool(rights_map[row["id"]].get("source"))
        if not has_rights: reasons.append("缺少来源记录")
        if row["id"] in overrides: reasons.append(overrides[row["id"]])
        hard_reject = short_edge < min_height or len(exact.get(row["sha256"], [])) > 1 or row["id"] in overrides
        status = "reject" if hard_reject else ("review" if subtitle_tracks or len(families.get(_family(row["path"]), [])) > 1 or not has_rights else "ready")
        score = (40 if short_edge >= 2160 else 30 if short_edge >= 1080 else 0) + (20 if row["fps"] and row["fps"] >= 50 else 10) + (20 if not subtitle_tracks else 0) + (15 if has_rights else 0) + (5 if status == "ready" else 0)
        out.append({"id": row["id"], "path": str(path), "filename": path.name, "width": row["width"], "height": row["height"], "short_edge": short_edge, "fps": row["fps"], "duration": row["duration"], "codec": row["codec"], "subtitle_tracks": subtitle_tracks, "rights_recorded": has_rights, "family": _family(row["path"]), "status": status, "score": score, "reasons": reasons, "thumbnail": f"/media/candidates/{row['id']}.jpg", "video": f"/media/candidates/videos/{row['id']}.mp4"})
    rank = {"ready": 0, "review": 1, "reject": 2}
    out.sort(key=lambda x: (rank[x["status"]], -x["score"], x["filename"]))
    return out[:limit]


def export_json(out: str | Path, *, min_height: int = 1080, limit: int = 100) -> dict:
    candidates = list_candidates(min_height=min_height, limit=limit)
    payload = {"generated_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"), "policy": {"min_source_height": min_height, "reject_burned_subtitles_by_default": True}, "candidates": candidates}
    path = Path(out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return {"output": str(path), "count": len(candidates), "ready": sum(c["status"] == "ready" for c in candidates), "review": sum(c["status"] == "review" for c in candidates), "reject": sum(c["status"] == "reject" for c in candidates)}
