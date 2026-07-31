"""Rendered QA —— hard technical gates plus Demo-relative metrics (REFACTOR.md §12.1-12.2).

Two independent judgments live in one report:

* ``technical`` / ``fusion_graph_consistent`` are hard gates. Any failure
  blocks release outright (§12.1) — nothing here can be rescued by a good
  score elsewhere.
* ``metrics`` compares the rendered output against the Demo's
  ``StyleSummary`` item by item (§12.2). This is diagnostic, not a single
  blended score: a caller must be able to see exactly which statistic
  drifted, by how much, and whether that drift was within tolerance.
"""
from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from studio.critic.technical.qa import TechnicalQAResult, run_technical_qa
from studio.spec.reference_blueprint import StyleSummary

# Tolerances are absolute differences on each StyleSummary statistic, all of
# which are already normalized to [0, 1] or "per second" scales small enough
# that one fixed tolerance per field is meaningful.
DEFAULT_TOLERANCES: dict[str, float] = {
    "cut_density": 0.35,
    "shot_duration_mean": 0.6,
    "shot_duration_median": 0.6,
    "music_sync_ratio": 0.25,
    "motion_coverage": 0.25,
    "hold_ratio": 0.25,
    "reversal_ratio": 0.25,
    "scale_motion_ratio": 0.25,
    "blur_usage": 0.3,
}


class MetricComparison(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reference: float
    actual: float
    difference: float
    tolerance: float
    passed: bool
    confidence: float = Field(1.0, ge=0.0, le=1.0)


def compare_style_summary(
    reference: StyleSummary,
    actual: StyleSummary,
    *,
    tolerances: dict[str, float] | None = None,
) -> dict[str, MetricComparison]:
    tolerances = {**DEFAULT_TOLERANCES, **(tolerances or {})}

    def _pair(name: str, ref_value: float, actual_value: float) -> MetricComparison:
        tolerance = tolerances[name]
        difference = abs(actual_value - ref_value)
        return MetricComparison(
            reference=ref_value, actual=actual_value, difference=difference,
            tolerance=tolerance, passed=difference <= tolerance,
        )

    return {
        "cut_density": _pair("cut_density", reference.cut_density, actual.cut_density),
        "shot_duration_mean": _pair(
            "shot_duration_mean",
            reference.shot_duration_distribution.get("mean", 0.0),
            actual.shot_duration_distribution.get("mean", 0.0),
        ),
        "shot_duration_median": _pair(
            "shot_duration_median",
            reference.shot_duration_distribution.get("median", 0.0),
            actual.shot_duration_distribution.get("median", 0.0),
        ),
        "music_sync_ratio": _pair(
            "music_sync_ratio",
            reference.music_sync_distribution.get("synced_ratio", 0.0),
            actual.music_sync_distribution.get("synced_ratio", 0.0),
        ),
        "motion_coverage": _pair("motion_coverage", reference.motion_coverage, actual.motion_coverage),
        "hold_ratio": _pair("hold_ratio", reference.hold_ratio, actual.hold_ratio),
        "reversal_ratio": _pair("reversal_ratio", reference.reversal_ratio, actual.reversal_ratio),
        "scale_motion_ratio": _pair(
            "scale_motion_ratio", reference.scale_motion_ratio, actual.scale_motion_ratio,
        ),
        "blur_usage": _pair("blur_usage", reference.blur_usage, actual.blur_usage),
    }


class RenderedQAReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    technical: TechnicalQAResult
    fusion_graph_consistent: bool
    metrics: dict[str, MetricComparison]
    passed: bool


def run_rendered_qa(
    path: Path,
    *,
    expected_duration: float,
    expected_width: int,
    expected_height: int,
    expected_fps,
    reference_style: StyleSummary,
    actual_style: StyleSummary,
    fusion_graph_consistent: bool,
    tolerances: dict[str, float] | None = None,
    **technical_kwargs,
) -> RenderedQAReport:
    """Combine the technical hard gate with the Demo-relative comparison.

    ``fusion_graph_consistent`` must come from an actual Resolve/Fusion
    readback (built comp nodes and curves match what the compiler wrote,
    per REFACTOR.md §10 rule 6-7) — this function never fabricates it.
    """
    technical = run_technical_qa(
        path,
        expected_duration=expected_duration,
        expected_width=expected_width,
        expected_height=expected_height,
        expected_fps=expected_fps,
        **technical_kwargs,
    )
    metrics = compare_style_summary(reference_style, actual_style, tolerances=tolerances)
    return RenderedQAReport(
        technical=technical,
        fusion_graph_consistent=fusion_graph_consistent,
        metrics=metrics,
        passed=technical.passed and fusion_graph_consistent,
    )


__all__ = [
    "DEFAULT_TOLERANCES",
    "MetricComparison",
    "RenderedQAReport",
    "compare_style_summary",
    "run_rendered_qa",
]
