import copy
import json
import re
import sqlite3
from pathlib import Path


ROOT = Path("/Users/zhangxiaolong/Desktop/anime-edit-studio")
PROJECT = ROOT / "projects/kimetsu-zenitsu-arc"
SOURCE_SPEC = PROJECT / "editspec.arc.json"
OUTPUT_SPEC = PROJECT / "editspec.arc.v2.json"
DB = ROOT / "library/engine.sqlite"


ARC = [
    # id, start, duration, speed, ramp, transition, camera, amount, effects
    ("89a293af79cf-169", 0, 120, 1.0, "none", "none", "pushIn", 0.10, ["vignette"]),
    ("89a293af79cf-103", 120, 120, 1.0, "none", "none", "pushIn", 0.08, []),
    ("89a293af79cf-130", 240, 120, 1.0, "none", "none", "panLeft", 0.07, []),
    ("89a293af79cf-164", 360, 120, 1.0, "none", "flash", "pushIn", 0.08, []),
    ("89a293af79cf-178", 480, 120, 0.75, "decel", "none", "pushOut", 0.06, ["vignette"]),
    ("89a293af79cf-211", 600, 60, 1.0, "none", "none", "panLeft", 0.06, []),
    ("89a293af79cf-213", 660, 60, 1.0, "none", "none", "pushIn", 0.08, []),
    ("89a293af79cf-215", 720, 120, 1.0, "accel", "none", "pushIn", 0.12, ["glow"]),
    ("89a293af79cf-274", 840, 120, 0.75, "decel", "zoomPunch", "pushIn", 0.14, ["glow"]),
    ("89a293af79cf-278", 960, 180, 0.5, "freezeEnd", "flash", "pushIn", 0.15, ["glow"]),
    ("6e44ae983e86-22", 1140, 30, 1.0, "none", "zoomPunch", "pushIn", 0.10, ["rgbSplit"]),
    ("89a293af79cf-293", 1170, 30, 1.0, "none", "whipLeft", "panLeft", 0.08, ["shake"]),
    ("89a293af79cf-295", 1200, 60, 1.0, "none", "none", "pushOut", 0.08, []),
    ("89a293af79cf-264", 1260, 60, 1.0, "none", "none", "pushOut", 0.06, []),
    ("89a293af79cf-257", 1320, 120, 1.0, "smooth", "none", "pushOut", 0.10, ["vignette"]),
]


def effect(name):
    defaults = {
        "glow": 0.24,
        "vignette": 0.20,
        "rgbSplit": 0.16,
        "shake": 0.12,
    }
    return {"type": name, "intensity": defaults[name], "params": {}}


with SOURCE_SPEC.open() as handle:
    spec = json.load(handle)

template = spec["shots"][0]
conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
shots = []
seen_assets = set()

for index, (
    shot_id,
    start_frame,
    duration,
    speed,
    ramp,
    transition,
    camera_move,
    camera_amount,
    effects,
) in enumerate(ARC):
    row = conn.execute(
        """
        SELECT s.id, s.asset_id, s.start_sec, s.end_sec, s.keyframe,
               s.reframe_x, s.fill_mode,
               a.path
        FROM shots s
        JOIN assets a ON a.id = s.asset_id
        WHERE s.id = ?
        """,
        (shot_id,),
    ).fetchone()
    assert row is not None, shot_id

    source_need = duration / spec["fps"] * speed
    shot_start = float(row["start_sec"])
    shot_end = float(row["end_sec"])
    candidate_match = re.search(r"_c([0-4])\.jpg$", row["keyframe"])
    assert candidate_match, row["keyframe"]
    candidate_index = int(candidate_match.group(1))
    candidate_time = shot_start + (candidate_index + 1) / 6 * (shot_end - shot_start)
    if ramp == "freezeEnd":
        source_in = candidate_time - source_need
    else:
        source_in = candidate_time - source_need / 2
    source_in = max(shot_start, min(source_in, shot_end - source_need))
    assert source_in + source_need <= float(row["end_sec"]) + 1e-6, (
        shot_id,
        source_need,
        float(row["end_sec"]) - source_in,
    )

    item = copy.deepcopy(template)
    item.update(
        {
            "id": f"{shot_id}@v2-{index}",
            "src": row["path"],
            "source_in_sec": round(source_in, 3),
            "start_frame": start_frame,
            "duration_in_frames": duration,
            "speed": speed,
            "ramp": ramp,
            "transition": transition,
            "transition_intensity": 0.28 if transition == "flash" else 0.34,
            "camera_move": camera_move,
            "camera_amount": camera_amount,
            "effects": [effect(name) for name in effects],
            "reframe_x": float(row["reframe_x"] or 0),
            "fill_mode": row["fill_mode"] or "crop",
        }
    )
    shots.append(item)
    seen_assets.add(row["asset_id"])

assert shots[0]["start_frame"] == 0
assert all(
    left["start_frame"] + left["duration_in_frames"] == right["start_frame"]
    for left, right in zip(shots, shots[1:])
)
assert shots[-1]["start_frame"] + shots[-1]["duration_in_frames"] == 1440
assert len(seen_assets) >= 2
assert 1440 / spec["fps"] >= 20

spec["id"] = "kimetsu-zenitsu-arc-v2"
spec["duration_in_frames"] = 1440
spec["shots"] = shots

with OUTPUT_SPEC.open("w") as handle:
    json.dump(spec, handle, ensure_ascii=False, indent=2)
    handle.write("\n")

print(
    json.dumps(
        {
            "output": str(OUTPUT_SPEC),
            "shots": len(shots),
            "assets": sorted(seen_assets),
            "duration_s": spec["duration_in_frames"] / spec["fps"],
        },
        ensure_ascii=False,
    )
)
