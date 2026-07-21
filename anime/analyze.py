"""逐镜头分析:亮度 / 清晰度 / 主导运动方向。可选 whisper 台词。

M1 只做无需额外模型、便宜可靠的视觉统计;VLM 打标与向量嵌入是后续升级项。
"""
from __future__ import annotations

import os
import re
import subprocess
import tempfile
from pathlib import Path

import cv2
import numpy as np

from . import config, db


def _brightness_scan(source: str) -> tuple[np.ndarray, np.ndarray]:
    """一次 ffmpeg signalstats 拿到整片逐帧平均亮度(YAVG)→ (时间, 亮度0..1)。
    比 N 点采样彻底:能抓到镜头内任意位置的暗瞬。"""
    ffmpeg = config.tool("ffmpeg")
    tmp = tempfile.mktemp(suffix=".txt")
    subprocess.run(
        [ffmpeg, "-v", "error", "-i", source,
         "-vf", f"signalstats,metadata=mode=print:file={tmp}", "-an", "-f", "null", "-"],
        check=True)
    times, yavgs, ct = [], [], None
    with open(tmp) as f:
        for line in f:
            if "pts_time:" in line:
                m = re.search(r"pts_time:([\d.]+)", line)
                ct = float(m.group(1)) if m else None
            elif "YAVG=" in line and ct is not None:
                m = re.search(r"YAVG=([\d.]+)", line)
                if m:
                    times.append(ct)
                    yavgs.append(float(m.group(1)) / 255)
    os.unlink(tmp)
    return np.array(times), np.array(yavgs)


def _frame_at(source: str, t: float, width: int = 256) -> np.ndarray | None:
    ffmpeg = config.tool("ffmpeg")
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        path = tmp.name
    subprocess.run(
        [ffmpeg, "-y", "-v", "error", "-ss", f"{t:.3f}", "-i", source,
         "-frames:v", "1", "-vf", f"scale={width}:-2", path],
        check=True,
    )
    img = cv2.imread(path)
    Path(path).unlink(missing_ok=True)
    return img


def _motion(source: str, t0: float, t1: float) -> tuple[str, float]:
    """两帧之间的稠密光流主导方向(Farneback)。"""
    a, b = _frame_at(source, t0), _frame_at(source, t1)
    if a is None or b is None:
        return ("none", 0.0)
    ga, gb = cv2.cvtColor(a, cv2.COLOR_BGR2GRAY), cv2.cvtColor(b, cv2.COLOR_BGR2GRAY)
    flow = cv2.calcOpticalFlowFarneback(ga, gb, None, 0.5, 3, 15, 3, 5, 1.2, 0)
    fx, fy = float(flow[..., 0].mean()), float(flow[..., 1].mean())
    mag = float(np.hypot(fx, fy))
    if mag < 0.3:
        return ("static", mag)
    ang = np.degrees(np.arctan2(fy, fx))
    dirs = ["right", "down-right", "down", "down-left", "left", "up-left", "up", "up-right"]
    return (dirs[int(((ang + 202.5) % 360) // 45)], round(mag, 3))


def analyze(asset_id: str, force: bool = False) -> list[dict]:
    conn = db.connect()
    asset = db.asset_by_id(conn, asset_id)
    if not asset:
        conn.close()
        raise ValueError(f"未找到素材: {asset_id}")
    source = asset["proxy_path"] or asset["path"]
    if not force:  # 缓存:已分析则直接返回
        done = conn.execute(
            "SELECT id, brightness, sharpness, motion_dir, motion_mag FROM shots "
            "WHERE asset_id=? AND brightness IS NOT NULL", (asset["id"],)).fetchall()
        if done:
            conn.close()
            return [dict(r) for r in done]
    rows = conn.execute("SELECT * FROM shots WHERE asset_id=? ORDER BY idx", (asset["id"],)).fetchall()

    scan_t, scan_y = _brightness_scan(source)   # 逐帧亮度,一次扫描
    results = []
    for r in rows:
        mid = (r["start_sec"] + r["end_sec"]) / 2
        img = _frame_at(source, mid)
        if img is None:
            continue
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        brightness = round(float(gray.mean()) / 255, 3)            # 代表值(给 slot 分类)
        sharpness = round(float(cv2.Laplacian(gray, cv2.CV_64F).var()), 1)
        # 镜头内逐帧最小亮度(抓任意位置暗瞬)
        m = (scan_t >= r["start_sec"]) & (scan_t < r["end_sec"])
        min_brightness = round(float(scan_y[m].min()), 3) if m.any() else brightness
        span = max(r["end_sec"] - r["start_sec"], 0.1)
        mdir, mmag = _motion(source, r["start_sec"] + span * 0.25, r["start_sec"] + span * 0.75)
        fields = {"brightness": brightness, "min_brightness": min_brightness,
                  "sharpness": sharpness, "motion_dir": mdir, "motion_mag": mmag}
        db.update_shot_analysis(conn, r["id"], fields)
        results.append({"id": r["id"], **fields})
    conn.close()
    return results
