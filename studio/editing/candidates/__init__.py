"""Narrative A/B/C grouping and review decisions."""

from .service import (
    CandidateGroup,
    choose_candidate,
    create_group,
    generate_review_assets,
    precision_metrics,
    replace_with_slot_groups,
)

__all__ = [
    "CandidateGroup",
    "choose_candidate",
    "create_group",
    "generate_review_assets",
    "precision_metrics",
    "replace_with_slot_groups",
]
