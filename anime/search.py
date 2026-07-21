"""镜头检索:CLIP 语义向量(有 embedding 时)+ FTS 全文 + 质量 → 综合排序。"""
from __future__ import annotations

import json
from functools import lru_cache

import numpy as np

from . import config, db


@lru_cache
def _aliases() -> dict:
    p = config.ROOT / "kit" / "aliases.json"
    if not p.exists():
        return {}
    return {k: v for k, v in json.loads(p.read_text()).items() if not k.startswith("_")}


def _expand_query(query: str) -> str:
    """中文/常用词 → 追加罗马音标签+英文描述(兼顾 FTS 与英文 CLIP)。"""
    extra = [v for k, v in _aliases().items() if k in query]
    return (query + " " + " ".join(extra)).strip() if extra else query


def _semantic_scores(query: str, shots: list) -> dict:
    """有 CLIP 向量时,返回 shot_id → 语义相似度(0..1)。"""
    have = [s for s in shots if s["embedding"]]
    if not have:
        return {}
    from . import embed
    q = embed.embed_text(query)
    mat = np.frombuffer(b"".join(s["embedding"] for s in have), dtype="float32").reshape(len(have), -1)
    sims = mat @ q  # 均已归一化 → 点积即余弦
    return {s["id"]: float(v) for s, v in zip(have, sims)}


def search(query: str = "", *, asset_id: str | None = None, slot: str | None = None,
           min_sharpness: float = 0.0, min_motion: float = 0.0,
           min_source_height: int = 1080, limit: int = 20) -> list[dict]:
    """Return editorial candidates from sources meeting the delivery floor.

    ``min_source_height`` deliberately refers to the shorter source edge.  A
    1920x1080 landscape file and a 1080x1920 portrait file therefore both
    qualify, while a 1920x816 cinema crop does not silently masquerade as
    1080p.  Pass ``0`` only for catalogue/research work; delivery routes keep
    the 1080p default.
    """
    conn = db.connect()
    if asset_id:
        requested = [part.strip() for part in asset_id.split(",") if part.strip()]
        assets = [db.asset_by_id(conn, part) for part in requested]
        if not assets or any(asset is None for asset in assets):
            conn.close()
            raise ValueError(f"未找到素材: {asset_id}")
        ids = [asset["id"] for asset in assets if asset]
        marks = ",".join("?" for _ in ids)
        shots = conn.execute(f"""SELECT s.* FROM shots s
            JOIN assets a ON a.id=s.asset_id
            WHERE s.asset_id IN ({marks})
              AND MIN(COALESCE(a.width, 0), COALESCE(a.height, 0)) >= ?""",
            [*ids, min_source_height]).fetchall()
    else:
        shots = conn.execute("""SELECT s.* FROM shots s
            JOIN assets a ON a.id=s.asset_id
            WHERE MIN(COALESCE(a.width, 0), COALESCE(a.height, 0)) >= ?""",
            (min_source_height,)).fetchall()

    order: dict = {}
    semantic: dict = {}
    if query.strip():
        import re
        q = _expand_query(query)                       # 中文/别名 → 追加罗马音+英文
        semantic = _semantic_scores(q, shots)          # CLIP 语义(整句)
        # FTS 标签匹配(WD-Tagger 角色名/属性)——词元 OR 连接,始终融合进排序
        try:
            toks = [t for t in re.findall(r"\w+", q) if len(t) > 1]
            fts = conn.execute(
                "SELECT shot_id, rank FROM shots_fts WHERE shots_fts MATCH ? ORDER BY rank LIMIT 300",
                (" OR ".join(toks),),
            ).fetchall() if toks else []
            order = {r["shot_id"]: -r["rank"] for r in fts}
        except Exception:
            order = {}                                 # FTS 语法错误则忽略
        if not semantic and order:                     # 无向量时收敛到 FTS 命中集
            ids = set(order)
            shots = [s for s in shots if s["id"] in ids]

    scored = []
    for s in shots:
        if slot and s["slot"] != slot:
            continue
        if (s["sharpness"] or 0) < min_sharpness:
            continue
        if (s["motion_mag"] or 0) < min_motion:
            continue
        score = (
            semantic.get(s["id"], 0.0) * 5.0        # 语义匹配主导
            + order.get(s["id"], 0.0) * 2.0
            + min((s["sharpness"] or 0) / 500, 1.0)
            + min((s["motion_mag"] or 0) / 5, 1.0)
            + (s["picked"] or 0) * 0.3
        )
        scored.append((score, s))
    scored.sort(key=lambda x: x[0], reverse=True)
    conn.close()

    return [{
        "shot_id": s["id"], "asset_id": s["asset_id"],
        "start_sec": s["start_sec"], "end_sec": s["end_sec"],
        "keyframe": s["keyframe"], "motion_dir": s["motion_dir"],
        "motion_mag": s["motion_mag"], "sharpness": s["sharpness"],
        "brightness": s["brightness"], "dialogue": s["dialogue"],
        "tags": s["tags"], "reframe_x": s["reframe_x"] or 0.0,
        "score": round(score, 3),
    } for score, s in scored[:limit]]
