"""RhythmStyleMapper + automatic sequence/motion planning for the new AMV chain.

Turns a (ReferenceBlueprint, MusicTimeline) pair into TimelineSlots and a
MotionGrammar (REFACTOR.md §7), fills slots with a Beam Search over
studio.selection's ShotWindow candidates (§16), and derives continuous
cross-cut TransitionPair motion (§9).
"""
from __future__ import annotations
