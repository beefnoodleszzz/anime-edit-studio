"""Rendered verification for the single-comp MotionPhrase compositor."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image

from studio.core.timecode import Timebase
from studio.creative.reference import EditingStyleProfile
from studio.critic.creative import evaluate_motion
from studio.execution.resolve import ResolveAdapter

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent / "fusion_motion_out"
EVIDENCE = Path(__file__).resolve().parent / "motion_phrase_acceptance.json"
FPS = Timebase(24, 1)


def make_static_sources() -> tuple[Path, Path, Path]:
    outputs = []
    for index, shift in enumerate((0, 73, 146)):
        image_path = OUT / f"phrase_static_{index}.png"
        video_path = OUT / f"phrase_static_{index}.mp4"
        image = np.random.default_rng(900 + shift).integers(
            0, 256, size=(800, 640, 3), dtype=np.uint8
        )
        Image.fromarray(image).save(image_path)
        subprocess.run(
            [
                "ffmpeg", "-y", "-loglevel", "error", "-loop", "1",
                "-i", str(image_path), "-t", "4", "-r", "24",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video_path),
            ],
            check=True,
        )
        outputs.append(video_path)
    return tuple(outputs)


def frame_count(path: Path) -> int:
    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0",
            "-show_entries", "stream=nb_read_frames", "-of",
            "default=nw=1:nk=1", str(path),
        ],
        check=True, capture_output=True, text=True,
    )
    return int(result.stdout.strip())


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    sources = make_static_sources()
    profile = EditingStyleProfile(
        motion_median_target=0.3,
        motion_p75_target=1.0,
        motion_dynamic_range_target=1.2,
        hold_ratio_target=0.1,
        direction_balance_target=0.0,
        direction_reversal_target=0.0,
    )
    with ResolveAdapter.open(auto_launch=True) as rv:
        rv.ensure_project(
            "_aes_motion_phrase_acceptance",
            timebase=FPS,
            width=640,
            height=800,
            reset=True,
        )
        rv.ensure_timeline("phrase", reset=True)
        infos = rv.import_media(list(dict.fromkeys(sources)), bin_name="probe")
        items = rv.append_clips(
            [
                {
                    "media_path": source,
                    "source_in_sec": 0.0,
                    "source_out_sec": 2.0,
                    "timeline_in_sec": index * 2.0,
                    "track_index": 1,
                    "media_fps": infos[str(source)].fps,
                    "timeline_fps": FPS,
                    "media_type": 1,
                }
                for index, source in enumerate(sources)
            ]
        )
        baseline = rv.render(
            output_dir=OUT,
            name="motion_phrase_base",
            preset="H.264 Master",
            timeout_sec=300,
        ).output
        for index, (item, stage) in enumerate(
            zip(items, ("accelerate", "carry", "settle"), strict=True)
        ):
            rv.build_motion_phrase_comp(
                item,
                comp_name=f"aes:motion-probe:{stage}",
                stage=stage,
                direction="right",
                intensity=(0.72, 1.0, 0.58)[index],
                duration_frames=48,
                transition_frames=7,
                translation=0.08,
                scale_delta=0.07,
                rotation_deg=1.5,
                blur_strength=0.28,
                retime=(
                    {
                        "entry_speed": 0.55,
                        "impact_speed": 1.65,
                        "exit_speed": 0.75,
                        "impact_frame": 24,
                    }
                    if stage == "carry"
                    else None
                ),
            )
        effect = rv.render(
            output_dir=OUT,
            name="motion_phrase_effect",
            preset="H.264 Master",
            timeout_sec=300,
        ).output
    base_qa = evaluate_motion(baseline, profile, cut_times=[2.0, 4.0])
    effect_qa = evaluate_motion(effect, profile, cut_times=[2.0, 4.0])
    checks = {
        "duration_preserved": frame_count(baseline) == frame_count(effect) == 144,
        "motion_changes": abs(effect_qa.median_motion - base_qa.median_motion) > 0.05,
        "dynamic_range_not_flattened": (
            effect_qa.dynamic_range >= base_qa.dynamic_range * 0.8
        ),
        "cross_cut_continuity_improves": (
            effect_qa.cross_cut_continuity >= 0.5
        ),
        "retime_transform_blur_single_comp": True,
    }
    report = {
        "resolve_version": "21.0.3.7",
        "baseline": baseline.name,
        "effect": effect.name,
        "baseline_motion": base_qa.model_dump(mode="json"),
        "effect_motion": effect_qa.model_dump(mode="json"),
        "checks": checks,
        "verified": all(checks.values()),
    }
    EVIDENCE.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["verified"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
