"""Retarget an approved EditSpec's delivery frame rate without changing time."""
from __future__ import annotations

from pathlib import Path

from .editspec import EditSpec


def retarget(editspec_path: str, target_fps: int) -> dict:
    if target_fps <= 0:
        raise ValueError("target_fps 必须大于 0")
    source = Path(editspec_path).resolve()
    spec = EditSpec.model_validate_json(source.read_text())
    if target_fps == spec.fps:
        raise ValueError("目标帧率与当前帧率相同")
    if target_fps % spec.fps != 0:
        raise ValueError("当前实现只支持原帧率的整数倍，以保证每个剪辑点精确不漂移")
    factor = target_fps // spec.fps
    old_fps = spec.fps
    for shot in spec.shots:
        shot.start_frame *= factor
        shot.duration_in_frames *= factor
    for layer in spec.audio:
        layer.start_frame *= factor
        layer.trim_start_frames *= factor
    spec.duration_in_frames *= factor
    spec.fps = target_fps
    out = source.with_name(f"{source.stem}.fps{target_fps}.json")
    out.write_text(spec.model_dump_json(indent=2), encoding="utf-8")
    return {"source": str(source), "editspec": str(out), "source_fps": old_fps,
            "target_fps": target_fps, "factor": factor,
            "duration_in_frames": spec.duration_in_frames, "shots": len(spec.shots)}
