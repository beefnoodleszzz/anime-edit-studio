"""Deterministic music structure analysis."""

from .map import MUSIC_MAP_VERSION, MusicMap, analyze_music
from .motion import (
    MUSIC_MOTION_MAP_VERSION,
    MotionAccent,
    MusicMotionMap,
    build_music_motion_map,
)
from .segments import (
    MUSIC_SEGMENT_RANKING_VERSION,
    MusicSegmentCandidate,
    rank_music_segments,
)

__all__ = [
    "MUSIC_MAP_VERSION",
    "MUSIC_MOTION_MAP_VERSION",
    "MUSIC_SEGMENT_RANKING_VERSION",
    "MusicMap",
    "MusicMotionMap",
    "MotionAccent",
    "MusicSegmentCandidate",
    "analyze_music",
    "build_music_motion_map",
    "rank_music_segments",
]
