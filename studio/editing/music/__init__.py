"""Deterministic music structure analysis."""

from .map import MUSIC_MAP_VERSION, MusicMap, analyze_music
from .segments import (
    MUSIC_SEGMENT_RANKING_VERSION,
    MusicSegmentCandidate,
    rank_music_segments,
)

__all__ = [
    "MUSIC_MAP_VERSION",
    "MUSIC_SEGMENT_RANKING_VERSION",
    "MusicMap",
    "MusicSegmentCandidate",
    "analyze_music",
    "rank_music_segments",
]
