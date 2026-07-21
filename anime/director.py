"""AI 导演编排(M6):slot 分类 + 去重 + 能量驱动的情绪弧装配。

把"均匀每拍切"升级为"开场钩子 → 铺垫 → 高潮 → 收束":
- 切点疏密由 BGM 逐拍能量驱动(高能每拍切,低能长握);
- 开场用 hero 长镜抓人,结尾用长镜收束;
- 各段从对应 slot 池选片,CLIP 近重复去重;
- 特效/运镜/速度曲线按段落调性分配。
"""
from __future__ import annotations

import numpy as np

from . import db, editspec

SLOT_PROMPTS = {
    "opening": "a striking dramatic hero character close-up, confident, eye-catching",
    "climax": "intense action, energy blast, explosion, fast dynamic motion, power",
    "build": "a character medium shot, tension building, movement",
    "ending": "a lonely melancholic silhouette, quiet, character from behind, somber",
}


def classify_slots(asset_id: str, force: bool = False) -> int:
    """z-score 相对打分:把镜头分到"相对最突出"的 slot,避免绝对分数使某类通吃;
    再叠加动作(→climax)、暗调(→ending)信号,让四个 slot 都能填满。"""
    from . import embed

    conn = db.connect()
    asset = db.asset_by_id(conn, asset_id)
    if not asset:
        conn.close()
        raise ValueError(f"未找到素材: {asset_id}")
    if not force and conn.execute(
            "SELECT COUNT(*) c FROM shots WHERE asset_id=? AND slot IS NOT NULL",
            (asset["id"],)).fetchone()["c"]:
        conn.close()
        return 0
    rows = conn.execute(
        "SELECT id, embedding, motion_mag, brightness, tags FROM shots WHERE asset_id=? AND embedding IS NOT NULL",
        (asset["id"],),
    ).fetchall()
    if not rows:
        conn.close()
        return 0

    slots = list(SLOT_PROMPTS)
    slot_vecs = np.stack([embed.embed_text(SLOT_PROMPTS[s]) for s in slots])           # [4, d]
    embs = np.stack([np.frombuffer(r["embedding"], dtype="float32") for r in rows])    # [n, d]
    scores = embs @ slot_vecs.T                                                        # [n, 4]
    z = (scores - scores.mean(0)) / (scores.std(0) + 1e-6)                             # 逐 slot 归一
    mm = np.array([min((r["motion_mag"] or 0) / 5, 1.0) for r in rows])
    br = np.array([r["brightness"] if r["brightness"] is not None else 0.5 for r in rows])
    z[:, slots.index("climax")] += mm * 1.0                                            # 高动作 → 高潮
    z[:, slots.index("ending")] += np.clip(0.45 - br, 0, None) * 2.0                   # 暗调 → 收束
    # WD 构图标签信号:特写/肖像 → 开场 hero;大远景 → 铺垫
    for i, r in enumerate(rows):
        tg = r["tags"] or ""
        if "close-up" in tg or "portrait" in tg:
            z[i, slots.index("opening")] += 0.6
        if "full_body" in tg or "scenery" in tg or "wide" in tg:
            z[i, slots.index("build")] += 0.4

    for r, i in zip(rows, z.argmax(1)):
        conn.execute("UPDATE shots SET slot=? WHERE id=?", (slots[i], r["id"]))
    conn.commit()
    conn.close()
    return len(rows)


def _pick(by_slot: dict, want: str, used: list, cursors: dict,
          prefer_asset: str | None = None, avoid_asset: str | None = None,
          used_ids: set[str] | None = None) -> dict:
    slot_order = [want, "build", "climax", "opening", "ending"]

    def scan(slot: str, *, constrained: bool, avoid: bool,
             unique: bool = True) -> dict | None:
        lst = by_slot.get(slot) or []
        start = cursors.get(slot, 0)
        for offset in range(len(lst)):
            pos = (start + offset) % len(lst)
            c = lst[pos]
            if unique and used_ids is not None and c["shot_id"] in used_ids:
                continue
            if constrained and c["asset_id"] != prefer_asset:
                continue
            # 多番混剪:优先避开与上一镜同源(同番/同角色),保证相邻变化;单素材时此约束无解会自动放行
            if avoid and c["asset_id"] == avoid_asset:
                continue
            emb = c.get("embedding")
            # 指定来源本身就是有价值的视觉变化；不要被 CLIP 的同角色相似度挡掉。
            if constrained or emb is None or all(float(emb @ u) < 0.9 for u in used):
                cursors[slot] = start + offset + 1
                return c
        return None

    # 先跨全部段位找指定来源，不能因当前 slot 有旧素材就提前回退。
    if prefer_asset:
        for slot in slot_order:
            c = scan(slot, constrained=True, avoid=False)
            if c:
                return c
    # 多番混剪:先只在「非上一镜来源」里找,保证相邻变化;找不到再放开走原逻辑(单素材必落这)
    if avoid_asset:
        for slot in slot_order:
            c = scan(slot, constrained=False, avoid=True)
            if c:
                return c
    for slot in slot_order:
        c = scan(slot, constrained=False, avoid=False)
        if c:
            return c
    # 全片唯一镜头耗尽后才允许复用；短片素材够时不会走到这里。
    if used_ids is not None:
        if prefer_asset:
            for slot in slot_order:
                c = scan(slot, constrained=True, avoid=False, unique=False)
                if c:
                    return c
        if avoid_asset:
            for slot in slot_order:
                c = scan(slot, constrained=False, avoid=True, unique=False)
                if c:
                    return c
        for slot in slot_order:
            c = scan(slot, constrained=False, avoid=False, unique=False)
            if c:
                return c
    for lst in by_slot.values():
        if lst:
            return lst[0]
    raise ValueError("无候选")


def _make_shot(c, sources, start_f, dur, k, section, is_down, energy, fps, speed=1.0) -> editspec.Shot:
    fast = (c.get("motion_mag") or 0) > 0.8
    # 长镜可能超出片段末尾 → 播过片尾会黑;把源起点回退,保证足够源可播
    needed = dur / fps + 0.1
    src_in = min(c["start_sec"], max(0.0, c.get("clip_dur", 1e9) - needed))
    eff, cam, amt, tr, ti, ramp = [], "pushIn", 0.08, "none", 0.0, "none"

    if section == "opening":                       # 钩子:hero 长镜,稳,微辉光
        cam, amt = "pushIn", 0.12
        eff = [editspec.Effect(type="glow", intensity=0.25)]
        tr, ti = ("flash", 0.4) if k == 0 else ("none", 0.0)
    elif section == "ending":                      # 收束:拉远长镜,暗角,慢
        cam, amt = "pushOut", 0.12
        eff = [editspec.Effect(type="vignette", intensity=0.35)]
        ramp = "smooth"
    elif section == "climax":                      # 高潮:密集冲击
        eff = [editspec.Effect(type="rgbSplit", intensity=0.5 if fast else 0.3)]
        if is_down:
            tr, ti = "flash", 0.6
        elif fast:
            tr, ti = ("whipLeft" if k % 2 else "whipRight"), 0.6
            eff.append(editspec.Effect(type="shake", intensity=0.4))
        ramp = "decel" if fast else "none"
    else:                                          # build:铺垫
        eff = [editspec.Effect(type="rgbSplit", intensity=0.22)]
        cam, amt = ("panLeft" if energy > 0.4 else "pushIn"), 0.08
        tr, ti = ("zoomBlur", 0.45) if k % 3 == 0 else ("none", 0.0)

    if speed < 0.99:                               # 真慢镜(高潮峰 hero 落地)必配 decel + RIFE
        ramp = "decel"
    return editspec.Shot(
        id=f"{c['shot_id']}@{k}", src=sources[c["asset_id"]],
        source_in_sec=round(src_in, 3), start_frame=start_f, duration_in_frames=dur,
        speed=speed, effects=eff, camera_move=cam, camera_amount=amt,
        transition=tr, transition_intensity=ti, ramp=ramp,
        reframe_x=c.get("reframe_x", 0.0), fill_mode=c.get("fill_mode", "crop"),
    )


def _make_showcase_shot(c, sources, start_f, dur, k, is_down, energy, fps) -> editspec.Shot:
    """Showcase 混剪镜头:恒定电影调 + 每刀落拍冲击转场(下拍闪、否则甩镜/变焦punch 交替)。"""
    fast = (c.get("motion_mag") or 0) > 0.8
    needed = dur / fps + 0.1
    src_in = min(c["start_sec"], max(0.0, c.get("clip_dur", 1e9) - needed))
    eff = [editspec.Effect(type="rgbSplit", intensity=0.4 if fast else 0.25)]
    # 每刀必上转场:下拍→闪切;快镜→甩镜(带抖);其余→冲击变焦
    if is_down:
        tr, ti = "flash", 0.6
    elif fast:
        tr, ti = ("whipLeft" if k % 2 else "whipRight"), 0.6
        eff.append(editspec.Effect(type="shake", intensity=0.35))
    else:
        tr, ti = "zoomPunch", 0.55
    # 运镜:短镜推近、长镜交替横摇,给静镜一点生命力
    cam, amt = ("pushIn", 0.10) if dur < fps * 0.6 else \
               (("panLeft" if k % 2 else "panRight"), 0.07)
    return editspec.Shot(
        id=f"{c['shot_id']}@{k}", src=sources[c["asset_id"]],
        source_in_sec=round(src_in, 3), start_frame=start_f, duration_in_frames=dur,
        speed=1.0, effects=eff, camera_move=cam, camera_amount=amt,
        transition=tr, transition_intensity=ti, ramp="none",
        reframe_x=c.get("reframe_x", 0.0), fill_mode=c.get("fill_mode", "crop"),
    )


def _assemble_showcase(project_id, beatmap, beats, energy, downbeats,
                       by_slot, pool, sources, fps, width, height, canvas) -> dict:
    """混剪/showcase 体裁:开场闪切爆发钩子 + 恒定密度能量锁拍 + 每刀冲击转场。

    复用 arc 的能量驱动切点引擎与 _pick 选片去重,只换掉「开场/结尾长握」为
    「开场 strobe 爆发 + 全程能量密度」,并给每刀配落拍转场。END 卡走 endcard 后处理。
    """
    n = len(beats)
    used: list = []
    used_ids: set[str] = set()
    cursors: dict = {}
    shots = []
    t0 = beats[0]

    # 1) 开场闪切爆发:把第一个拍间隔切成 ~6 个 3–5 帧微镜(不同镜头),flash 转场当钩子
    multi = len({p["asset_id"] for p in pool}) > 1        # 多番池才启用相邻去源
    prev_asset = None
    beat_len = max(round((beats[1] - beats[0]) * fps), 6)
    n_strobe = min(6, beat_len // 3)
    for j in range(n_strobe):
        c = _pick(by_slot, "climax", used, cursors,
                  avoid_asset=prev_asset if multi else None, used_ids=used_ids)
        prev_asset = c["asset_id"]
        used_ids.add(c["shot_id"])
        if c.get("embedding") is not None:
            used.append(c["embedding"]); used[:] = used[-6:]
        sf = round(j * beat_len / n_strobe)
        ef = round((j + 1) * beat_len / n_strobe)
        s = _make_showcase_shot(c, sources, sf, max(ef - sf, 1), j, False, 1.0, fps)
        s.transition, s.transition_intensity = "flash", 0.7
        s.camera_move, s.camera_amount = "pushIn", 0.14
        shots.append(s)

    # 2) 正片:能量驱动的变长握拍(高能每拍、低能 2–3 拍),但不做开场/结尾长握
    cuts, i = [], 1
    while i < n - 1:
        cuts.append(i)
        e = energy[i]
        i += 1 if e > 0.55 else 2 if e > 0.3 else 3

    k0 = len(shots)
    for k, bi in enumerate(cuts):
        c = _pick(by_slot, "climax" if energy[bi] > 0.5 else "build", used, cursors,
                  avoid_asset=prev_asset if multi else None, used_ids=used_ids)
        prev_asset = c["asset_id"]
        used_ids.add(c["shot_id"])
        if c.get("embedding") is not None:
            used.append(c["embedding"]); used[:] = used[-6:]
        end_bi = cuts[k + 1] if k + 1 < len(cuts) else min(bi + 3, n - 1)
        start_f = round((beats[bi] - t0) * fps)
        dur = max(round((beats[end_bi] - beats[bi]) * fps), 1)
        shots.append(_make_showcase_shot(c, sources, start_f, dur, k0 + k,
                                         beats[bi] in downbeats, energy[bi], fps))

    total = shots[-1].start_frame + shots[-1].duration_in_frames
    if total / fps < 20:
        raise ValueError("交付剪辑不得短于 20 秒；请扩大音频窗口")
    spec = editspec.EditSpec(
        id=project_id, fps=fps, width=width, height=height,
        duration_in_frames=total, shots=shots,
        audio=[editspec.AudioLayer(id="bgm", src=beatmap["audio"],
                                   trim_start_frames=round(t0 * fps))],
    )
    path = editspec.save(spec, name="arc")
    return {"editspec": str(path), "shots": len(shots), "mode": "showcase",
            "canvas": canvas, "width": width, "height": height,
            "strobe": n_strobe, "duration_s": round(total / fps, 1),
            "audio_start_s": round(t0, 3)}


def direct(project_id: str, beatmap: dict, query: str, *, asset_id: str | None = None,
           must_tag: str | None = None, start_s: float = 0.0,
           duration_s: float | None = None, fps: int = 60, mode: str = "arc",
           fill: str = "crop", canvas: str = "4x5", width: int | None = None,
           height: int | None = None) -> dict:
    from . import search

    if duration_s is not None and duration_s < 20:
        raise ValueError("交付剪辑不得短于 20 秒；--duration 必须至少为 20")
    if asset_id and len({part.strip() for part in asset_id.split(",") if part.strip()}) < 2:
        raise ValueError("交付剪辑必须使用至少两条独立素材源；--asset 请传入至少两个 asset_id")

    all_beats = beatmap["beats"]
    all_energy = beatmap.get("beat_energy") or [0.5] * len(all_beats)
    stop_s = start_s + duration_s if duration_s is not None else float("inf")
    selected = [(b, e) for b, e in zip(all_beats, all_energy) if start_s <= b < stop_s]
    beats = [b for b, _ in selected]
    energy = [e for _, e in selected]
    downbeats = {b for b in beatmap["downbeats"] if start_s <= b < stop_s}
    n = len(beats)
    if n < 4:
        raise ValueError("所选音频窗口内节拍太少")

    cands = search.search(query, asset_id=asset_id, limit=120)
    if must_tag:
        tag = must_tag.casefold()
        cands = [c for c in cands if tag in (c.get("tags") or "").casefold()]
    if not cands:
        raise ValueError("候选池为空")
    if len({c["asset_id"] for c in cands}) < 2:
        raise ValueError("交付剪辑必须使用至少两条独立素材源")
    conn = db.connect()
    durs = {aid: (conn.execute("SELECT duration FROM assets WHERE id=?", (aid,)).fetchone()["duration"])
            for aid in {c["asset_id"] for c in cands}}
    out_w, out_h, orientation = editspec.choose_canvas(
        cands, canvas=canvas, width=width, height=height)
    target_ar = out_w / out_h
    asset_dims = {
        aid: conn.execute("SELECT width, height FROM assets WHERE id=?", (aid,)).fetchone()
        for aid in {c["asset_id"] for c in cands}
    }
    pool = []
    for c in cands:
        row = conn.execute("SELECT embedding, slot, min_brightness, fill_mode FROM shots WHERE id=?",
                           (c["shot_id"],)).fetchone()
        # 近黑瞬镜头不入池(min_brightness 抓镜头内暗瞬,避免黑场/死镜);无值则回退中值
        mb = row["min_brightness"] if (row and row["min_brightness"] is not None) else c.get("brightness")
        if mb is None:
            mb = 0.5
        if mb < 0.10:   # signalstats 限制范围黑≈0.063;<0.10 抓近黑瞬(注:不能用 or,0.0 falsy)
            continue
        emb = (np.frombuffer(row["embedding"], dtype="float32")
               if row and row["embedding"] is not None else None)
        dims = asset_dims.get(c["asset_id"])
        src_ar = ((dims["width"] or 1) / (dims["height"] or 1)) if dims else target_ar
        # Auto means preserve composition: native/near-native aspect fills the
        # canvas; mismatched material is contained unless crop was explicitly requested.
        fm = "crop" if abs(src_ar / target_ar - 1.0) <= 0.08 else "fit_blur"
        pool.append({**c, "embedding": emb, "slot": (row["slot"] if row else None) or "build",
                     "clip_dur": durs.get(c["asset_id"], 1e9),
                     "fill_mode": fill if fill != "auto" else fm})   # --fill 覆盖 DB 自动判定
    conn.close()
    sources = editspec._resolve_sources(cands)

    by_slot: dict = {}
    for p in pool:
        by_slot.setdefault(p["slot"], []).append(p)
    # 各段先拿最贴合的镜头:高潮=最烈,收束=最静但可见(避开近黑填充),开场=最清晰
    by_slot.get("climax", []).sort(key=lambda c: -(c.get("motion_mag") or 0))
    by_slot.get("ending", []).sort(
        key=lambda c: ((c.get("brightness") or 0) < 0.06, c.get("motion_mag") or 0))
    by_slot.get("opening", []).sort(key=lambda c: -(c.get("sharpness") or 0))

    if mode == "showcase":
        return _assemble_showcase(project_id, beatmap, beats, energy, downbeats,
                                  by_slot, pool, sources, fps, out_w, out_h, orientation)

    asset_cycle = list(dict.fromkeys(c["asset_id"] for c in pool))
    diversity_asset = min(asset_cycle, key=lambda asset: sum(c["asset_id"] == asset for c in pool))

    open_end = max(1, int(n * 0.08))
    end_start = int(n * 0.86)

    # 能量驱动的切点(变长握拍)
    cuts, i = [], 0
    while i < n - 1:
        cuts.append(i)
        if i < open_end:
            i += 4                                  # 开场长镜
        elif i >= end_start:
            i += 6                                  # 结尾长镜
        else:
            e = energy[i]
            i += 1 if e > 0.6 else 2 if e > 0.32 else 3

    # 能量峰所在切点 → hero 真慢镜落地(speed 0.5,render 自动 RIFE 平滑)
    peak_bi = int(np.argmax(energy))
    peak_cut = min(range(len(cuts)), key=lambda k: abs(cuts[k] - peak_bi))

    used: list = []
    used_ids: set[str] = set()
    cursors: dict = {}
    shots = []
    t0 = beats[cuts[0]]
    structure = {"opening": 0, "build": 0, "climax": 0, "ending": 0}
    for k, bi in enumerate(cuts):
        if bi < open_end:
            section = "opening"
        elif bi >= end_start:
            section = "ending"
        else:
            section = "climax" if energy[bi] > 0.55 else "build"
        structure[section] += 1
        # 高潮每三切优先换一次来源，避免排序一直吸附同一素材。
        prefer_asset = None
        if len(asset_cycle) > 1 and section == "climax" and k % 3 == 0:
            prefer_asset = diversity_asset
        c = _pick(by_slot, section, used, cursors, prefer_asset=prefer_asset,
                  used_ids=used_ids)
        used_ids.add(c["shot_id"])
        if c.get("embedding") is not None:
            used.append(c["embedding"])
            used[:] = used[-6:]
        end_bi = cuts[k + 1] if k + 1 < len(cuts) else min(bi + 6, n - 1)
        start_f = round((beats[bi] - t0) * fps)
        dur = max(round((beats[end_bi] - beats[bi]) * fps), 1)
        speed = 0.5 if (k == peak_cut and section == "climax") else 1.0
        shots.append(_make_shot(c, sources, start_f, dur, k,
                                section, beats[bi] in downbeats, energy[bi], fps, speed))

    total = shots[-1].start_frame + shots[-1].duration_in_frames
    if total / fps < 20:
        raise ValueError("交付剪辑不得短于 20 秒；请扩大音频窗口")
    spec = editspec.EditSpec(
        id=project_id, fps=fps, width=out_w, height=out_h,
        duration_in_frames=total, shots=shots,
        audio=[editspec.AudioLayer(id="bgm", src=beatmap["audio"],
                                   trim_start_frames=round(t0 * fps))],
    )
    path = editspec.save(spec, name="arc")
    return {"editspec": str(path), "shots": len(shots), "structure": structure,
            "canvas": orientation, "width": out_w, "height": out_h,
            "duration_s": round(total / fps, 1), "audio_start_s": round(t0, 3)}
