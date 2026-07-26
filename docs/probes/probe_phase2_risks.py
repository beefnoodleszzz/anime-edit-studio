"""Phase 2.0 professional-execution risk probe.

This is deliberately a probe, not production code.  It answers four questions
whose results constrain EditSpec v2:

1. Can a ColorGroup be created, assigned, graded and observed in a render?
2. Does the public API expose Fairlight volume automation, beyond track setup?
3. Is there a native transition API, or at least a scriptable Fusion fallback?
4. Can a Fusion composition be exported, imported and parameterised?

Visual capabilities follow pitfalls P12-P14: callability and return values are
recorded, but a positive verdict requires changed rendered output.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import subprocess
import sys
import time
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from studio.execution.resolve import connection  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
OUT = HERE / "phase2_risk_out"
PROJECT = "_aes_phase2_risk_probe"


def serialise(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple)):
        return [serialise(v) for v in value]
    if isinstance(value, dict):
        return {str(k): serialise(v) for k, v in value.items()}
    return repr(value)


def call(label: str, fn, *args) -> dict[str, Any]:
    callable_ = callable(fn)
    result = None
    error = None
    if callable_:
        try:
            result = fn(*args)
        except Exception as exc:  # noqa: BLE001 - probes must record remote failures
            error = f"{type(exc).__name__}: {exc}"
    print(f"  {label}: callable={callable_} result={result!r} error={error}")
    return {"callable": callable_, "result": serialise(result), "error": error}


def render(project, name: str) -> pathlib.Path | None:
    for hit in OUT.glob(f"{name}.*"):
        hit.unlink()
    project.DeleteAllRenderJobs()
    project.LoadRenderPreset("H.264 Master")
    project.SetRenderSettings(
        {"TargetDir": str(OUT), "CustomName": name, "SelectAllFrames": True}
    )
    job = project.AddRenderJob()
    if not job or not project.StartRendering(job):
        return None
    for _ in range(180):
        if not project.IsRenderingInProgress():
            break
        time.sleep(1)
    hits = sorted(OUT.glob(f"{name}.*"))
    return hits[0] if hits else None


def first_frame_digest(video: pathlib.Path, name: str) -> dict[str, Any]:
    frame = OUT / f"{name}.png"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(video), "-frames:v", "1", str(frame)],
        check=True,
    )
    digest = hashlib.sha256(frame.read_bytes()).hexdigest()
    stats_run = subprocess.run(
        [
            "ffmpeg", "-loglevel", "info", "-i", str(frame),
            "-vf", "signalstats,metadata=mode=print", "-f", "null", "-",
        ],
        capture_output=True, text=True, check=False,
    )
    stats: dict[str, float] = {}
    for line in stats_run.stderr.splitlines():
        for key in ("YAVG", "UAVG", "VAVG"):
            marker = f"lavfi.signalstats.{key}="
            if marker in line:
                stats[key] = float(line.rsplit("=", 1)[1])
    return {"sha256": digest, "stats": stats, "frame": str(frame)}


def write_test_lut() -> pathlib.Path:
    """Write a deliberately obvious 2×2×2 inverse LUT for visual verification."""
    path = OUT / "phase2_inverse.cube"
    lines = [
        'TITLE "AES Phase2 inverse probe"',
        "LUT_3D_SIZE 2",
        "DOMAIN_MIN 0.0 0.0 0.0",
        "DOMAIN_MAX 1.0 1.0 1.0",
    ]
    for b in (0.0, 1.0):
        for g in (0.0, 1.0):
            for r in (0.0, 1.0):
                lines.append(f"{1-r:.6f} {1-g:.6f} {1-b:.6f}")
    path.write_text("\n".join(lines) + "\n")
    return path


def pick_visible_moment(video: pathlib.Path) -> float:
    """P14 guard: choose a non-black source moment before visual comparisons."""
    best_time, best_score = 0.0, -1.0
    for second in (30, 60, 90, 120, 180, 240, 360, 540):
        run = subprocess.run(
            [
                "ffmpeg", "-loglevel", "info", "-ss", str(second), "-i", str(video),
                "-frames:v", "1", "-vf", "signalstats,metadata=mode=print",
                "-f", "null", "-",
            ],
            capture_output=True, text=True, check=False,
        )
        yavg = 0.0
        for line in run.stderr.splitlines():
            marker = "lavfi.signalstats.YAVG="
            if marker in line:
                yavg = float(line.rsplit("=", 1)[1])
                break
        score = yavg if 40.0 < yavg < 200.0 else 0.0
        if score > best_score:
            best_time, best_score = float(second), score
    if best_score <= 0:
        raise SystemExit("P14 guard failed: no non-black probe frame found")
    return best_time


def method_names(obj, words: tuple[str, ...]) -> list[str]:
    return sorted(
        name for name in dir(obj)
        if any(word in name.lower() for word in words)
    )


def main() -> None:
    OUT.mkdir(exist_ok=True)
    report: dict[str, Any] = {
        "probe": "phase2_professional_execution_risks",
        "resolve_version": None,
        "project": PROJECT,
    }

    resolve = connection.connect()
    report["resolve_version"] = resolve.GetVersionString()
    pm = resolve.GetProjectManager()
    current = pm.GetCurrentProject()
    if current:
        pm.CloseProject(current)
    pm.DeleteProject(PROJECT)
    project = pm.CreateProject(PROJECT)
    if project is None:
        raise SystemExit("cannot create probe project")
    for key, value in {
        "timelineFrameRate": "24",
        "timelineResolutionWidth": "1080",
        "timelineResolutionHeight": "1350",
        "timelineOutputResolutionWidth": "1080",
        "timelineOutputResolutionHeight": "1350",
    }.items():
        project.SetSetting(key, value)

    source = sorted((ROOT / "library" / "proxies").glob("*.mp4"))[0].resolve()
    moment = pick_visible_moment(source)
    report["source"] = {"path": str(source), "moment_sec": moment}
    pool = project.GetMediaPool()
    media = pool.ImportMedia([str(source)])[0]
    timeline = pool.CreateEmptyTimeline("phase2-risk")
    fps = float(media.GetClipProperty("FPS"))
    origin = timeline.GetStartFrame()
    items = pool.AppendToTimeline([
        {
            "mediaPoolItem": media,
            "startFrame": int(moment * fps),
            "endFrame": int(moment * fps) + int(2 * fps),
            "trackIndex": 1,
            "recordFrame": origin,
        },
        {
            "mediaPoolItem": media,
            "startFrame": int((moment + 5) * fps),
            "endFrame": int((moment + 5) * fps) + int(2 * fps),
            "trackIndex": 1,
            "recordFrame": origin + 48,
        },
    ])
    if not items or any(item is None for item in items):
        raise SystemExit("cannot append probe clips")
    first, second = items

    print("=== Baseline ===")
    baseline_video = render(project, "phase2_base")
    if baseline_video is None:
        raise SystemExit("baseline render failed")
    baseline = first_frame_digest(baseline_video, "phase2_base")
    report["baseline"] = baseline

    print("\n=== ColorGroup ===")
    color: dict[str, Any] = {}
    group = project.AddColorGroup("_aes_phase2_color")
    color["add_group"] = bool(group)
    color["assign_first"] = call("AssignToColorGroup(first)", first.AssignToColorGroup, group)
    color["assign_second"] = call("AssignToColorGroup(second)", second.AssignToColorGroup, group)
    color["group_members"] = len(group.GetClipsInTimeline(timeline) or []) if group else 0
    color["add_version"] = call("AddVersion", first.AddVersion, "_aes_recipe_v1", 0)
    lut = write_test_lut()
    color["refresh_luts"] = call("RefreshLUTList", project.RefreshLUTList)
    graph = group.GetPostClipNodeGraph() if group else None
    color["group_graph_nodes"] = graph.GetNumNodes() if graph else None
    # Use a Resolve-shipped LUT already present in its discovered LUT registry.
    # The generated inverse LUT remains evidence that arbitrary external paths
    # are rejected until installed/refreshed through Resolve's LUT locations.
    builtin_lut = "Olympus/Olympus OM-Log400_to_BT.709_v1.0.cube"
    color["custom_lut_path"] = str(lut)
    color["builtin_lut"] = builtin_lut
    color["set_group_lut"] = (
        call("ColorGroup.PostClip.SetLUT", graph.SetLUT, 1, builtin_lut)
        if graph and graph.GetNumNodes() else
        {"callable": False, "result": None, "error": "group graph has no nodes"}
    )
    color_video = render(project, "phase2_color")
    if color_video:
        color_frame = first_frame_digest(color_video, "phase2_color")
        color["render"] = color_frame
        color["changes_output"] = color_frame["sha256"] != baseline["sha256"]
    else:
        color["render"] = None
        color["changes_output"] = False
    color["verified"] = bool(
        color["add_group"]
        and color["group_members"] == 2
        and color["set_group_lut"]["result"]
        and color["changes_output"]
    )
    report["color_recipe"] = color

    print("\n=== Fairlight / audio tracks ===")
    fairlight: dict[str, Any] = {}
    before_tracks = timeline.GetTrackCount("audio") or 0
    fairlight["add_track"] = call("AddTrack(audio, stereo)", timeline.AddTrack, "audio", "stereo")
    fairlight["track_count_before"] = before_tracks
    fairlight["track_count_after"] = timeline.GetTrackCount("audio") or 0
    fairlight["timeline_audio_methods"] = method_names(
        timeline, ("audio", "volume", "gain", "automation", "keyframe", "fairlight")
    )
    audio_items = timeline.GetItemListInTrack("audio", 1) or []
    fairlight["audio_item_count"] = len(audio_items)
    if audio_items:
        audio_item = audio_items[0]
        fairlight["audio_item_methods"] = method_names(
            audio_item, ("audio", "volume", "gain", "automation", "keyframe", "fairlight")
        )
        props = audio_item.GetProperty() or {}
        fairlight["audio_properties"] = serialise(props)
        attempts = {}
        for key in ("Volume", "AudioVolume", "Gain", "Pan"):
            attempts[key] = {
                "before": serialise(audio_item.GetProperty(key)),
                "set": serialise(audio_item.SetProperty(key, -12.0)),
                "after": serialise(audio_item.GetProperty(key)),
            }
        fairlight["property_attempts"] = attempts
    fairlight["automation_callable"] = any(
        any(word in name.lower() for word in ("automation", "keyframe", "volume", "gain"))
        for name in fairlight.get("audio_item_methods", [])
    )
    fairlight["track_management_verified"] = bool(
        fairlight["add_track"]["result"]
        and fairlight["track_count_after"] == before_tracks + 1
    )
    fairlight["automation_verified"] = False
    report["fairlight"] = fairlight

    print("\n=== Transition ===")
    transition: dict[str, Any] = {
        "timeline_methods": method_names(timeline, ("transition",)),
        "item_methods": method_names(first, ("transition",)),
    }
    transition["native_api_found"] = bool(
        transition["timeline_methods"] or transition["item_methods"]
    )
    transition["fusion_clip"] = call(
        "CreateFusionClip(two adjacent clips)", timeline.CreateFusionClip, [first, second]
    )
    transition["fusion_fallback_created"] = bool(transition["fusion_clip"]["result"])
    transition["verified"] = False
    report["transition"] = transition

    print("\n=== Fusion Recipe round-trip ===")
    fusion: dict[str, Any] = {}
    probe_item = (timeline.GetItemListInTrack("video", 1) or [first])[0]
    comp = probe_item.AddFusionComp()
    fusion["add_comp"] = bool(comp)
    fusion["tools_before"] = method_names(comp, ("tool",)) if comp else []
    tools = comp.GetToolList(False) if comp else {}
    fusion["tool_ids_before"] = sorted(str(k) for k in (tools or {}))
    brightness = None
    if comp and callable(getattr(comp, "AddTool", None)):
        try:
            brightness = comp.AddTool("BrightnessContrast")
        except Exception as exc:  # noqa: BLE001
            fusion["add_brightness_error"] = f"{type(exc).__name__}: {exc}"
    fusion["add_brightness"] = bool(brightness)
    if brightness:
        try:
            brightness.SetInput("Gain", 0.25)
            fusion["gain_readback"] = serialise(brightness.GetInput("Gain"))
            tool_values = list((tools or {}).values())
            media_in = next(
                tool for tool in tool_values
                if (tool.GetAttrs() or {}).get("TOOLS_RegID") == "MediaIn"
            )
            media_out = next(
                tool for tool in tool_values
                if (tool.GetAttrs() or {}).get("TOOLS_RegID") == "MediaOut"
            )
            fusion["connect_input"] = bool(brightness.ConnectInput("Input", media_in))
            fusion["connect_output"] = bool(media_out.ConnectInput("Input", brightness))
        except Exception as exc:  # noqa: BLE001
            fusion["gain_error"] = f"{type(exc).__name__}: {exc}"
    fusion_video = render(project, "phase2_fusion")
    if fusion_video:
        fusion_frame = first_frame_digest(fusion_video, "phase2_fusion")
        fusion["render"] = fusion_frame
        fusion["changes_output"] = fusion_frame["sha256"] != color["render"]["sha256"]
    else:
        fusion["render"] = None
        fusion["changes_output"] = False
    comp_path = OUT / "phase2_recipe.comp"
    fusion["export"] = call("ExportFusionComp", probe_item.ExportFusionComp, str(comp_path), 1)
    fusion["export_exists"] = comp_path.exists() and comp_path.stat().st_size > 0
    fusion["delete"] = call(
        "DeleteFusionCompByName",
        probe_item.DeleteFusionCompByName,
        (probe_item.GetFusionCompNameList() or ["Composition 1"])[0],
    )
    imported = probe_item.ImportFusionComp(str(comp_path)) if fusion["export_exists"] else None
    fusion["import_result"] = bool(imported)
    fusion["comp_count_after_import"] = probe_item.GetFusionCompCount()
    fusion["parameter_injection"] = fusion.get("gain_readback") == 0.25
    fusion["roundtrip_verified"] = bool(
        fusion["add_comp"]
        and fusion["export"]["result"]
        and fusion["export_exists"]
        and fusion["import_result"]
        and fusion["parameter_injection"]
    )
    fusion["visual_output_verified"] = bool(
        fusion.get("connect_input")
        and fusion.get("connect_output")
        and fusion["changes_output"]
    )
    report["fusion_recipe"] = fusion

    out = HERE / "phase2_risk_probe.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=serialise))
    print(f"\nReport: {out}")


if __name__ == "__main__":
    main()
