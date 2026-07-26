"""SmartReframe 视觉验证 —— 第二版，修掉第一版的方法论缺陷。

第一版的错误（值得记录）：
    1. 随手取了 300s 处的帧，实测 YAVG≈30，接近全黑。
       黑场上做任何重构图都看不出差别 —— 对照组也"无变化"，
       是检测方法失效，不是能力失效。
    2. 设了 Pan 却没读回确认，无法区分「没生效」和「没设上」。

本版做法：
    - 先用 ffmpeg 扫描，挑一个**画面明亮且细节丰富**的时间点
    - 每次操作后读回属性确认
    - 每次渲染后重新获取 item 引用（避免对象过期）
    - 先跑对照组：手动 Pan 必须produce可见差异，否则方法论无效，直接终止
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from studio.execution.resolve import connection  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parent
OUT = REPO / "probe_out"
PROJECT = "_aes_smartreframe_visual"


def pick_bright_moment(video: pathlib.Path) -> float:
    """扫描若干候选时间点，挑亮度与细节最好的那个。"""
    best, best_score = 60.0, -1.0
    for t in (60, 120, 180, 240, 300, 420, 540, 660, 780, 900):
        png = OUT / f"_scan_{t}.png"
        r = subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-ss", str(t), "-i", str(video),
             "-vframes", "1", str(png)], capture_output=True)
        if r.returncode != 0 or not png.exists():
            continue
        stats = signalstats(png)
        # 亮度适中 + 有色彩偏离灰点 = 有内容
        y = stats.get("YAVG", 0)
        score = y if 40 < y < 200 else 0
        if score > best_score:
            best, best_score = float(t), score
        png.unlink(missing_ok=True)
    print(f"  选定时间点 {best}s（亮度分 {best_score:.1f}）")
    return best


def signalstats(png: pathlib.Path) -> dict:
    r = subprocess.run(
        ["ffmpeg", "-loglevel", "info", "-i", str(png),
         "-vf", "signalstats,metadata=mode=print", "-f", "null", "-"],
        capture_output=True, text=True)
    stats = {}
    for line in r.stderr.splitlines():
        for key in ("YAVG", "YMIN", "YMAX", "UAVG", "VAVG"):
            if f"lavfi.signalstats.{key}" in line:
                stats[key] = float(line.split("=")[-1])
    return stats


def frame_md5(video: pathlib.Path, tag: str) -> tuple[str, dict]:
    png = OUT / f"{tag}.png"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(video),
                    "-vframes", "1", str(png)], capture_output=True)
    md5 = subprocess.run(["md5", "-q", str(png)], capture_output=True, text=True).stdout.strip()
    return md5, signalstats(png)


def render(project, name: str) -> pathlib.Path | None:
    project.DeleteAllRenderJobs()
    project.LoadRenderPreset("H.264 Master")
    project.SetRenderSettings(
        {"TargetDir": str(OUT), "CustomName": name, "SelectAllFrames": True})
    job = project.AddRenderJob()
    if not job:
        return None
    project.StartRendering(job)
    for _ in range(180):
        if not project.IsRenderingInProgress():
            break
        time.sleep(1)
    hits = sorted(OUT.glob(f"{name}.*"))
    return hits[0] if hits else None


def main() -> None:
    OUT.mkdir(exist_ok=True)
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

    src = sorted((REPO / "library" / "proxies").glob("*.mp4"))[0].resolve()
    print("挑选有内容的画面：")
    moment = pick_bright_moment(src)

    mp = project.GetMediaPool()
    media = mp.ImportMedia([str(src)])[0]
    tl = mp.CreateEmptyTimeline("sr")
    fps = float(media.GetClipProperty("FPS"))
    mp.AppendToTimeline([{
        "mediaPoolItem": media, "startFrame": int(moment * fps),
        "endFrame": int(moment * fps) + 24, "trackIndex": 1,
        "recordFrame": tl.GetStartFrame(),
    }])

    def item():
        """每次重新获取，避免对象引用过期。"""
        return tl.GetItemListInTrack("video", 1)[0]

    report: dict = {"moment_sec": moment}

    # ── 基线 ──
    print("\n=== 基线渲染 ===")
    base = render(project, "v2_base")
    if not base:
        sys.exit("渲染失败，无法继续")
    base_md5, base_stats = frame_md5(base, "v2_base")
    print(f"  {base.name}  md5={base_md5[:12]}  {base_stats}")
    report["base"] = {"md5": base_md5, "stats": base_stats}

    # ── 对照组：手动 Pan（必须可见，否则方法论无效）──
    print("\n=== 对照组：手动 Pan 400px ===")
    it = item()
    applied = it.SetProperty("Pan", 400.0)
    readback = it.GetProperty("Pan")
    print(f"  SetProperty -> {applied}，读回 Pan = {readback}")
    ctrl = render(project, "v2_control")
    ctrl_md5, ctrl_stats = frame_md5(ctrl, "v2_control") if ctrl else ("", {})
    control_visible = ctrl_md5 != base_md5
    print(f"  md5={ctrl_md5[:12]}  {ctrl_stats}")
    print(f"  → 对照组可见差异: {control_visible}")
    report["control"] = {"applied": bool(applied), "readback": readback,
                         "md5": ctrl_md5, "stats": ctrl_stats,
                         "visible": control_visible}

    if not control_visible:
        print("\n❌ 对照组无差异 —— 检测方法仍然无效，SmartReframe 的结论不可信。")
        (REPO / "smartreframe_visual.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2, default=str))
        return
    print("  ✅ 方法论有效，可以判定 SmartReframe")

    # ── SmartReframe ──
    print("\n=== SmartReframe ===")
    it = item()
    it.SetProperty("Pan", 0.0)          # 复位
    print(f"  复位后 Pan = {it.GetProperty('Pan')}")
    ret = it.SmartReframe()
    print(f"  SmartReframe() -> {ret!r}")
    time.sleep(8)                       # 主体分析可能异步
    it = item()
    print(f"  调用后 Pan={it.GetProperty('Pan')} ZoomX={it.GetProperty('ZoomX')}")
    sr = render(project, "v2_smartreframe")
    sr_md5, sr_stats = frame_md5(sr, "v2_smartreframe") if sr else ("", {})
    sr_visible = sr_md5 != base_md5
    print(f"  md5={sr_md5[:12]}  {sr_stats}")
    print(f"  → 相对基线是否改变画面: {sr_visible}")

    report["smart_reframe"] = {
        "returns": ret, "md5": sr_md5, "stats": sr_stats,
        "changes_output": sr_visible,
        "pan_after": it.GetProperty("Pan"), "zoomx_after": it.GetProperty("ZoomX"),
    }
    report["verdict"] = (
        "SmartReframe 生效" if sr_visible else "SmartReframe 返回 True 但不改变输出（空转）"
    )
    print(f"\n结论: {report['verdict']}")

    (REPO / "smartreframe_visual.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
