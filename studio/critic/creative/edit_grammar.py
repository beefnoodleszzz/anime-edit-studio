"""Deterministic QA for semantic cutting grammar."""
from __future__ import annotations

from collections import Counter

from pydantic import BaseModel, ConfigDict, Field

from studio.editspec.schema import EditSpec

EDIT_GRAMMAR_QA_VERSION = "edit-grammar-qa-1.0.0"


class EditGrammarCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")
    metric: str
    actual: float
    target: float
    passed: bool


class EditGrammarQAResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: str = EDIT_GRAMMAR_QA_VERSION
    clip_count: int
    motivated_cut_ratio: float = Field(..., ge=0, le=1)
    source_phase_ratio: float = Field(..., ge=0, le=1)
    relation_diversity: int = Field(..., ge=0)
    source_phase_diversity: int = Field(..., ge=0)
    repeated_relation_run: int = Field(..., ge=0)
    relation_counts: dict[str, int]
    checks: list[EditGrammarCheck]
    passed: bool


def _longest_run(values: list[str]) -> int:
    longest = current = 0
    previous = None
    for value in values:
        current = current + 1 if value == previous else 1
        previous = value
        longest = max(longest, current)
    return longest


def evaluate_edit_grammar(spec: EditSpec) -> EditGrammarQAResult:
    """Check that the plan explains joins and exact source-window phases."""
    ordered = sorted(spec.clips, key=lambda clip: clip.timeline.in_sec)
    relations = [
        clip.incoming_cut.kind
        for clip in ordered[1:]
        if clip.incoming_cut is not None
    ]
    motivated = sum(
        bool(clip.incoming_cut and clip.incoming_cut.motivation.strip())
        for clip in ordered[1:]
    )
    phased = sum(clip.source_selection is not None for clip in ordered)
    cut_count = max(0, len(ordered) - 1)
    motivated_ratio = motivated / cut_count if cut_count else 1.0
    source_phase_ratio = phased / len(ordered) if ordered else 1.0
    counts = dict(sorted(Counter(relations).items()))
    phases = {
        clip.source_selection.phase
        for clip in ordered
        if clip.source_selection is not None
    }
    diversity = len(counts)
    repeated = _longest_run(relations)
    diversity_target = min(3, max(1, cut_count))
    phase_diversity_target = 1 if len(ordered) < 8 else 3
    run_limit = max(3, round(cut_count * 0.45))
    checks = [
        EditGrammarCheck(
            metric="motivated_cut_ratio", actual=motivated_ratio,
            target=0.95, passed=motivated_ratio >= 0.95,
        ),
        EditGrammarCheck(
            metric="source_phase_ratio", actual=source_phase_ratio,
            target=0.95, passed=source_phase_ratio >= 0.95,
        ),
        EditGrammarCheck(
            metric="relation_diversity", actual=float(diversity),
            target=float(diversity_target), passed=diversity >= diversity_target,
        ),
        EditGrammarCheck(
            metric="source_phase_diversity", actual=float(len(phases)),
            target=float(phase_diversity_target),
            passed=len(phases) >= phase_diversity_target,
        ),
        EditGrammarCheck(
            metric="repeated_relation_run", actual=float(repeated),
            target=float(run_limit), passed=repeated <= run_limit,
        ),
    ]
    return EditGrammarQAResult(
        clip_count=len(ordered),
        motivated_cut_ratio=motivated_ratio,
        source_phase_ratio=source_phase_ratio,
        relation_diversity=diversity,
        source_phase_diversity=len(phases),
        repeated_relation_run=repeated,
        relation_counts=counts,
        checks=checks,
        passed=all(check.passed for check in checks),
    )


__all__ = [
    "EDIT_GRAMMAR_QA_VERSION",
    "EditGrammarCheck",
    "EditGrammarQAResult",
    "evaluate_edit_grammar",
]
