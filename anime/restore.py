"""Conservative source restoration before interpolation and super-resolution.

This stage targets platform compression blocks and temporal mosquito noise without
claiming to recreate source detail.  It extracts each chosen EditSpec range to a
10-bit ProRes intermediate so later RIFE/Real-ESRGAN stages do not repeatedly
encode a lossy H.264 proxy.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from . import cache, config


# Validated on the project's low-bitrate gameplay source.  Keep this deliberately
# conservative: anime/game line art is more valuable than a marginal noise gain.
_FILTER = (
    "deblock=filter=weak:block=8:alpha=0.06:beta=0.04:gamma=0.04:delta=0.04,"
    "atadenoise=0a=0.01:0b=0.02:1a=0.008:1b=0.016:2a=0.008:2b=0.016:s=5,"
    "cas=strength=0.08"
)


def _restore_segment(src: str, in_sec: float, dur_sec: float) -> str:
    src_path = Path(src).resolve()
    key = cache.key("restore", cache.sha256_file(src_path), round(in_sec, 3),
                    round(dur_sec, 3), _FILTER)
    out = cache.cache_path("restore", key, ".mov")
    if out.exists():
        return str(out)

    ffmpeg = config.tool("ffmpeg")
    subprocess.run([
        ffmpeg, "-y", "-v", "error", "-ss", f"{in_sec:.3f}",
        "-t", f"{max(dur_sec, 0.1):.3f}", "-i", str(src_path), "-an",
        "-vf", _FILTER, "-c:v", "prores_videotoolbox", "-profile:v", "3",
        "-pix_fmt", "yuv422p10le", str(out),
    ], check=True)
    return str(out)


def restore_editspec(editspec_path: str, only_ids: list[str] | None = None) -> dict:
    """Restore selected source ranges and write an immutable derived EditSpec."""
    spec = json.loads(Path(editspec_path).read_text())
    fps = float(spec["fps"])
    restored = 0
    for shot in spec["shots"]:
        if only_ids and shot["id"] not in only_ids:
            continue
        shown = (float(shot["duration_in_frames"]) / fps) * float(shot["speed"])
        # Picture enhancement strips audio. Preserve the immutable original
        # source reference so the final sound stage can still build dialogue,
        # impacts and ambience from the approved source range.
        shot.setdefault("source_audio_src", shot["src"])
        shot.setdefault("source_audio_in_sec", float(shot["source_in_sec"]))
        shot["src"] = _restore_segment(shot["src"], float(shot["source_in_sec"]), shown + 0.15)
        shot["source_in_sec"] = 0.0
        restored += 1

    p = Path(editspec_path)
    out_path = p.with_name(p.name[: -len(".json")] + ".restore.json")
    out_path.write_text(json.dumps(spec, ensure_ascii=False, indent=2))
    return {"editspec": str(out_path), "restored_shots": restored,
            "filter": _FILTER}
