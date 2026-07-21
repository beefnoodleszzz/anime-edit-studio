#!/usr/bin/env python3
"""Ease the six-cut showcase hook without changing the downstream rhythm."""
from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: slow_showcase_opening.py <editspec.json>")
    path = Path(sys.argv[1]).expanduser().resolve()
    spec = json.loads(path.read_text())
    shots = spec.get("shots", [])
    if len(shots) < 6:
        raise SystemExit("showcase opening requires at least six shots")

    opening_frames = 10
    for shot in shots[:6]:
        shot["duration_in_frames"] = opening_frames

    cursor = 0
    for shot in shots:
        shot["start_frame"] = cursor
        cursor += int(shot["duration_in_frames"])
    spec["duration_in_frames"] = cursor
    path.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"editspec": str(path), "opening_frames": opening_frames,
                      "duration_in_frames": cursor, "duration_s": round(cursor / spec["fps"], 3)},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
