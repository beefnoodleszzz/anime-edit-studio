"""Detect where layered Fusion moves are *motivated* (W4b/W4c intent).

Parallax and occlusion cuts must never be sprayed across every cut just because
a subject exists — the same discipline the MotionPhrase admission rule enforces.
This module reads the measured subject layer plus the editorial CutRelation and
reports only the joins where the evidence supports a layered move:

  - occlusion_cut: the outgoing shot's subject sweeps across the frame with high
    coverage, so it can wipe the join instead of a dissolve.
  - parallax_25d: a shot holds a distinct foreground subject (present but not
    filling the frame) long enough to separate it from the background.

Output is *intent*, deliberately not executable Recipe refs: both recipes are
unverified, so per R3 nothing here is compiled until they are render-checked.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from studio.editspec.schema import EditSpec
from studio.execution.external_ai.subject_mask import SubjectLayer

LAYERING_VERSION = "layering-intent-1.0.0"

# An occlusion needs the subject to both cover a lot and travel a lot.
OCCLUSION_SWEEP_MIN = 0.35
OCCLUSION_COVERAGE_MIN = 0.25
# Parallax wants a subject clearly present but not filling the frame.
PARALLAX_COVERAGE_MIN = 0.12
PARALLAX_COVERAGE_MAX = 0.7
PARALLAX_MIN_DURATION_SEC = 0.7


class LayeringOpportunity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str  # occlusion_cut | parallax_25d
    clip_id: str
    recipe: str            # the (unverified) Recipe this intent would use
    confidence: float = Field(..., ge=0, le=1)
    evidence: list[str] = Field(default_factory=list)


class LayeringPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str = LAYERING_VERSION
    opportunities: list[LayeringOpportunity] = Field(default_factory=list)


def detect_layering_opportunities(
    spec: EditSpec,
    *,
    subject_layers: dict[str, SubjectLayer],
) -> LayeringPlan:
    ordered = sorted(spec.clips, key=lambda clip: clip.timeline.in_sec)
    opportunities: list[LayeringOpportunity] = []
    for index, clip in enumerate(ordered):
        layer = subject_layers.get(clip.shot_id)
        if layer is None:
            continue
        # Parallax: a held shot with a distinct foreground subject.
        if (
            PARALLAX_COVERAGE_MIN <= layer.mean_coverage <= PARALLAX_COVERAGE_MAX
            and clip.timeline.duration_sec >= PARALLAX_MIN_DURATION_SEC
        ):
            opportunities.append(LayeringOpportunity(
                kind="parallax_25d",
                clip_id=clip.id,
                recipe="parallax_25d_v1",
                confidence=round(min(1.0, layer.mean_coverage / PARALLAX_COVERAGE_MAX), 4),
                evidence=[
                    f"coverage:{layer.mean_coverage:.3f}",
                    f"duration:{clip.timeline.duration_sec:.3f}",
                ],
            ))
        # Occlusion: this clip's subject sweeps across, hiding the *next* join.
        if index + 1 < len(ordered):
            following = ordered[index + 1]
            join = following.incoming_cut
            hard_cut = following.transition.in_.recipe == "hard_cut"
            if (
                hard_cut
                and layer.horizontal_sweep >= OCCLUSION_SWEEP_MIN
                and layer.mean_coverage >= OCCLUSION_COVERAGE_MIN
            ):
                confidence = round(
                    min(1.0, 0.5 * layer.horizontal_sweep / OCCLUSION_SWEEP_MIN
                        + 0.5 * layer.mean_coverage / OCCLUSION_COVERAGE_MIN),
                    4,
                )
                evidence = [
                    f"sweep:{layer.horizontal_sweep:.3f}",
                    f"coverage:{layer.mean_coverage:.3f}",
                ]
                if join is not None:
                    evidence.append(f"cut_relation:{join.kind}")
                opportunities.append(LayeringOpportunity(
                    kind="occlusion_cut",
                    clip_id=following.id,
                    recipe="occlusion_cut_v1",
                    confidence=confidence,
                    evidence=evidence,
                ))
    return LayeringPlan(opportunities=opportunities)


__all__ = [
    "LAYERING_VERSION",
    "LayeringOpportunity",
    "LayeringPlan",
    "detect_layering_opportunities",
]
