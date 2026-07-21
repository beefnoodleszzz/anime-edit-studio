"""Create a portrait spec that presents 16:9 material sideways, full-bleed."""
from __future__ import annotations

from pathlib import Path

from .editspec import EditSpec


def create(editspec_path: str, *, direction: str = "cw", overscan: float = 1.0,
           shift_x: float = 0.0, shift_y: float = 0.0,
           width: int = 2160, height: int = 3840) -> dict:
    """Write a derivative spec for the sideways full-bleed visual grammar.

    The renderer owns rotation before fitting. `overscan` is reserved for an
    authorized edge-watermark cleanup; clean material stays at 1.0.
    """
    if direction not in {"cw", "ccw"}:
        raise ValueError("direction 必须是 cw 或 ccw")
    if not 1.0 <= overscan <= 1.5:
        raise ValueError("overscan 必须在 1.0 到 1.5 之间")
    if width <= 0 or height <= width:
        raise ValueError("横置全屏需要纵向画布，width/height 必须为正且 height > width")

    source = Path(editspec_path).resolve()
    spec = EditSpec.model_validate_json(source.read_text())
    spec.width, spec.height = width, height
    for shot in spec.shots:
        shot.fill_mode = f"sideways_{direction}"
        shot.reframe_x = 0.0
        shot.transform.rotate = 0.0
        shot.transform.x = shift_x
        shot.transform.y = shift_y
        shot.transform.scale = overscan

    out = source.with_name(f"{source.stem}.sideways-{direction}.json")
    out.write_text(spec.model_dump_json(indent=2), encoding="utf-8")
    return {
        "source": str(source),
        "editspec": str(out),
        "direction": direction,
        "overscan": overscan,
        "shift_x": shift_x,
        "shift_y": shift_y,
        "width": width,
        "height": height,
        "shots": len(spec.shots),
    }
