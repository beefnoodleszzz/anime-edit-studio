"""Deterministic StyleFingerprint for reference-video grammar."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import cv2
import numpy as np
from pydantic import BaseModel, ConfigDict, Field
from scenedetect import ContentDetector, SceneManager, open_video

from studio.core.cache import JsonCache
from studio.core.hashing import analysis_cache_key, file_sha256
from studio.editing.music import MusicMap, analyze_music

STYLE_FINGERPRINT_VERSION = "style-fingerprint-1.1.0"
MODEL = "opencv+scenedetect+librosa"
MODEL_VERSION = f"opencv-{cv2.__version__}"


class CurvePoint(BaseModel):
    model_config = ConfigDict(extra="forbid")
    time: float = Field(..., ge=0)
    value: float = Field(..., ge=0, le=1)


class StyleFingerprint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str = STYLE_FINGERPRINT_VERSION
    reference_id: str | None = None
    duration_sec: float
    shot_count: int
    shot_length_distribution: dict[str, float]
    mean_shot_length: float
    median_shot_length: float
    cut_density: float
    hard_cut_ratio: float
    transition_types: dict[str, int]
    beat_sync_ratio: float
    music_structure: list[dict]
    energy_curve: list[CurvePoint]
    brightness_curve: list[CurvePoint]
    color_progression: list[str]
    impact_points: list[float]
    silence_usage: float
    sound_effect_density: float
    slow_motion_locations: list[dict]
    shot_scale_sequence: list[float]
    motion_direction_sequence: list[str]
    camera_motion: list[str]
    speed_ramp_locations: list[dict] = Field(default_factory=list)
    visual_rhyme: list[dict] = Field(default_factory=list)
    motion_rhyme: list[dict] = Field(default_factory=list)
    confidence: dict[str, float]


def _frame(capture: cv2.VideoCapture, sec: float) -> np.ndarray:
    capture.set(cv2.CAP_PROP_POS_MSEC, sec * 1000)
    ok, image = capture.read()
    if not ok:
        raise RuntimeError(f"参考片抽帧失败: {sec:.3f}s")
    return cv2.resize(image, (320, 180), interpolation=cv2.INTER_AREA)


def _palette(image: np.ndarray) -> str:
    pixels = cv2.cvtColor(image, cv2.COLOR_BGR2RGB).reshape(-1, 3)
    color = np.median(pixels, axis=0).astype(int)
    return "#{:02x}{:02x}{:02x}".format(*color)


def _palette_vector(value: str) -> np.ndarray:
    return np.asarray([int(value[index:index + 2], 16) for index in (1, 3, 5)])


def _motion(first: np.ndarray, second: np.ndarray) -> tuple[str, float]:
    a = cv2.cvtColor(first, cv2.COLOR_BGR2GRAY)
    b = cv2.cvtColor(second, cv2.COLOR_BGR2GRAY)
    flow = cv2.calcOpticalFlowFarneback(a, b, None, 0.5, 3, 15, 3, 5, 1.2, 0)
    fx, fy = float(np.median(flow[..., 0])), float(np.median(flow[..., 1]))
    magnitude = float(np.median(np.hypot(flow[..., 0], flow[..., 1])))
    if magnitude < 0.3:
        return "static", magnitude
    directions = [
        "right", "down-right", "down", "down-left",
        "left", "up-left", "up", "up-right",
    ]
    angle = np.degrees(np.arctan2(fy, fx))
    return directions[int(((angle + 202.5) % 360) // 45)], magnitude


def _subject_scale(image: np.ndarray) -> float:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 60, 160)
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    areas = [cv2.contourArea(value) for value in contours]
    return min(1.0, max(areas, default=0) / (gray.shape[0] * gray.shape[1]) * 4)


def _compute(path: Path, reference_id: str | None, music: MusicMap) -> StyleFingerprint:
    manager = SceneManager()
    manager.add_detector(ContentDetector(threshold=27, min_scene_len=6))
    manager.detect_scenes(open_video(str(path)))
    scenes = manager.get_scene_list()
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise ValueError(f"无法打开参考片: {path}")
    fps = capture.get(cv2.CAP_PROP_FPS) or 24
    duration = capture.get(cv2.CAP_PROP_FRAME_COUNT) / fps
    if not scenes:
        from scenedetect import FrameTimecode
        scenes = [(FrameTimecode(0, fps), FrameTimecode(round(duration * fps), fps))]
    lengths, brightness, colors, scales, directions, magnitudes = [], [], [], [], [], []
    speed_ratios = []
    boundary_strength = []
    try:
        previous_end = None
        for start, end in scenes:
            start_sec, end_sec = start.seconds, end.seconds
            length = max(0.001, end_sec - start_sec)
            lengths.append(length)
            first = _frame(capture, start_sec + length * 0.2)
            middle = _frame(capture, start_sec + length * 0.5)
            last = _frame(capture, start_sec + length * 0.8)
            gray = cv2.cvtColor(middle, cv2.COLOR_BGR2GRAY)
            brightness.append(float(gray.mean()) / 255)
            colors.append(_palette(middle))
            scales.append(_subject_scale(middle))
            direction, magnitude = _motion(first, last)
            _, entry_motion = _motion(first, middle)
            _, exit_motion = _motion(middle, last)
            speed_ratios.append(
                max(entry_motion, exit_motion)
                / max(min(entry_motion, exit_motion), 0.08)
            )
            directions.append(direction)
            magnitudes.append(magnitude)
            if previous_end is not None:
                boundary_strength.append(
                    float(np.mean(cv2.absdiff(previous_end, first))) / 255
                )
            previous_end = last
    finally:
        capture.release()
    cut_times = [start.seconds for start, _ in scenes[1:]]
    synced = sum(
        1 for cut in cut_times
        if music.beats and min(abs(cut - beat) for beat in music.beats) <= 0.08
    )
    hard = sum(value >= 0.12 for value in boundary_strength)
    energy_curve = [
        CurvePoint(
            time=(section.start + section.end) / 2,
            value=section.energy,
        )
        for section in music.sections
    ]
    slow = [
        {"start": scenes[index][0].seconds, "end": scenes[index][1].seconds}
        for index, value in enumerate(magnitudes)
        if value <= np.percentile(magnitudes, 15)
    ]
    speed_ramps = [
        {
            "start": scenes[index][0].seconds,
            "end": scenes[index][1].seconds,
            "ratio": float(ratio),
            "confidence": min(0.8, 0.35 + (ratio - 2.2) * 0.12),
        }
        for index, ratio in enumerate(speed_ratios)
        if ratio >= 2.2
    ]
    visual_rhyme = []
    motion_rhyme = []
    for first_index in range(len(scenes)):
        for second_index in range(first_index + 2, len(scenes)):
            color_distance = float(
                np.linalg.norm(
                    _palette_vector(colors[first_index])
                    - _palette_vector(colors[second_index])
                )
            ) / (255 * np.sqrt(3))
            scale_delta = abs(scales[first_index] - scales[second_index])
            if color_distance <= 0.13 and scale_delta <= 0.08:
                visual_rhyme.append(
                    {
                        "first_shot": first_index,
                        "second_shot": second_index,
                        "similarity": 1.0 - 0.65 * color_distance - 0.35 * scale_delta,
                    }
                )
            magnitude_delta = abs(magnitudes[first_index] - magnitudes[second_index])
            magnitude_scale = max(magnitudes[first_index], magnitudes[second_index], 0.3)
            if (
                directions[first_index] != "static"
                and directions[first_index] == directions[second_index]
                and magnitude_delta / magnitude_scale <= 0.35
            ):
                motion_rhyme.append(
                    {
                        "first_shot": first_index,
                        "second_shot": second_index,
                        "direction": directions[first_index],
                        "similarity": 1.0 - magnitude_delta / magnitude_scale,
                    }
                )
    visual_rhyme.sort(key=lambda item: -item["similarity"])
    motion_rhyme.sort(key=lambda item: -item["similarity"])
    lengths_array = np.asarray(lengths)
    return StyleFingerprint(
        reference_id=reference_id,
        duration_sec=duration,
        shot_count=len(scenes),
        shot_length_distribution={
            "p10": float(np.percentile(lengths_array, 10)),
            "p25": float(np.percentile(lengths_array, 25)),
            "p75": float(np.percentile(lengths_array, 75)),
            "p90": float(np.percentile(lengths_array, 90)),
        },
        mean_shot_length=float(np.mean(lengths_array)),
        median_shot_length=float(np.median(lengths_array)),
        cut_density=len(cut_times) / max(duration, 1e-6),
        hard_cut_ratio=hard / len(boundary_strength) if boundary_strength else 1.0,
        transition_types={
            "hard_cut": hard,
            "soft_or_dissolve": len(boundary_strength) - hard,
        },
        beat_sync_ratio=synced / len(cut_times) if cut_times else 0.0,
        music_structure=[
            section.model_dump(mode="json") for section in music.sections
        ],
        energy_curve=energy_curve,
        brightness_curve=[
            CurvePoint(
                time=(scene[0].seconds + scene[1].seconds) / 2,
                value=value,
            )
            for scene, value in zip(scenes, brightness, strict=True)
        ],
        color_progression=colors,
        impact_points=music.impact_points,
        silence_usage=sum(value.end - value.start for value in music.silences)
        / max(duration, 1e-6),
        sound_effect_density=len(music.onsets) / max(duration, 1e-6),
        slow_motion_locations=slow,
        shot_scale_sequence=scales,
        motion_direction_sequence=directions,
        camera_motion=directions,
        speed_ramp_locations=speed_ramps,
        visual_rhyme=visual_rhyme[:24],
        motion_rhyme=motion_rhyme[:24],
        confidence={
            "hard_cut_ratio": 0.72,
            "beat_sync_ratio": 0.9,
            "sound_effect_density": 0.35,
            "slow_motion_locations": 0.3,
            "shot_scale_sequence": 0.48,
            "camera_motion": 0.4,
            "speed_ramp_locations": 0.5,
            "visual_rhyme": 0.55,
            "motion_rhyme": 0.52,
        },
    )


def analyze_reference(
    path: Path,
    *,
    cache_root: Path,
    reference_id: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> StyleFingerprint:
    digest = file_sha256(path)
    key = analysis_cache_key(
        asset_hash=digest,
        model=MODEL,
        model_version=MODEL_VERSION,
        pipeline_version=STYLE_FINGERPRINT_VERSION,
        parameters={"scene_threshold": 27, "min_scene_frames": 6},
    )
    cache = JsonCache(cache_root)
    cached = cache.get("style-fingerprint-v2", key)
    if cached:
        result = StyleFingerprint.model_validate(cached)
    else:
        music = analyze_music(path, cache_root=cache_root)
        result = _compute(path, reference_id, music)
        cache.put("style-fingerprint-v2", key, result.model_dump(mode="json"))
    if conn is not None and reference_id is not None:
        with conn:
            conn.execute(
                """
                INSERT INTO reference_videos(
                  id,style_fingerprint_json,analysis_version,updated_at
                ) VALUES (?,?,?,strftime('%Y-%m-%dT%H:%M:%fZ','now'))
                ON CONFLICT(id) DO UPDATE SET
                  style_fingerprint_json=excluded.style_fingerprint_json,
                  analysis_version=excluded.analysis_version,
                  updated_at=excluded.updated_at
                """,
                (
                    reference_id,
                    json.dumps(result.model_dump(mode="json"), ensure_ascii=False),
                    STYLE_FINGERPRINT_VERSION,
                ),
            )
    return result


__all__ = [
    "STYLE_FINGERPRINT_VERSION",
    "StyleFingerprint",
    "analyze_reference",
]
