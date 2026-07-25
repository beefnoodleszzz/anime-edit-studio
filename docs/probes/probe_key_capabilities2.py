"""关键能力深挖（第二轮）。

第一轮结论与新问题：
    SmartReframe()      返回 True，但 ZoomX/Pan 没变 —— 效果写在哪？
    CreateMagicMask()   全部签名返回 False —— 是否需要切到 Color 页？
    RetimeProcess       SetProperty 传字符串失败，GetProperty 返回 0 —— 应传整数枚举？
    SetSpeedRamp        属性存在但值为 None —— hasattr 是假阳性，方法并不存在
"""
from __future__ import annotations

import json
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from studio.execution.resolve import connection  # noqa: E402

PROJECT = "_aes_key_caps2"
REPO = pathlib.Path(__file__).resolve().parent
report: dict = {}

ALL_PROPS = [
    "ZoomX", "ZoomY", "ZoomGang", "Pan", "Tilt", "RotationAngle",
    "AnchorPointX", "AnchorPointY", "CropLeft", "CropRight", "CropTop",
    "CropBottom", "Opacity", "ResizeFilter", "RetimeProcess",
    "MotionEstimation", "Scaling", "DynamicZoomEase", "CompositeMode",
]


def snapshot(item) -> dict:
    return {k: item.GetProperty(k) for k in ALL_PROPS}


def diff(a: dict, b: dict) -> dict:
    return {k: (a[k], b[k]) for k in a if a[k] != b[k]}


def try_call(label, fn, *args):
    try:
        r = fn(*args)
        print(f"  {'OK  ' if r not in (None, False) else 'FAIL'} {label:46} -> {r!r}")
        return r
    except Exception as exc:  # noqa: BLE001
        print(f"  ERR  {label:46} -> {type(exc).__name__}: {exc}")
        return None


def main() -> None:
    resolve = connection.connect()
    pm = resolve.GetProjectManager()
    pm.CloseProject(pm.GetCurrentProject())
    pm.DeleteProject(PROJECT)
    project = pm.CreateProject(PROJECT)
    for k, v in {
        "timelineFrameRate": "24",
        "timelineResolutionWidth": "1080", "timelineResolutionHeight": "1350",
        "timelineOutputResolutionWidth": "1080", "timelineOutputResolutionHeight": "1350",
    }.items():
        project.SetSetting(k, v)

    mp = project.GetMediaPool()
    src = str(sorted((REPO / "library" / "proxies").glob("*.mp4"))[0].resolve())
    media = mp.ImportMedia([src])[0]
    tl = mp.CreateEmptyTimeline("caps2")
    origin, fps = tl.GetStartFrame(), float(media.GetClipProperty("FPS"))
    mp.AppendToTimeline([
        {"mediaPoolItem": media, "startFrame": int(100 * fps),
         "endFrame": int(100 * fps) + 96, "trackIndex": 1, "recordFrame": origin},
    ])
    item = tl.GetItemListInTrack("video", 1)[0]

    # ── A. 变速：属性到底接受什么类型 ──────────────────────────
    print("=== A. RetimeProcess / MotionEstimation 的取值类型 ===")
    print(f"  当前值: RetimeProcess={item.GetProperty('RetimeProcess')!r} "
          f"MotionEstimation={item.GetProperty('MotionEstimation')!r}")
    accepted = {}
    for prop in ("RetimeProcess", "MotionEstimation"):
        for value in (0, 1, 2, 3):
            r = item.SetProperty(prop, value)
            if r:
                accepted.setdefault(prop, []).append(value)
        print(f"  {prop} 接受的整数值: {accepted.get(prop, [])}")
    report["retime_enums"] = accepted

    # ── B. 变速：真正的接口是什么 ─────────────────────────────
    print("\n=== B. 变速接口探测 ===")
    print(f"  SetSpeedRamp 属性值: {item.SetSpeedRamp!r}  "
          f"(callable={callable(item.SetSpeedRamp)})")
    speed_related = [m for m in dir(item)
                     if any(k in m.lower() for k in ("speed", "retime", "duration", "flow"))]
    print(f"  片段上与变速相关的方法: {speed_related}")

    before_dur = item.GetDuration()
    for prop in ("Speed", "SpeedPercent", "PlaybackSpeed"):
        try_call(f"SetProperty({prop!r}, 50)", item.SetProperty, prop, 50)
    print(f"  duration: {before_dur} -> {item.GetDuration()}")
    report["speed_ramp_method_exists"] = callable(item.SetSpeedRamp)
    report["speed_related_methods"] = speed_related

    # ── C. SmartReframe 的效果写在哪 ──────────────────────────
    print("\n=== C. SmartReframe 效果落点 ===")
    resolve.OpenPage("edit")
    before = snapshot(item)
    r = try_call("SmartReframe()", item.SmartReframe)
    time.sleep(3)                      # 分析可能是异步的
    after = snapshot(item)
    d = diff(before, after)
    print(f"  返回值={r!r}  属性变化: {d if d else '无'}")

    # 换成手动 Pan/Zoom 验证「属性确实可写」，排除只读的可能
    manual = try_call("SetProperty('ZoomX', 1.78)", item.SetProperty, "ZoomX", 1.78)
    print(f"  手动设 ZoomX 后: {item.GetProperty('ZoomX')!r}")
    report["smart_reframe"] = {
        "returns": r, "prop_diff": {k: list(v) for k, v in d.items()},
        "manual_zoom_writable": bool(manual),
    }

    # ── D. Magic Mask 是否需要 Color 页 ───────────────────────
    print("\n=== D. CreateMagicMask（切到 Color 页 + 选中片段）===")
    resolve.OpenPage("color")
    time.sleep(1)
    tl.SetCurrentTimecode(tl.GetStartTimecode())
    print(f"  当前页: {resolve.GetCurrentPage()}  节点数: {item.GetNumNodes()}")
    mask_ok = None
    for mode in ("F", "B", "BI"):
        mask_ok = try_call(f"CreateMagicMask({mode!r})", item.CreateMagicMask, mode)
        if mask_ok:
            report["magic_mask_mode"] = mode
            break
    print(f"  调用后节点数: {item.GetNumNodes()}")
    report["magic_mask"] = {"ok": bool(mask_ok), "num_nodes": item.GetNumNodes(),
                            "page": resolve.GetCurrentPage()}

    resolve.OpenPage("edit")
    out = REPO / "key_capabilities_probe2.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    print(f"\n报告: {out}")


if __name__ == "__main__":
    main()
