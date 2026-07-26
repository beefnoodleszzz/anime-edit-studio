"""Resolve 21 Fusion motion capability probe.

This is evidence-gathering code, not production execution. It creates an
isolated project, builds two adjacent clips, converts them to one Fusion clip,
and records the real tool graph exposed by Resolve.
"""
from __future__ import annotations

import json
from pathlib import Path

from studio.core.timecode import Timebase
from studio.execution.resolve import ResolveAdapter


ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent / "fusion_motion_out"
PROJECT = "_aes_fusion_motion_probe"


def _tool_row(tool) -> dict:
    attrs = tool.GetAttrs() or {}
    return {
        "name": attrs.get("TOOLS_Name"),
        "reg_id": attrs.get("TOOLS_RegID"),
        "global_in": attrs.get("TOOLNT_GlobalIn"),
        "global_out": attrs.get("TOOLNT_GlobalOut"),
    }


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    sources = sorted((ROOT / "library" / "proxies").glob("*.mp4"))
    if len(sources) < 2:
        raise SystemExit("need at least two proxy sources")

    with ResolveAdapter.open() as rv:
        rv.ensure_project(
            PROJECT,
            timebase=Timebase(24, 1),
            width=1080,
            height=1350,
            reset=True,
        )
        rv.ensure_timeline("motion-probe", reset=True)
        infos = rv.import_media(sources[:2], bin_name="probe")
        items = rv.append_clips(
            [
                {
                    "media_path": source,
                    "source_in_sec": 30.0,
                    "source_out_sec": 32.0,
                    "timeline_in_sec": index * 2.0,
                    "track_index": 1,
                    "media_fps": infos[str(source)].fps,
                    "timeline_fps": Timebase(24, 1),
                    "media_type": 1,
                }
                for index, source in enumerate(sources[:2])
            ]
        )
        timeline = rv.timeline
        create = getattr(timeline, "CreateFusionClip", None)
        fused = create(items) if callable(create) else None
        report = {
            "resolve_version": rv.version,
            "create_fusion_clip_callable": callable(create),
            "create_fusion_clip_succeeded": bool(fused),
            "timeline_items_after": len(rv.timeline_items()),
        }
        if fused:
            added_comp = fused.AddFusionComp()
            report.update(
                {
                    "duration_frames": fused.GetDuration(),
                    "start_frame": fused.GetStart(),
                    "end_frame": fused.GetEnd(),
                    "comp_names": list(fused.GetFusionCompNameList() or []),
                    "add_fusion_comp_succeeded": bool(added_comp),
                }
            )
            names = fused.GetFusionCompNameList() or []
            comp = (
                fused.GetFusionCompByName(names[0])
                if names and callable(getattr(fused, "GetFusionCompByName", None))
                else added_comp
            )
            report["tools"] = [
                _tool_row(tool)
                for tool in (comp.GetToolList(False) or {}).values()
            ] if comp else []

        target = OUT / "fusion_container_structure.json"
        target.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
