"""分镜:PySceneDetect 切镜 + 每镜多帧候选(并行抽帧) + contact sheet。

每镜抽 K 个候选帧(避开首尾转场位置),后续 embed 用美学评分从中挑最优帧作代表,
避免"中点单帧定生死":中点若是运动模糊/转场/构图差帧,打标/向量/清晰度会全错。
"""
from __future__ import annotations

import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from scenedetect import ContentDetector, SceneManager, open_video

from . import config, db

# 每镜候选帧数与采样位置(镜头内相对位置,避开最前/最后的转场边缘)
CANDIDATES = 5
_SAMPLE_POS = [0.15, 0.325, 0.5, 0.675, 0.85]


def candidate_frames(kf_dir: Path, idx: int) -> list[Path]:
    """某镜已抽出的候选帧(供 embed 重排选最优)。"""
    return sorted(kf_dir.glob(f"shot_{idx:04d}_c*.jpg"))


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

    # 先算出所有(镜头, 候选)抽帧任务,再并行跑外部 ffmpeg 快速 seek(线程即可,释放 GIL)。
    jobs: list[tuple[int, Path, float]] = []
    shots: list[dict] = []
    for i, (start, end) in enumerate(scenes):
        start_sec, end_sec = start.get_seconds(), end.get_seconds()
        span = max(end_sec - start_sec, 0.01)
        for j, pos in enumerate(_SAMPLE_POS):
            jobs.append((j, kf_dir / f"shot_{i:04d}_c{j}.jpg", start_sec + span * pos))
        keyframe = kf_dir / f"shot_{i:04d}_c{len(_SAMPLE_POS) // 2}.jpg"  # 中间候选兜底
        shot = {"id": f"{asset_id}-{i}", "asset_id": asset_id, "idx": i,
                "start_sec": round(start_sec, 3), "end_sec": round(end_sec, 3),
                "keyframe": str(keyframe)}
        db.insert_shot(conn, shot)
        shots.append(shot)
    conn.commit()
    conn.close()

    def _grab(job: tuple[int, Path, float]) -> None:
        _, out, t = job
        subprocess.run(
            [ffmpeg, "-y", "-v", "error", "-ss", f"{t:.3f}", "-i", source,
             "-frames:v", "1", "-vf", "scale=320:-2", str(out)],
            check=True,
        )

    if jobs:
        with ThreadPoolExecutor(max_workers=min(len(jobs), (os.cpu_count() or 4))) as pool:
            list(pool.map(_grab, jobs))
        _contact_sheet(kf_dir, [Path(s["keyframe"]) for s in shots])
    return shots


def _contact_sheet(kf_dir: Path, keyframes: list[Path], cols: int = 6) -> Path:
    """把每镜代表帧拼成 contact sheet(软链到临时序列后 tile,避开候选帧污染)。"""
    import math
    import tempfile

    ffmpeg = config.tool("ffmpeg")
    out = kf_dir / "contact_sheet.jpg"
    frames = [k for k in keyframes if k.exists()]
    if not frames:
        return out
    cols = min(cols, len(frames))
    rows = math.ceil(len(frames) / cols)
    with tempfile.TemporaryDirectory() as tmp:
        for n, k in enumerate(frames):
            os.link(k, Path(tmp) / f"{n:05d}.jpg")
        subprocess.run(
            [ffmpeg, "-y", "-v", "error", "-pattern_type", "glob",
             "-i", str(Path(tmp) / "*.jpg"),
             "-filter_complex", f"tile={cols}x{rows}:padding=4:margin=4", str(out)],
            check=True,
        )
    return out
