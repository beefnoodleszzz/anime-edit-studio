"""RIFE temporal enhancement for every explicitly selected EditSpec shot.

Unlike slowmo.py, this keeps picture timing and speed unchanged.  It densifies
24/25/30fps source fragments before Remotion samples them onto a 60fps timeline.
"""
from __future__ import annotations

import json
from pathlib import Path

from . import enhance


def interpolate_editspec(editspec_path: str, only_ids: list[str] | None = None,
                         target_fps: float = 60.0) -> dict:
    spec = json.loads(Path(editspec_path).read_text())
    processed = 0
    skipped = 0
    for shot in spec["shots"]:
        if only_ids and shot["id"] not in only_ids:
            continue
        mult = enhance.multiplier_for_fps(shot["src"], target_fps)
        if mult <= 1:
            skipped += 1
            continue
        clip = enhance.interpolate(shot["src"], mult=mult)
        shot["src"] = clip
        shot["source_in_sec"] = 0.0
        processed += 1

    p = Path(editspec_path)
    out_path = p.with_name(p.name[: -len(".json")] + ".rife.json")
    out_path.write_text(json.dumps(spec, ensure_ascii=False, indent=2))
    return {"editspec": str(out_path), "interpolated_shots": processed,
            "already_at_target": skipped, "target_fps": target_fps}
