"""Representative-interval selection and bounded parameter search (REFACTOR.md §12.3).

The optimizer never touches analysis algorithms, adds character-specific
conditions, or piles on extra Flash/Shake/Blur to mask a bad cut — it only
walks the fixed nine-parameter gain space, re-renders the same representative
window, and re-runs QA. Rendering and QA are injected callables so this stays
testable without a live Resolve connection; wiring a real render/QA pair into
these callables is a Commit 6/7 concern.
"""
from __future__ import annotations

from typing import Callable

from pydantic import BaseModel, ConfigDict, Field

from studio.qa.rendered_qa import MetricComparison, RenderedQAReport
from studio.spec.amv import AMVSpec
from studio.spec.music_timeline import MusicTimeline

ALLOWED_PARAMS = (
    "translation_gain",
    "zoom_gain",
    "rotation_gain",
    "motion_blur_gain",
    "directional_blur_gain",
    "anticipation_scale",
    "release_scale",
    "overshoot_gain",
    "hold_motion_gain",
)

DEFAULT_PARAMS: dict[str, float] = {name: 1.0 for name in ALLOWED_PARAMS}

# Only these StyleSummary metrics have a tunable counterpart among the nine
# allowed gains. A drift in cut_density, shot-duration distribution, or
# music-sync ratio cannot be fixed by re-gaining motion curves — per §12.3
# that kind of failure means swapping the worst slot's asset, not more
# rounds of this search.
METRIC_PARAM_MAP: dict[str, str] = {
    "motion_coverage": "translation_gain",
    "hold_ratio": "hold_motion_gain",
    "reversal_ratio": "overshoot_gain",
    "scale_motion_ratio": "zoom_gain",
    "blur_usage": "directional_blur_gain",
}

MAX_ROUNDS = 4
PARAM_STEP = 0.15
MIN_PARAM = 0.1


class OptimizationRound(BaseModel):
    model_config = ConfigDict(extra="forbid")
    round_index: int = Field(..., ge=1)
    params: dict[str, float]
    report: RenderedQAReport


def pick_representative_interval(
    spec: AMVSpec,
    music: MusicTimeline,
    *,
    min_duration: float = 3.0,
    max_duration: float = 5.0,
) -> tuple[float, float] | None:
    """First window that has >=2 cuts, >=1 accent, cross-shot motion, and a landing.

    Returns ``None`` when no such window exists (e.g. a very short spec) —
    callers must not fabricate a window in that case.
    """
    cut_secs = sorted(pair.cut_sec for pair in spec.transition_pairs)
    if len(cut_secs) < 2:
        return None
    accent_secs = sorted(accent.sec for accent in music.accents)

    for start in cut_secs:
        for duration in (min_duration, (min_duration + max_duration) / 2, max_duration):
            end = start + duration
            if end > spec.duration_sec:
                continue
            cuts_in_window = [c for c in cut_secs if start <= c <= end]
            if len(cuts_in_window) < 2:
                continue
            if not any(start <= a <= end for a in accent_secs):
                continue
            clips_in_window = [
                clip for clip in spec.clips
                if clip.timeline.in_sec < end
                and clip.timeline.in_sec + clip.timeline.duration_sec > start
            ]
            has_cross_shot_motion = any(
                len(clip.motion.transform_keyframes) >= 2 for clip in clips_in_window
            )
            if not has_cross_shot_motion:
                continue
            has_stable_landing = any(
                pair.cut_sec in cuts_in_window and pair.confidence >= 0.3
                for pair in spec.transition_pairs
            )
            if not has_stable_landing:
                continue
            return (start, end)
    return None


def _direction(metric: MetricComparison) -> float:
    return 1.0 if metric.actual < metric.reference else -1.0


def optimize_test_interval(
    spec: AMVSpec,
    interval: tuple[float, float],
    *,
    render_fn: Callable[[AMVSpec, dict[str, float], tuple[float, float]], object],
    qa_fn: Callable[[object], RenderedQAReport],
    max_rounds: int = MAX_ROUNDS,
    param_step: float = PARAM_STEP,
) -> list[OptimizationRound]:
    """Render/QA the representative window, adjusting one tunable gain per round.

    Stops early once QA passes, or once every failing metric falls outside
    ``METRIC_PARAM_MAP`` (no gain in the allowed set can move it further).
    """
    params = dict(DEFAULT_PARAMS)
    rounds: list[OptimizationRound] = []

    for round_index in range(1, max_rounds + 1):
        rendered = render_fn(spec, params, interval)
        report = qa_fn(rendered)
        rounds.append(OptimizationRound(round_index=round_index, params=dict(params), report=report))
        if report.passed:
            break

        tunable_failures = {
            name: metric
            for name, metric in report.metrics.items()
            if name in METRIC_PARAM_MAP and not metric.passed
        }
        if not tunable_failures:
            break

        worst_name = max(
            tunable_failures,
            key=lambda name: tunable_failures[name].difference / max(tunable_failures[name].tolerance, 1e-6),
        )
        param_name = METRIC_PARAM_MAP[worst_name]
        params[param_name] = max(
            MIN_PARAM, params[param_name] + _direction(tunable_failures[worst_name]) * param_step,
        )

    return rounds


__all__ = [
    "ALLOWED_PARAMS",
    "DEFAULT_PARAMS",
    "METRIC_PARAM_MAP",
    "OptimizationRound",
    "optimize_test_interval",
    "pick_representative_interval",
]
