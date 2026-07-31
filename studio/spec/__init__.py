"""New Demo-driven AMV pipeline schemas (spec v3.0.0).

This package is the new primary chain described in REFACTOR.md: a Demo
reference video plus a target music track produce a ``ReferenceBlueprint``
and a ``MusicTimeline``, which a rhythm/style mapper turns into an
``AMVSpec`` for the unified Resolve compiler.

It does not load or convert the old ``studio.editspec`` v2.2.0 IR. The two
chains run side by side until the new chain is verified end-to-end on real
Resolve renders, at which point the old chain is deleted (REFACTOR.md §0.1).
"""
from __future__ import annotations

SPEC_VERSION = "3.0.0"

__all__ = ["SPEC_VERSION"]
