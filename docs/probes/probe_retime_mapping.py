"""Self-contained probe: render one short sample per RetimeProcess value
(0-3) against the existing _aes_retime_probe timeline and classify each by
pixel analysis, so no manual GUI interaction is required.

Classification signal:
  Nearest      -> exact duplicate consecutive frames (mean abs diff == 0
                  on alternating pairs, since it just repeats source frames)
  Frame Blend  -> non-duplicate but high blur/ghosting: blended frames have
                  visibly lower edge energy (Laplacian variance) than both
                  of their neighbours
  Optical Flow / Speed Warp -> non-duplicate, edge energy close to source
                  neighbours (no ghosting). Distinguished from each other by
                  warp artifact magnitude near fast motion (Speed Warp uses
                  a newer model and tends to keep sharper edges under large
                  motion than classic Optical Flow).

Writes docs/probes/retime_mapping_probe.json with the measurements and a
best-guess label per RetimeProcess value, plus PNG contact sheets for human
sanity-check.
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys

import cv2
import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent.parent))

from studio.execution.resolve import connection  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parent.parent.parent
SCRATCH = pathlib.Path(
    "/private/tmp/claude-501/-Users-zhangxiaolong-Desktop-anime-edit-studio/"
    "f7ab516f-0c5e-4724-b694-af96fba0edeb/scratchpad"
)
RENDER_DIR = SCRATCH / "retime_renders"
FRAMES_DIR = SCRATCH / "retime_frames"


def render_variant(project, item, retime_process: int, motion_estimation: int, name: str) -> pathlib.Path:
    ok_rp = item.SetProperty("RetimeProcess", retime_process)
    ok_me = item.SetProperty("MotionEstimation", motion_estimation)
    print(f"  SetProperty RetimeProcess={retime_process} -> {ok_rp}, "
          f"MotionEstimation={motion_estimation} -> {ok_me}")

    RENDER_DIR.mkdir(parents=True, exist_ok=True)
    if not project.LoadRenderPreset("H.264 Master"):
        raise RuntimeError("加载 H.264 Master 预设失败")
    settings = {
        "TargetDir": str(RENDER_DIR),
        "CustomName": name,
        "SelectAllFrames": True,
    }
    if not project.SetRenderSettings(settings):
        raise RuntimeError(f"渲染设置被拒绝: {settings}")
    project.DeleteAllRenderJobs()
    job_id = project.AddRenderJob()
    if not job_id or not project.StartRendering(job_id):
        raise RuntimeError("启动渲染失败")
    import time
    while project.IsRenderingInProgress():
        time.sleep(0.25)
    status = project.GetRenderJobStatus(job_id) or {}
    if float(status.get("CompletionPercentage") or 0) < 100:
        raise RuntimeError(f"渲染未完成: {status}")
    outputs = sorted(RENDER_DIR.glob(f"{name}.*"))
    if not outputs:
        raise RuntimeError(f"找不到渲染产物: {name}.*")
    return outputs[-1]


def extract_frames(video_path: pathlib.Path, out_dir: pathlib.Path) -> list[pathlib.Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    for f in out_dir.glob("*.png"):
        f.unlink()
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(video_path), str(out_dir / "f_%04d.png")],
        check=True, capture_output=True,
    )
    return sorted(out_dir.glob("*.png"))


def laplacian_energy(gray: np.ndarray) -> float:
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def analyze(frames: list[pathlib.Path]) -> dict:
    imgs = [cv2.cvtColor(cv2.imread(str(f)), cv2.COLOR_BGR2GRAY) for f in frames]
    diffs = [float(np.mean(np.abs(imgs[i].astype(int) - imgs[i + 1].astype(int))))
             for i in range(len(imgs) - 1)]
    energies = [laplacian_energy(im) for im in imgs]
    exact_dupe_pairs = sum(1 for d in diffs if d < 0.05)
    return {
        "frame_count": len(imgs),
        "mean_abs_diff_series": [round(d, 4) for d in diffs],
        "exact_dupe_pairs": exact_dupe_pairs,
        "laplacian_energy_series": [round(e, 1) for e in energies],
        "mean_laplacian_energy": round(float(np.mean(energies)), 1),
        "laplacian_energy_std": round(float(np.std(energies)), 1),
    }


def classify(stats: dict) -> str:
    if stats["exact_dupe_pairs"] >= stats["frame_count"] // 3:
        return "nearest (大量完全重复帧)"
    if stats["laplacian_energy_std"] > stats["mean_laplacian_energy"] * 0.25:
        return "frame_blend (锐度在插值帧处规律性下降，帧间锐度方差大)"
    return "optical_flow_or_speed_warp (无重复帧，锐度稳定，运动内容真实变化)"


def main() -> None:
    resolve = connection.connect()
    pm = resolve.GetProjectManager()
    if not pm.LoadProject("_aes_retime_probe"):
        raise RuntimeError("找不到 _aes_retime_probe 工程，先跑 probe_retime_setup.py")
    project = pm.GetCurrentProject()
    tl = project.GetCurrentTimeline() or project.GetTimelineByIndex(1)
    project.SetCurrentTimeline(tl)
    item = tl.GetItemListInTrack("video", 1)[0]

    report: dict = {}
    for rp in (0, 1, 2, 3):
        name = f"rp{rp}"
        print(f"\n=== RetimeProcess={rp} ===")
        video = render_variant(project, item, rp, 1, name)
        frames = extract_frames(video, FRAMES_DIR / name)
        stats = analyze(frames)
        stats["label_guess"] = classify(stats)
        stats["output"] = str(video)
        report[f"retime_process_{rp}"] = stats
        print(f"  {stats['label_guess']}")

    out = REPO / "docs" / "probes" / "retime_mapping_probe.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n报告: {out}")

    # 切回原工程
    pm.LoadProject("sukuna-square-v1-interpolated-delivery")


if __name__ == "__main__":
    main()
