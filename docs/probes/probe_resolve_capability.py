"""一次性能力探测：回答 resolve_capabilities.yaml 里哪些能力真的存在。

不属于最终架构，Phase 1 完成后删除。
用法：
  export RESOLVE_SCRIPT_API="/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting"
  export RESOLVE_SCRIPT_LIB="/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so"
  export PYTHONPATH="$PYTHONPATH:$RESOLVE_SCRIPT_API/Modules/"
  .venv/bin/python probe_resolve_capability.py
"""
import json
import sys
from pathlib import Path

import DaVinciResolveScript as dvr

PROBE_PROJECT = "_aes_capability_probe"
REPO = Path(__file__).resolve().parent


def methods(obj):
    return sorted(m for m in dir(obj) if not m.startswith("_"))


def main():
    resolve = dvr.scriptapp("Resolve")
    if not resolve:
        sys.exit("Resolve 未运行或脚本 API 未启用")

    report = {"resolve_version": resolve.GetVersionString(), "api": {}}
    pm = resolve.GetProjectManager()

    # 干净的探测工程，不污染现有工程
    pm.DeleteProject(PROBE_PROJECT)
    project = pm.CreateProject(PROBE_PROJECT) or pm.LoadProject(PROBE_PROJECT)
    if not project:
        sys.exit("无法创建探测工程")

    report["api"]["Resolve"] = methods(resolve)
    report["api"]["ProjectManager"] = methods(pm)
    report["api"]["Project"] = methods(project)

    mp = project.GetMediaPool()
    report["api"]["MediaPool"] = methods(mp)
    report["api"]["Folder"] = methods(mp.GetRootFolder())

    # 导入一个已有代理做真实 clip 探测
    proxies = sorted((REPO / "library" / "proxies").glob("*.mp4"))
    if not proxies:
        sys.exit("library/proxies 下没有可用素材")
    src = str(proxies[0])

    ms = resolve.GetMediaStorage()
    report["api"]["MediaStorage"] = methods(ms)

    items = mp.ImportMedia([src])
    report["import_media_ok"] = bool(items)
    if not items:
        sys.exit("ImportMedia 失败")
    mpi = items[0]
    report["api"]["MediaPoolItem"] = methods(mpi)
    report["clip_props_sample"] = {
        k: mpi.GetClipProperty(k)
        for k in ("FPS", "Resolution", "Duration", "Format", "Video Codec")
    }

    # 时间线：按 in/out 放置
    project.SetSetting("timelineFrameRate", "24.0")
    tl = mp.CreateEmptyTimeline("probe_tl")
    report["create_timeline_ok"] = bool(tl)
    report["api"]["Timeline"] = methods(tl)

    appended = mp.AppendToTimeline([
        {"mediaPoolItem": mpi, "startFrame": 100, "endFrame": 150, "trackIndex": 1},
        {"mediaPoolItem": mpi, "startFrame": 300, "endFrame": 340, "trackIndex": 1},
    ])
    report["append_with_in_out_ok"] = bool(appended)
    report["appended_count"] = len(appended) if appended else 0

    items_v1 = tl.GetItemListInTrack("video", 1) or []
    report["timeline_item_count"] = len(items_v1)
    if items_v1:
        ti = items_v1[0]
        report["api"]["TimelineItem"] = methods(ti)
        # 逐项探测关键能力是否可调用（存在 ≠ 生效，但不存在 = 一定不行）
        probe = {}
        for name in (
            "SetProperty", "GetProperty",
            "SetClipColor", "AddFlag", "AddMarker",
            "GetFusionCompCount", "AddFusionComp", "ImportFusionComp",
            "LoadFusionCompByName", "GetFusionCompByIndex",
            "SetLUT", "SetCDL", "GetNumNodes",
            "AddTake", "SetScale", "GetStereoConvergenceValues",
            "CreateMagicMask", "SmartReframe",
            "SetSpeedRamp",
        ):
            probe[name] = hasattr(ti, name)
        report["timeline_item_capabilities"] = probe

        # 变速/属性可写性实测
        try:
            report["set_property_ZoomX"] = ti.SetProperty("ZoomX", 1.12)
        except Exception as e:
            report["set_property_ZoomX"] = f"ERR {e}"
        try:
            report["get_property_all_keys"] = sorted((ti.GetProperty() or {}).keys())
        except Exception as e:
            report["get_property_all_keys"] = f"ERR {e}"
        try:
            report["fusion_comp_count"] = ti.GetFusionCompCount()
            comp = ti.AddFusionComp()
            report["add_fusion_comp"] = bool(comp)
            if comp:
                report["api"]["FusionComp"] = methods(comp)[:60]
        except Exception as e:
            report["add_fusion_comp"] = f"ERR {e}"

    # 标记
    try:
        report["add_marker"] = tl.AddMarker(10, "Blue", "drop", "probe", 1)
    except Exception as e:
        report["add_marker"] = f"ERR {e}"

    # 渲染预设
    report["render_presets"] = project.GetRenderPresetList()
    report["render_formats"] = list((project.GetRenderFormats() or {}).items())[:10]
    report["render_codecs_h264"] = list((project.GetRenderCodecs("mp4") or {}).items())[:10]

    out = REPO / "resolve_capability_probe.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"written: {out}")

    # 控制台摘要
    print("\n=== 关键结论 ===")
    for k in ("resolve_version", "import_media_ok", "create_timeline_ok",
              "append_with_in_out_ok", "appended_count", "timeline_item_count",
              "set_property_ZoomX", "fusion_comp_count", "add_fusion_comp", "add_marker"):
        print(f"{k:26} {report.get(k)}")
    print("\nTimelineItem 能力:")
    for k, v in (report.get("timeline_item_capabilities") or {}).items():
        print(f"  {'OK ' if v else '-- '} {k}")


if __name__ == "__main__":
    main()
