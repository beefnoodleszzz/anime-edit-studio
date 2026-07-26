"""Creative Critic and natural-language revision share one issue contract."""

from .revision import (
    CreativeReview,
    RevisionIssue,
    SuggestedFix,
    parse_feedback,
    proposal_to_diff,
    run_creative_critic,
    select_replacement_from_db,
)

__all__ = [
    "CreativeReview",
    "RevisionIssue",
    "SuggestedFix",
    "parse_feedback",
    "proposal_to_diff",
    "run_creative_critic",
    "select_replacement_from_db",
]
