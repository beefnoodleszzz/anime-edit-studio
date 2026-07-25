"""Reference cut rhythm DNA extraction."""
from __future__ import annotations

import json
import math
import statistics
from pathlib import Path

import cv2

try:
    from scenedetect import ContentDetector, SceneManager, open_video
except ModuleNotFoundError:  # pragma: no cover - fallback path is covered functionally
    ContentDetector = SceneManager = open_video = None

from . import config


def analyze_reference(project_id: str, video_path: str) -> dict:
    source = Path(video_path).expanduser().resolve()
    if not source.exists():
        raise FileNotFoundError(str(source))
    if open_video and SceneManager and ContentDetector:
        video = open_video(str(source))
        fps = video.frame_rate or 24.0
        manager = SceneManager()
        manager.add_detector(ContentDetector(threshold=27.0, min_scene_len=max(int(fps * 0.25), 6)))
        manager.detect_scenes(video)
        scenes = manager.get_scene_list()
        shot_lengths = [round(end.get_seconds() - start.get_seconds(), 3) for start, end in scenes]
        cut_points = [round(start.get_seconds(), 3) for start, _ in scenes[1:]]
    else:
        fps, shot_lengths, cut_points = _fallback_scene_stats(source)

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

    rhythm_grammar = _rhythm_grammar(source, shot_lengths, cut_points)
    hook_length = shot_lengths[0] if shot_lengths else 0.0
    climax_density = round(len([length for length in shot_lengths if length <= statistics.median(shot_lengths or [1.0])]) / max(len(shot_lengths), 1), 4)
    ending_length = shot_lengths[-1] if shot_lengths else 0.0
    beat_alignment = round(_regularity_score(cut_points), 4)
    dna = {
        "project_id": project_id,
        "source_video": str(source),
        "fps": round(float(fps), 3),
        "shot_duration_distribution": shot_lengths,
        "cut_points_sec": cut_points,
        "beat_alignment": beat_alignment,
        "motion_curve": motion_curve,
        "brightness_curve": brightness_curve,
        "color_change_curve": color_curve,
        "motion_still_alternation": _alternation_score(motion_curve),
        "pre_climax_pause": round(max(shot_lengths[-3:-1], default=0.0), 3) if len(shot_lengths) >= 3 else 0.0,
        "hook_length": hook_length,
        "climax_shot_density": climax_density,
        "ending_shot_length": ending_length,
        "rhythm_grammar": rhythm_grammar,
    }
    out = config.PROJECTS / project_id / "reference-dna.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(dna, ensure_ascii=False, indent=2))
    dna["output"] = str(out)
    return dna


def _rhythm_grammar(
    source: Path,
    shot_lengths: list[float],
    cut_points: list[float],
) -> dict:
    """Convert raw scene lengths into a beat-relative pattern the director can reuse."""
    try:
        from . import beat

        beatmap = beat.analyze(str(source))
        beats = beatmap.get("beats") or []
    except Exception:  # Reference video can be silent or use an unsupported audio codec.
        beatmap, beats = {}, []

    intervals = [right - left for left, right in zip(beats, beats[1:])]
    beat_sec = statistics.median(intervals) if intervals else 0.0
    if beat_sec <= 0:
        return {
            "bpm": 0.0,
            "beat_sec": 0.0,
            "shot_units": [],
            "cut_alignment": [],
            "aligned_cut_ratio": 0.0,
            "clusters": [],
        }

    # Half-beat precision preserves deliberate syncopation without copying frame noise.
    shot_units = [max(0.5, round((length / beat_sec) * 2) / 2) for length in shot_lengths]
    alignments = []
    for cut in cut_points:
        nearest = min(beats, key=lambda value: abs(value - cut))
        delta_beats = (cut - nearest) / beat_sec
        alignments.append(round(delta_beats, 3))

    clusters = _duration_clusters(shot_units)
    aligned = sum(abs(delta) <= 0.18 for delta in alignments)
    return {
        "bpm": round(60.0 / beat_sec, 2),
        "beat_sec": round(beat_sec, 4),
        "shot_units": shot_units,
        "cut_alignment": alignments,
        "aligned_cut_ratio": round(aligned / max(len(alignments), 1), 4),
        "median_shot_units": round(statistics.median(shot_units), 2) if shot_units else 0.0,
        "clusters": clusters,
    }


def _duration_clusters(units: list[float]) -> list[dict]:
    """Summarize runs as burst/breath phrases rather than one global density score."""
    if not units:
        return []
    labels = ["burst" if value <= 1.25 else "breath" for value in units]
    result = []
    start = 0
    for index in range(1, len(labels) + 1):
        if index < len(labels) and labels[index] == labels[start]:
            continue
        values = units[start:index]
        result.append(
            {
                "kind": labels[start],
                "start_shot": start,
                "shot_count": len(values),
                "total_beats": round(math.fsum(values), 2),
            }
        )
        start = index
    return result


def _alternation_score(curve: list[float]) -> float:
    if len(curve) < 2:
        return 0.0
    switches = 0
    for left, right in zip(curve, curve[1:]):
        if (left > 0.15) != (right > 0.15):
            switches += 1
    return round(switches / max(len(curve) - 1, 1), 4)


def _regularity_score(cut_points: list[float]) -> float:
    if len(cut_points) < 3:
        return 0.0
    gaps = [right - left for left, right in zip(cut_points, cut_points[1:])]
    mean = statistics.mean(gaps)
    if mean <= 0:
        return 0.0
    deviation = statistics.pstdev(gaps)
    return max(0.0, 1.0 - deviation / mean)


def _fallback_scene_stats(source: Path) -> tuple[float, list[float], list[float]]:
    capture = cv2.VideoCapture(str(source))
    fps = capture.get(cv2.CAP_PROP_FPS) or 24.0
    previous = None
    frame_index = 0
    cuts = [0.0]
    last_cut = 0.0
    shot_lengths: list[float] = []
    min_scene_frames = max(int(fps * 0.25), 6)
    while True:
        ok, frame = capture.read()
        if not ok:
            break
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        if previous is not None:
            diff = float(cv2.absdiff(gray, previous).mean())
            current_sec = frame_index / fps
            if diff > 18.0 and frame_index - int(last_cut * fps) >= min_scene_frames:
                shot_lengths.append(round(current_sec - last_cut, 3))
                cuts.append(round(current_sec, 3))
                last_cut = current_sec
        previous = gray
        frame_index += 1
    total_sec = frame_index / fps if fps else 0.0
    if total_sec > last_cut:
        shot_lengths.append(round(total_sec - last_cut, 3))
    capture.release()
    return float(fps), shot_lengths, cuts[1:]
