"""Reference cut rhythm DNA extraction."""
from __future__ import annotations

import json
import statistics
from pathlib import Path

import cv2
from scenedetect import ContentDetector, SceneManager, open_video

from . import config


def analyze_reference(project_id: str, video_path: str) -> dict:
    source = Path(video_path).expanduser().resolve()
    if not source.exists():
        raise FileNotFoundError(str(source))
    video = open_video(str(source))
    fps = video.frame_rate or 24.0
    manager = SceneManager()
    manager.add_detector(ContentDetector(threshold=27.0, min_scene_len=max(int(fps * 0.25), 6)))
    manager.detect_scenes(video)
    scenes = manager.get_scene_list()
    shot_lengths = [round(end.get_seconds() - start.get_seconds(), 3) for start, end in scenes]
    cut_points = [round(start.get_seconds(), 3) for start, _ in scenes[1:]]

    capture = cv2.VideoCapture(str(source))
    frame_total = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    sample_step = max(frame_total // 60, 1)
    brightness_curve = []
    motion_curve = []
    color_curve = []
    previous = None
    index = 0
    while True:
        ok, frame = capture.read()
        if not ok:
            break
        if index % sample_step != 0:
            index += 1
            continue
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        brightness_curve.append(round(float(hsv[:, :, 2].mean()) / 255.0, 4))
        color_curve.append(round(float(hsv[:, :, 1].mean()) / 255.0, 4))
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        if previous is None:
            motion_curve.append(0.0)
        else:
            diff = cv2.absdiff(gray, previous)
            motion_curve.append(round(float(diff.mean()) / 255.0, 4))
        previous = gray
        index += 1
    capture.release()

    hook_length = shot_lengths[0] if shot_lengths else 0.0
    climax_density = round(len([length for length in shot_lengths if length <= statistics.median(shot_lengths or [1.0])]) / max(len(shot_lengths), 1), 4)
    ending_length = shot_lengths[-1] if shot_lengths else 0.0
    dna = {
        "project_id": project_id,
        "source_video": str(source),
        "fps": round(float(fps), 3),
        "shot_duration_distribution": shot_lengths,
        "cut_points_sec": cut_points,
        "beat_alignment": None,
        "motion_curve": motion_curve,
        "brightness_curve": brightness_curve,
        "color_change_curve": color_curve,
        "motion_still_alternation": _alternation_score(motion_curve),
        "pre_climax_pause": round(max(shot_lengths[-3:-1], default=0.0), 3) if len(shot_lengths) >= 3 else 0.0,
        "hook_length": hook_length,
        "climax_shot_density": climax_density,
        "ending_shot_length": ending_length,
    }
    out = config.PROJECTS / project_id / "reference-dna.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(dna, ensure_ascii=False, indent=2))
    dna["output"] = str(out)
    return dna


def _alternation_score(curve: list[float]) -> float:
    if len(curve) < 2:
        return 0.0
    switches = 0
    for left, right in zip(curve, curve[1:]):
        if (left > 0.15) != (right > 0.15):
            switches += 1
    return round(switches / max(len(curve) - 1, 1), 4)
