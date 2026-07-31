"""Versioned, calibratable thresholds/weights for the selection stage (REFACTOR.md §9).

Numbers live in YAML under ``config/``, not scattered through code or
prompts, so they can be recalibrated against real footage without a code
change.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

REPO = Path(__file__).resolve().parent.parent.parent
THRESHOLDS_PATH = REPO / "config" / "selection_thresholds.yaml"


class TechnicalGateThresholds(BaseModel):
    model_config = ConfigDict(extra="forbid")

    minimum_window_sec: float = Field(0.30, gt=0)
    maximum_bad_frame_ratio: float = Field(0.10, ge=0, le=1)
    maximum_subtitle_frame_ratio: float = Field(0.05, ge=0, le=1)
    minimum_subject_visible_ratio: float = Field(0.70, ge=0, le=1)
    minimum_safe_crop_ratio: float = Field(0.85, ge=0, le=1)
    maximum_consecutive_unusable_frames: int = Field(3, ge=1)
    maximum_watermark_probability: float = Field(0.50, ge=0, le=1)
    maximum_black_clip_ratio: float = Field(0.40, ge=0, le=1)
    maximum_white_clip_ratio: float = Field(0.40, ge=0, le=1)
    minimum_sharpness_p10: float = Field(40.0, ge=0)
    maximum_compression_score: float = Field(0.55, ge=0, le=1)


class SelectionThresholds(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str
    technical_gate: TechnicalGateThresholds = Field(default_factory=TechnicalGateThresholds)


@lru_cache
def load_thresholds(path: Path | None = None) -> SelectionThresholds:
    import yaml

    target = path or THRESHOLDS_PATH
    if not target.exists():
        raise FileNotFoundError(f"选镜阈值配置缺失: {target}")
    return SelectionThresholds.model_validate(
        yaml.safe_load(target.read_text(encoding="utf-8"))
    )


__all__ = [
    "SelectionThresholds",
    "TechnicalGateThresholds",
    "THRESHOLDS_PATH",
    "load_thresholds",
]
