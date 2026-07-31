"""RhythmStyleMapper + automatic sequence/motion planning for the new AMV chain.

Turns a (ReferenceBlueprint, MusicTimeline) pair into TimelineSlots and a
MotionGrammar (REFACTOR.md §7), fills slots from the asset database via
Beam Search over the existing retrieval/ranking engines (§8.5), and derives
continuous cross-cut TransitionPair motion (§9).
"""
from __future__ import annotations
