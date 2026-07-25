"""Phase 1.12–1.14 关键能力实测。

探测 SmartReframe / CreateMagicMask / SetSpeedRamp 的真实签名与生效性。
这三项决定「史诗级提升」的实际幅度，因此从 Phase 6 提前到 Phase 1。

一次性脚本，Phase 1 结束后删除。
"""
from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from studio.execution.resolve import connection  # noqa: E402

PROJECT = "_aes_key_caps"
REPO = pathlib.Path(__file__).resolve().parent

report: dict = {}


def try_call(label: str, fn, *args, **kwargs):
    """调用并记录结果，不让异常中断整个探测。"""
    try:
        result = fn(*args, **kwargs)
        ok = result not in (None, False)
        print(f"  {'OK  ' if ok else 'FAIL'} {label:52} -> {result!r}")
        return ok, result
    except Exception as exc:  # noqa: BLE001
        print(f"  ERR  {label:52} -> {type(exc).__name__}: {exc}")
        return False, None


def landscape_proxy() -> str:
    """挑一个横屏 1920x1080 素材，用于测竖屏重构图。"""
    return str(sorted((REPO / "library" / "proxies").glob("*.mp4"))[0].resolve())


def main() -> None:
    resolve = connection.connect()
    pm = resolve.GetProjectManager()
    pm.CloseProject(pm.GetCurrentProject())
    pm.DeleteProject(PROJECT)
    project = pm.CreateProject(PROJECT)

    # 竖屏 4:5 时间线 + 横屏源 —— SmartReframe 的目标场景
    for key, value in {
        "timelineFrameRate": "24",
        "timelineResolutionWidth": "1080",
        "timelineResolutionHeight": "1350",
        "timelineOutputResolutionWidth": "1080",
        "timelineOutputResolutionHeight": "1350",
    }.items():
        project.SetSetting(key, value)

    mp = project.GetMediaPool()
    media = mp.ImportMedia([landscape_proxy()])[0]
    print(f"源素材: {media.GetClipProperty('Resolution')} @ {media.GetClipProperty('FPS')}fps")
    print(f"时间线: {project.GetSetting('timelineResolutionWidth')}x"
          f"{project.GetSetting('timelineResolutionHeight')} @ "
          f"{project.GetSetting('timelineFrameRate')}fps\n")

    timeline = mp.CreateEmptyTimeline("caps")
    origin = timeline.GetStartFrame()
    fps = float(media.GetClipProperty("FPS"))
    mp.AppendToTimeline([
        {"mediaPoolItem": media, "startFrame": int(100 * fps),
         "endFrame": int(100 * fps) + 96, "trackIndex": 1, "recordFrame": origin},
        {"mediaPoolItem": media, "startFrame": int(200 * fps),
         "endFrame": int(200 * fps) + 96, "trackIndex": 1, "recordFrame": origin + 96},
    ])
    items = sorted(timeline.GetItemListInTrack("video", 1), key=lambda i: i.GetStart())
    print(f"时间线片段数: {len(items)}\n")

    # ── 1.12 SmartReframe ──────────────────────────────────────
    print("=== 1.12 SmartReframe（横屏 → 竖屏主体感知重构图）===")
    item = items[0]
    before = {k: item.GetProperty(k) for k in ("ZoomX", "ZoomY", "Pan", "Tilt")}
    print(f"  调用前 transform: {before}")

    ok, _ = try_call("SmartReframe()", item.SmartReframe)
    if not ok:
        # 有的版本要求指定预设
        for preset in ("Auto", "auto", 0, 1):
            ok, _ = try_call(f"SmartReframe({preset!r})", item.SmartReframe, preset)
            if ok:
                break

    after = {k: item.GetProperty(k) for k in ("ZoomX", "ZoomY", "Pan", "Tilt")}
    print(f"  调用后 transform: {after}")
    changed = before != after
    print(f"  → transform 是否被改变: {changed}")
    report["smart_reframe"] = {
        "callable": ok, "transform_changed": changed,
        "before": before, "after": after,
    }

    # ── 1.13 CreateMagicMask ───────────────────────────────────
    print("\n=== 1.13 CreateMagicMask（主体跟踪遮罩）===")
    item2 = items[1]
    mask_ok = False
    for mode in ("F", "B", "BI", "f", "forward", 0, 1):
        mask_ok, _ = try_call(f"CreateMagicMask({mode!r})", item2.CreateMagicMask, mode)
        if mask_ok:
            report["magic_mask_mode"] = mode
            break
    if not mask_ok:
        mask_ok, _ = try_call("CreateMagicMask()", item2.CreateMagicMask)

    print(f"  节点数（遮罩会新增 color 节点）: {item2.GetNumNodes()}")
    report["magic_mask"] = {"callable": mask_ok, "num_nodes": item2.GetNumNodes()}

    # ── 1.14 SetSpeedRamp ──────────────────────────────────────
    print("\n=== 1.14 变速：SetSpeedRamp 与 RetimeProcess ===")
    item3 = items[0]
    print(f"  变速前 duration: {item3.GetDuration()} 帧")

    # 先确认光流插值可编程
    try_call("SetProperty('RetimeProcess','NearestFrame')",
             item3.SetProperty, "RetimeProcess", "NearestFrame")
    try_call("SetProperty('RetimeProcess','OpticalFlow')",
             item3.SetProperty, "RetimeProcess", "OpticalFlow")
    try_call("SetProperty('MotionEstimation','SpeedWarp')",
             item3.SetProperty, "MotionEstimation", "SpeedWarp")
    print(f"  RetimeProcess    = {item3.GetProperty('RetimeProcess')!r}")
    print(f"  MotionEstimation = {item3.GetProperty('MotionEstimation')!r}")

    # 三段变速：1.0 → 0.35 → 1.4
    start = item3.GetStart()
    ramp_variants = [
        ("dict 列表(frame/speed)",
         [{"frame": start, "speed": 1.0},
          {"frame": start + 32, "speed": 0.35},
          {"frame": start + 64, "speed": 1.4}]),
        ("dict 列表(相对帧)",
         [{"frame": 0, "speed": 1.0},
          {"frame": 32, "speed": 0.35},
          {"frame": 64, "speed": 1.4}]),
        ("元组列表", [(start, 1.0), (start + 32, 0.35), (start + 64, 1.4)]),
    ]
    ramp_ok = False
    for label, payload in ramp_variants:
        ramp_ok, _ = try_call(f"SetSpeedRamp({label})", item3.SetSpeedRamp, payload)
        if ramp_ok:
            report["speed_ramp_signature"] = label
            break

    print(f"  变速后 duration: {item3.GetDuration()} 帧")
    report["speed_ramp"] = {
        "callable": ramp_ok,
        "retime_process": item3.GetProperty("RetimeProcess"),
        "motion_estimation": item3.GetProperty("MotionEstimation"),
        "duration_after": item3.GetDuration(),
    }

    out = REPO / "key_capabilities_probe.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    print(f"\n报告已写入: {out}")


if __name__ == "__main__":
    main()
