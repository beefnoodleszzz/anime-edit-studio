"""主体感知装帧:算每镜主体横向质心(reframe_x)+ 判定装帧方式(fill_mode)。

复用已抽好的关键帧(小图,rembg 快)。核心问题:横屏素材(16:9)裁成竖屏(9:16)只保留
~32% 宽度,特写单人 crop 满屏最燃,但主体过宽/多人/宽景会被切坏——这类改用 fit_blur
(毛玻璃背景 + 完整画面居中),抖音横转竖标准做法。主体过小/检不到则默认 crop。
"""
from __future__ import annotations

import numpy as np

from . import config, db, matte


def _decide(arr: np.ndarray, src_ar: float, target_ar: float) -> tuple[float, str]:
    """返回 (reframe_x, fill_mode)。arr=主体灰度遮罩(H×W)。

    关键:不看主体多宽(居中的宽特写裁成竖屏最燃),而看「reframe 后的裁切窗口能保住
    多少主体质量」——保不住(主体质量散到两侧,如多人/横向大动作)才 fit_blur。
    """
    col = arr.sum(axis=0)
    total = col.sum()
    W = len(col)
    if total < arr.size * 0.02:                     # 主体太小/无主体(宽景) → 居中 crop
        return 0.0, "crop"
    idx = np.arange(W)
    cx = float((col * idx).sum() / total)
    rx = round((cx / W - 0.5) * 2, 3)               # -1..1(左负右正)
    if src_ar <= target_ar * 1.05:                  # 源已是竖屏/接近目标 → crop 几乎无损
        return rx, "crop"
    kept_w = target_ar / src_ar * W                 # crop 后可见宽度(源列单位)
    lo = max(0.0, min(cx - kept_w / 2, W - kept_w)) # 窗口对准主体质心,贴边夹紧(同渲染 objectPosition)
    inside = col[int(lo):int(lo + kept_w)].sum() / total
    # 优先竖屏满屏(更燃);仅当 reframe 对准主体后仍保不住半数主体质量(主体铺到两侧:
    # 多人/横向大动作,crop 必切坏)才 fit_blur。丢胳膊腿/衣摆仍算 crop——竖屏特写照样燃。
    return rx, ("fit_blur" if inside < 0.5 else "crop")


def reframe_asset(asset_id: str, *, target_width: int | None = None,
                  target_height: int | None = None) -> dict:
    from PIL import Image
    from rembg import remove

    conn = db.connect()
    asset = db.asset_by_id(conn, asset_id)
    if not asset:
        conn.close()
        raise ValueError(f"未找到素材: {asset_id}")
    src_ar = (asset["width"] or 16) / (asset["height"] or 9)
    if (target_width is None) != (target_height is None):
        conn.close()
        raise ValueError("target_width 与 target_height 必须同时提供")
    # No requested delivery canvas means native composition is authoritative.
    target_ar = ((target_width / target_height)
                 if target_width is not None else src_ar)
    rows = conn.execute(
        "SELECT id, keyframe FROM shots WHERE asset_id=? AND keyframe IS NOT NULL", (asset["id"],)
    ).fetchall()
    session = matte._get_session()

    modes = {"crop": 0, "fit_blur": 0}
    for r in rows:
        mask = remove(Image.open(r["keyframe"]).convert("RGB"), session=session, only_mask=True)
        arr = np.asarray(mask.convert("L")).astype(float)
        rx, fm = _decide(arr, src_ar, target_ar)
        conn.execute("UPDATE shots SET reframe_x=?, fill_mode=? WHERE id=?", (rx, fm, r["id"]))
        modes[fm] += 1
    conn.commit()
    conn.close()
    return {"reframed": sum(modes.values()), **modes, "src_ar": round(src_ar, 3),
            "target_ar": round(target_ar, 3)}
