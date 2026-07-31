"""Demo and target-music analysis for the new AMV chain.

Produces the two new spec objects: ``ReferenceBlueprint`` (measured Demo
cut/motion grammar) and ``MusicTimeline`` (target-track structure). Both are
pure functions over a video/audio file — no project-specific tuning, no
fixed timestamps, no character names (REFACTOR.md §0.3).
"""
from __future__ import annotations
