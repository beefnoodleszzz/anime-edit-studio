"""RhythmStyleMapper + automatic sequence/motion planning for the AMV chain.

Turns a (ReferenceBlueprint, MusicTimeline) pair into TimelineSlots and a
MotionGrammar, fills slots with a Beam Search over pre-curated shot
candidates (shots arrive by ID from the external anime-shot-library
catalog; see ``studio.planning.shot_library_import``/``candidates``), and
derives continuous cross-cut TransitionPair motion.
"""
from __future__ import annotations
