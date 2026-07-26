"""PySceneDetect based v2 shot detection with multi-frame representatives."""
from __future__ import annotations

import math
import sqlite3
from pathlib import Path

import cv2
from scenedetect import ContentDetector, SceneManager, open_video

from studio.core.database import DEFAULT_V2_DB, connect

SAMPLE_POSITIONS = (0.15, 0.325, 0.5, 0.675, 0.85)
PIPELINE_VERSION = "shots-1.0.0"


def _grab(video: cv2.VideoCapture, sec: float, target: Path) -> None:
    video.set(cv2.CAP_PROP_POS_MSEC, sec * 1000)
    ok, frame = video.read()
    if not ok:
        raise RuntimeError(f"无法在 {sec:.3f}s 抽帧")
    height, width = frame.shape[:2]
    if width > 640:
        frame = cv2.resize(frame, (640, round(height * 640 / width)), interpolation=cv2.INTER_AREA)
    if not cv2.imwrite(str(target), frame, [cv2.IMWRITE_JPEG_QUALITY, 92]):
        raise RuntimeError(f"无法写 keyframe: {target}")


def detect_shots(
    asset_id: str,
    *,
    database: Path = DEFAULT_V2_DB,
    keyframes_root: Path | None = None,
    threshold: float = 27.0,
    min_scene_sec: float = 0.4,
    force: bool = False,
) -> list[dict]:
    conn = connect(database)
    conn.row_factory = sqlite3.Row
    asset = conn.execute("SELECT * FROM assets WHERE id=?", (asset_id,)).fetchone()
    if asset is None:
        conn.close()
        raise ValueError(f"asset 不存在: {asset_id}")
    existing = conn.execute(
        "SELECT id,asset_id,idx,start_sec,end_sec,keyframe FROM shots "
        "WHERE asset_id=? ORDER BY idx",
        (asset_id,),
    ).fetchall()
    if existing and not force:
        conn.close()
        return [dict(row) for row in existing]

    media = Path(asset["proxy_path"] or asset["path"])
    fps = asset["fps_num"] / asset["fps_den"]
    scene_manager = SceneManager()
    scene_manager.add_detector(
        ContentDetector(
            threshold=threshold,
            min_scene_len=max(1, round(min_scene_sec * fps)),
        )
    )
    scene_manager.detect_scenes(open_video(str(media)))
    scenes = scene_manager.get_scene_list()
    if not scenes:
        from scenedetect import FrameTimecode

        scenes = [
            (
                FrameTimecode(0, fps),
                FrameTimecode(round(asset["duration_sec"] * fps), fps),
            )
        ]

    root = (keyframes_root or database.parent / "keyframes") / asset_id
    root.mkdir(parents=True, exist_ok=True)
    capture = cv2.VideoCapture(str(media))
    if not capture.isOpened():
        conn.close()
        raise RuntimeError(f"无法打开 proxy: {media}")
    rows = []
    try:
        for index, (start, end) in enumerate(scenes):
            start_sec, end_sec = start.get_seconds(), end.get_seconds()
            duration = max(0.001, end_sec - start_sec)
            candidates = []
            for candidate_index, position in enumerate(SAMPLE_POSITIONS):
                path = root / f"shot_{index:04d}_c{candidate_index}.jpg"
                _grab(capture, start_sec + duration * position, path)
                candidates.append(path)
            rows.append(
                {
                    "id": f"{asset_id}-{index}",
                    "asset_id": asset_id,
                    "idx": index,
                    "start_sec": round(start_sec, 6),
                    "end_sec": round(end_sec, 6),
                    "keyframe": str(candidates[len(candidates) // 2]),
                }
            )
    finally:
        capture.release()

    with conn:
        if force:
            conn.execute("DELETE FROM shots WHERE asset_id=?", (asset_id,))
        conn.executemany(
            """
            INSERT INTO shots(id,asset_id,idx,start_sec,end_sec,keyframe,analysis_version)
            VALUES (:id,:asset_id,:idx,:start_sec,:end_sec,:keyframe,NULL)
            """,
            rows,
        )
    conn.close()
    return rows


__all__ = ["PIPELINE_VERSION", "SAMPLE_POSITIONS", "detect_shots"]
