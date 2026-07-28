"""Creative Critic and natural-language revision share one issue contract."""

from .rhythm import RhythmCheck, RhythmQAResult, evaluate_rhythm
from .motion import MotionCheck, MotionQAResult, evaluate_motion
from .edit_grammar import EditGrammarCheck, EditGrammarQAResult, evaluate_edit_grammar
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
    "RhythmCheck",
    "RhythmQAResult",
    "evaluate_rhythm",
    "MotionCheck",
    "MotionQAResult",
    "evaluate_motion",
    "EditGrammarCheck",
    "EditGrammarQAResult",
    "evaluate_edit_grammar",
    "CreativeReview",
    "RevisionIssue",
    "SuggestedFix",
    "parse_feedback",
    "proposal_to_diff",
    "run_creative_critic",
    "select_replacement_from_db",
]
