"""分镜:PySceneDetect 切镜 + 抽关键帧 + contact sheet。"""
from __future__ import annotations

import subprocess
from pathlib import Path

from scenedetect import ContentDetector, SceneManager, open_video

from . import config, db


def detect(asset_id: str, threshold: float = 27.0, min_scene_sec: float = 0.4,
           force: bool = False) -> list[dict]:
    conn = db.connect()
    asset = db.asset_by_id(conn, asset_id)
    if not asset:
        conn.close()
        raise ValueError(f"未找到素材: {asset_id}")
    asset_id = asset["id"]
    if not force:  # 缓存:已分镜则跳过
        existing = conn.execute(
            "SELECT id, idx, start_sec, end_sec, keyframe FROM shots WHERE asset_id=? ORDER BY idx",
            (asset_id,)).fetchall()
        if existing:
            conn.close()
            return [dict(r) for r in existing]
    source = asset["proxy_path"] or asset["path"]
    fps = asset["fps"] or 24.0

    video = open_video(source)
    sm = SceneManager()
    sm.add_detector(ContentDetector(threshold=threshold, min_scene_len=int(min_scene_sec * fps)))
    sm.detect_scenes(video)
    scenes = sm.get_scene_list()

    kf_dir = config.KEYFRAMES / asset_id
    kf_dir.mkdir(parents=True, exist_ok=True)
    ffmpeg = config.tool("ffmpeg")

    shots: list[dict] = []
    for i, (start, end) in enumerate(scenes):
        start_sec, end_sec = start.get_seconds(), end.get_seconds()
        mid = (start_sec + end_sec) / 2
        kf = kf_dir / f"shot_{i:04d}.jpg"
        subprocess.run(
            [ffmpeg, "-y", "-v", "error", "-ss", f"{mid:.3f}", "-i", source,
             "-frames:v", "1", "-vf", "scale=320:-2", str(kf)],
            check=True,
        )
        shot = {"id": f"{asset_id}-{i}", "asset_id": asset_id, "idx": i,
                "start_sec": round(start_sec, 3), "end_sec": round(end_sec, 3),
                "keyframe": str(kf)}
        db.insert_shot(conn, shot)
        shots.append(shot)
    conn.commit()
    conn.close()

    if shots:
        _contact_sheet(kf_dir, asset_id)
    return shots


def _contact_sheet(kf_dir: Path, asset_id: str, cols: int = 6) -> Path:
    """把关键帧拼成 contact sheet。"""
    import math

    ffmpeg = config.tool("ffmpeg")
    out = kf_dir / "contact_sheet.jpg"
    n = len(list(kf_dir.glob("shot_*.jpg")))
    cols = min(cols, n)
    rows = math.ceil(n / cols)
    subprocess.run(
        [ffmpeg, "-y", "-v", "error", "-pattern_type", "glob",
         "-i", str(kf_dir / "shot_*.jpg"),
         "-filter_complex", f"tile={cols}x{rows}:padding=4:margin=4", str(out)],
        check=True,
    )
    return out
