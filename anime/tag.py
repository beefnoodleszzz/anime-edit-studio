"""WD-Tagger 动漫打标(Danbooru v3):角色名 + 属性标签 → 写入 DB 并同步 FTS。

比通用 VLM 更适合动漫:能认出角色名(gojou_satoru)、给二次元属性(white_hair/
sunglasses/close-up/adjusting_eyewear…),且轻(~400MB)、快(~0.35s/图)。
character 存 shots.character;角色名+属性合并存 shots.tags,自动进 FTS 全文检索。
"""
from __future__ import annotations

from . import db

_tagger = None


def _get_tagger():
    global _tagger
    if _tagger is None:
        from wdtagger import Tagger
        _tagger = Tagger()
    return _tagger


def tag_asset(asset_id: str, force: bool = False) -> int:
    conn = db.connect()
    asset = db.asset_by_id(conn, asset_id)
    if not asset:
        conn.close()
        raise ValueError(f"未找到素材: {asset_id}")
    if not force and conn.execute(
            "SELECT COUNT(*) c FROM shots WHERE asset_id=? AND character IS NOT NULL",
            (asset["id"],)).fetchone()["c"]:
        conn.close()
        return 0
    rows = conn.execute(
        "SELECT id, keyframe FROM shots WHERE asset_id=? AND keyframe IS NOT NULL", (asset["id"],)
    ).fetchall()
    tagger = _get_tagger()

    n = 0
    for r in rows:
        res = tagger.tag(r["keyframe"])
        chars = list(getattr(res, "character_tags", None) or ())
        gens = list(getattr(res, "general_tags", None) or ())
        character = chars[0] if chars else None
        tags = ",".join(chars + gens)                    # 角色名+属性一起进 FTS
        db.update_shot_analysis(conn, r["id"], {"character": character, "tags": tags})
        n += 1
    conn.close()
    return n
