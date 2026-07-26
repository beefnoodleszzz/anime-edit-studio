"""Rendered-output verification for Fusion speed ramp and Whip/Blur recipes."""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

from PIL import Image, ImageChops, ImageStat

from studio.core.timecode import Timebase
from studio.execution.recipes import RecipeRegistry
from studio.execution.resolve import (
    ResolveAdapter,
    apply_speed_ramp_recipe,
    apply_whip_blur_side,
)


ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent / "fusion_motion_out"
PROJECT = "_aes_fusion_motion_acceptance"
FPS = Timebase(24, 1)


def run(*args: str) -> None:
    subprocess.run(args, check=True, capture_output=True)


def make_sources() -> tuple[Path, Path]:
    OUT.mkdir(parents=True, exist_ok=True)
    first = OUT / "probe_a.mp4"
    second = OUT / "probe_b_motion.mp4"
    run(
        "ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
        "-i", "testsrc2=size=640x800:rate=24:duration=4",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(first),
    )
    run(
        "ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
        "-i", "testsrc2=size=640x800:rate=24:duration=4",
        "-vf", "hue=h=120:s=1.35",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(second),
    )
    return first, second


def frame_digest(video: Path, frame: int) -> str:
    target = OUT / f"{video.stem}-{frame}.png"
    run(
        "ffmpeg", "-y", "-loglevel", "error", "-i", str(video),
        "-vf", f"select=eq(n\\,{frame})", "-frames:v", "1", str(target),
    )
    return hashlib.sha256(target.read_bytes()).hexdigest()


def frame_mean_abs_delta(first_video: Path, second_video: Path, frame: int) -> float:
    frame_digest(first_video, frame)
    frame_digest(second_video, frame)
    first = Image.open(OUT / f"{first_video.stem}-{frame}.png").convert("RGB")
    second = Image.open(OUT / f"{second_video.stem}-{frame}.png").convert("RGB")
    return sum(ImageStat.Stat(ImageChops.difference(first, second)).mean) / 3


def frame_count(video: Path) -> int:
    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0",
            "-show_entries", "stream=nb_read_frames", "-of", "default=nw=1:nk=1",
            str(video),
        ],
        check=True, capture_output=True, text=True,
    )
    return int(result.stdout.strip())


def render(rv: ResolveAdapter, name: str) -> Path:
    return rv.render(
        output_dir=OUT,
        name=name,
        preset="H.264 Master",
        timeout_sec=300,
    ).output


def append(rv: ResolveAdapter, sources: tuple[Path, ...], duration: float) -> list:
    infos = rv.import_media(list(sources), bin_name="probe")
    return rv.append_clips(
        [
            {
                "media_path": source,
                "source_in_sec": 0.0,
                "source_out_sec": duration,
                "timeline_in_sec": index * duration,
                "track_index": 1,
                "media_fps": infos[str(source)].fps,
                "timeline_fps": FPS,
                "media_type": 1,
            }
            for index, source in enumerate(sources)
        ]
    )


def main() -> None:
    source_a, source_b = make_sources()
    registry = RecipeRegistry.load()
    report: dict = {"project": PROJECT}
    with ResolveAdapter.open(auto_launch=True) as rv:
        report["resolve_version"] = rv.version
        rv.ensure_project(
            PROJECT, timebase=FPS, width=640, height=800, reset=True
        )

        rv.ensure_timeline("speed", reset=True)
        speed_item = append(rv, (source_a,), 4.0)[0]
        speed_base = render(rv, "fusion_speed_base")
        apply_speed_ramp_recipe(
            rv,
            registry,
            item=speed_item,
            duration_frames=96,
            entry_speed=0.45,
            impact_speed=1.8,
            exit_speed=0.65,
            impact_frame=48,
        )
        speed_effect = render(rv, "fusion_speed_effect")
        speed_checks = {
            "duration_preserved": frame_count(speed_base) == frame_count(speed_effect) == 96,
            "entry_changed": frame_digest(speed_base, 12) != frame_digest(speed_effect, 12),
            "impact_changed": frame_digest(speed_base, 48) != frame_digest(speed_effect, 48),
            "exit_changed": frame_digest(speed_base, 84) != frame_digest(speed_effect, 84),
        }
        report["timespeed_recipe"] = {
            "baseline": speed_base.name,
            "effect": speed_effect.name,
            "checks": speed_checks,
            "verified": all(speed_checks.values()),
        }

        rv.ensure_timeline("transition", reset=True)
        left, right = append(rv, (source_a, source_b), 2.0)
        transition_base = render(rv, "fusion_transition_base")
        for item, side in ((left, "out"), (right, "in")):
            apply_whip_blur_side(
                rv,
                registry,
                item=item,
                side=side,
                duration_frames=48,
                transition_frames=7,
                params={"length": 0.24, "angle": 0.0},
            )
        transition_effect = render(rv, "fusion_transition_effect")
        transition_checks = {
            "duration_preserved": (
                frame_count(transition_base) == frame_count(transition_effect) == 96
            ),
            "left_identity_preserved": (
                frame_mean_abs_delta(transition_base, transition_effect, 12) < 1.0
            ),
            "right_identity_preserved": (
                frame_mean_abs_delta(transition_base, transition_effect, 84) < 1.0
            ),
            "left_cut_side_changed": (
                frame_digest(transition_base, 45) != frame_digest(transition_effect, 45)
            ),
            "right_cut_side_changed": (
                frame_digest(transition_base, 50) != frame_digest(transition_effect, 50)
            ),
        }
        report["transition"] = {
            "baseline": transition_base.name,
            "effect": transition_effect.name,
            "checks": transition_checks,
            "verified": all(transition_checks.values()),
        }

    report["verified"] = all(
        report[key]["verified"] for key in ("timespeed_recipe", "transition")
    )
    evidence = Path(__file__).resolve().parent / "fusion_motion_acceptance.json"
    evidence.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["verified"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
