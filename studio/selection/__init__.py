"""ShotWindow-based selection stage (REFACTOR.md §4).

Responsibility split from the rest of the codebase:

- ``studio.asset_intelligence`` produces long-lived, project-agnostic
  per-asset analysis (unchanged by this package).
- ``studio.selection`` (this package) turns that analysis into scored
  ``ShotWindow`` candidates and picks a globally consistent sequence of
  them for a project's ``TimelineSlot`` list.
- ``studio.planning`` consumes ``studio.selection`` output to build the
  final ``AMVSpec``.
- ``studio.execution`` only compiles an ``AMVSpec``; it makes no
  selection decisions.
"""
from __future__ import annotations

SELECTION_VERSION = "selection-1.0.0"

__all__ = ["SELECTION_VERSION"]
