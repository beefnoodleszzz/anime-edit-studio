#!/usr/bin/env python3
"""Create a delivery EditSpec from an approved cut without changing its timing.

The delivery copy switches back to registered master media, refreshes each
shot's content-adaptive framing from the library, and can intentionally remove
all audio while preserving the beat-authored edit timeline.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from anime import db


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--width", type=int, required=True)
    parser.add_argument("--height", type=int, required=True)
    parser.add_argument("--keep-audio", action="store_true")
    args = parser.parse_args()

    spec = json.loads(args.input.read_text())
    conn = db.connect()
    missing: list[str] = []
    modes = {"crop": 0, "fit_blur": 0}
    masters: set[str] = set()
    for shot in spec.get("shots", []):
        base_shot_id = shot["id"].split("@", 1)[0]
        row = conn.execute(
            "SELECT asset_id, reframe_x, fill_mode FROM shots WHERE id=?",
            (base_shot_id,),
        ).fetchone()
        if not row:
            missing.append(base_shot_id)
            continue
        asset = db.asset_by_id(conn, row["asset_id"])
        if not asset:
            missing.append(row["asset_id"])
            continue
        master = Path(asset["path"])
        source = master if master.exists() else Path(asset["proxy_path"])
        if not source.exists():
            missing.append(str(source))
            continue
        shot["src"] = str(source)
        shot["reframe_x"] = float(row["reframe_x"] or 0.0)
        shot["fill_mode"] = row["fill_mode"] or "crop"
        modes[shot["fill_mode"]] = modes.get(shot["fill_mode"], 0) + 1
        masters.add(str(source))
    conn.close()

    if missing:
        raise SystemExit("Missing delivery media/metadata: " + ", ".join(sorted(set(missing))))
    spec["width"] = args.width
    spec["height"] = args.height
    if not args.keep_audio:
        spec["audio"] = []
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(spec, ensure_ascii=False, indent=2))
    print(json.dumps({
        "editspec": str(args.output.resolve()),
        "width": args.width,
        "height": args.height,
        "audio_layers": len(spec.get("audio", [])),
        "shots": len(spec.get("shots", [])),
        "fill_modes": modes,
        "source_files": len(masters),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
