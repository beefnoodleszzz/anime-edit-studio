"""Project-level readiness gate for the Studio's extreme production line."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from . import config, experiment, quality_gate, sound


def capabilities() -> dict:
    vspipe = shutil.which("vspipe")
    ffmpeg = config.tool("ffmpeg")
    filters = subprocess.run([ffmpeg, "-hide_banner", "-filters"],
                             capture_output=True, text=True).stdout
    audio = sound.capabilities()
    items = {
        "core_tools": not any(config.tool_optional(name) is None for name in
                              ("ffmpeg", "ffprobe", "realesrgan", "rife", "chrome")),
        "vapoursynth": bool(vspipe),
        "source_audio_bed": audio["source_audio_bed"],
        "rubberband": audio["rubberband"],
        "demucs": audio["demucs"],
        "linear_light_compositor": False,
        "tracked_video_matte": False,
        "platform_private_api": False,
    }
    return {
        "items": items,
        "ready": [name for name, value in items.items() if value],
        "unavailable": [name for name, value in items.items() if not value],
        "fallbacks": {
            "rubberband": audio["policy"]["rubberband"],
            "demucs": "source audio + authored SFX" if not audio["demucs"] else "demucs",
            "linear_light_compositor": "deterministic DOM/SVG/CSS + LUT",
            "tracked_video_matte": "static matte; action shots must not use it",
            "platform_private_api": "authorized CSV export via experiment import",
        },
    }


def status(project_id: str, editspec_path: str | None = None) -> dict:
    cap = capabilities()
    quality = quality_gate.status(project_id)
    experiments = experiment.list_project(project_id)
    structural = None
    if editspec_path:
        structural = quality_gate.audit(editspec_path, visual=True)
    has_decisive_experiment = any(item["decision"] == "winner" for item in experiments)
    gates = {
        "structural_quality": bool(structural and structural["pass"]),
        "enhancements_approved": quality["pass"],
        "publication_experiment": has_decisive_experiment,
        "core_tools": "core_tools" in cap["ready"],
    }
    return {
        "project_id": project_id, "gates": gates,
        "release_ready": all(gates.values()),
        "capabilities": cap, "quality": quality,
        "experiments": experiments,
        "note": "Unavailable optional capabilities are explicit and never claimed as executed.",
    }
