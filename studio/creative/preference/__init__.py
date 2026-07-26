"""Pairwise personal preference learning."""

from .feedback import record_diff_feedback, record_final_survival
from .ranker import (
    PREFERENCE_MODEL_VERSION,
    PreferenceModel,
    PreferenceProfile,
    preference_profile,
    preference_signal,
    train_pairwise,
)

__all__ = [
    "PREFERENCE_MODEL_VERSION",
    "PreferenceModel",
    "PreferenceProfile",
    "preference_profile",
    "preference_signal",
    "record_diff_feedback",
    "record_final_survival",
    "train_pairwise",
]
