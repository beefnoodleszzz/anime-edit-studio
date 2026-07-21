#!/usr/bin/env python3
"""Copy an approved EditSpec to a new delivery canvas without touching timing/media."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--width", type=int, required=True)
    parser.add_argument("--height", type=int, required=True)
    args = parser.parse_args()
    if args.width <= 0 or args.height <= 0:
        raise SystemExit("Canvas dimensions must be positive")

    spec = json.loads(args.input.read_text())
    spec["width"] = args.width
    spec["height"] = args.height
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(spec, ensure_ascii=False, indent=2))
    print(json.dumps({"editspec": str(args.output.resolve()),
                      "width": args.width, "height": args.height,
                      "shots": len(spec.get("shots", [])),
                      "audio_layers": len(spec.get("audio", []))}, ensure_ascii=False))


if __name__ == "__main__":
    main()
