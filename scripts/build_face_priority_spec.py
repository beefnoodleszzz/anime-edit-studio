#!/usr/bin/env python3
"""Build the approved face-priority revision from an assembled shot list."""

import json
from pathlib import Path

root = Path(__file__).resolve().parents[1]
project = root / "projects/zhanshen-linqiye-20s"
assembled = json.loads((project / "editspec.json").read_text())

shots = assembled["shots"]
for index, shot in enumerate(shots):
    shot["start_frame"] = sum(s["duration_in_frames"] for s in shots[:index])
    shot["camera_move"] = "pushIn" if index not in {5, 10, 15, 17} else "panLeft"
    shot["camera_amount"] = 0.07 if index < 16 else 0.1
    shot["fill_mode"] = "crop"
    shot["transition"] = "flash" if index in {0, 4, 8, 12, 16} else "none"
    shot["transition_intensity"] = 0.32 if shot["transition"] == "flash" else 0.0
    shot["effects"] = []
    if index in {3, 7, 11, 15}:
        shot["effects"].append({"type": "rgbSplit", "intensity": 0.12, "params": {}})
    if index == 17:
        shot["camera_move"] = "pushOut"
        shot["camera_amount"] = 0.1
        shot["ramp"] = "smooth"

duration = sum(s["duration_in_frames"] for s in shots)
spec = {
    "id": "zhanshen-linqiye-20s-face-priority",
    "fps": 60,
    "width": 3072,
    "height": 3840,
    "duration_in_frames": duration,
    "shots": shots,
    "audio": [{
        "id": "bgm",
        "src": str(root / "library/music/zhanshen/zhanshen_mask_squad_entrance_M4A_124.m4a"),
        "start_frame": 0,
        "trim_start_frames": 145,
        "gain_db": 0.0,
    }],
}

out = project / "editspec.arc.face-priority.json"
out.write_text(json.dumps(spec, ensure_ascii=False, separators=(",", ":")) + "\n")
print(json.dumps({"spec": str(out), "shots": len(shots), "duration_s": duration / 60}, ensure_ascii=False))
