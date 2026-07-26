"""Author bounded 3D LUT artifacts without hiding decisions in prompts."""
from __future__ import annotations

import colorsys
from pathlib import Path
from typing import Callable

RGB = tuple[float, float, float]


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def _grade(
    rgb: RGB,
    *,
    contrast: float = 1.0,
    saturation: float = 1.0,
    lift: RGB = (0.0, 0.0, 0.0),
    gain: RGB = (1.0, 1.0, 1.0),
) -> RGB:
    adjusted = tuple(
        _clamp(((channel - 0.5) * contrast + 0.5 + lift[index]) * gain[index])
        for index, channel in enumerate(rgb)
    )
    h, s, v = colorsys.rgb_to_hsv(*adjusted)
    return tuple(_clamp(x) for x in colorsys.hsv_to_rgb(h, _clamp(s * saturation), v))


COLOR_RECIPES: dict[str, Callable[[RGB], RGB]] = {
    "anime_clean_v1": lambda rgb: _grade(rgb, contrast=1.04, saturation=1.03),
    "anime_high_contrast_v1": lambda rgb: _grade(
        rgb, contrast=1.18, saturation=1.08, lift=(0.012, 0.012, 0.012)
    ),
    "anime_cold_v1": lambda rgb: _grade(
        rgb, contrast=1.07, saturation=1.04,
        lift=(-0.025, 0.0, 0.035), gain=(0.96, 1.01, 1.08),
    ),
    "anime_fire_v1": lambda rgb: _grade(
        rgb, contrast=1.11, saturation=1.13,
        lift=(0.025, -0.008, -0.03), gain=(1.08, 1.01, 0.92),
    ),
    "anime_night_blue_v1": lambda rgb: _grade(
        rgb, contrast=1.06, saturation=0.94,
        lift=(-0.06, -0.035, 0.025), gain=(0.76, 0.88, 1.04),
    ),
    "red_impact_v1": lambda rgb: _grade(
        rgb, contrast=1.2, saturation=1.2,
        lift=(0.055, -0.04, -0.04), gain=(1.12, 0.82, 0.82),
    ),
}


def _write_cube(path: Path, name: str, transform: Callable[[RGB], RGB], size: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f'TITLE "{name}"',
        f"LUT_3D_SIZE {size}",
        "DOMAIN_MIN 0.0 0.0 0.0",
        "DOMAIN_MAX 1.0 1.0 1.0",
    ]
    denominator = size - 1
    for blue in range(size):
        for green in range(size):
            for red in range(size):
                out = transform((red / denominator, green / denominator, blue / denominator))
                lines.append(" ".join(f"{value:.8f}" for value in out))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def build_color_recipe_library(root: Path, *, size: int = 17) -> list[Path]:
    if size < 2:
        raise ValueError("LUT size must be >= 2")
    return [
        _write_cube(root / recipe_id / f"{recipe_id}.cube", recipe_id, transform, size)
        for recipe_id, transform in COLOR_RECIPES.items()
    ]
