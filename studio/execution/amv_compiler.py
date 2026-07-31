"""ResolveCompiler entry point for the new AMV chain (REFACTOR.md §4, §10).

One function, one job: for every clip in an AMVSpec, place it on the
timeline item Resolve already built (via ResolveAdapter.append_clips) and
hand it to the single Fusion compiler along with whichever TransitionPair
touches it as outgoing/incoming. No other module is allowed to build or
mutate a clip's Fusion comp.
"""
from __future__ import annotations

from studio.execution.resolve.fusion_program import FusionProgram, build_fusion_clip_program
from studio.spec.amv import AMVSpec


def compile_amv_spec(adapter, spec: AMVSpec, items_by_clip_id: dict[str, object]) -> dict[str, FusionProgram]:
    """Build every clip's Fusion program.

    ``items_by_clip_id`` maps ``Clip.id`` to the TimelineItem Resolve
    already placed on the timeline (via the existing, verified
    ``ResolveAdapter.append_clips`` / ``mark_clip`` path) — this module does
    not itself place clips or touch the timeline structure, only their
    Fusion comps.
    """
    outgoing_by_clip = {p.outgoing_clip_id: p for p in spec.transition_pairs}
    incoming_by_clip = {p.incoming_clip_id: p for p in spec.transition_pairs}

    programs: dict[str, FusionProgram] = {}
    for clip in spec.clips:
        item = items_by_clip_id.get(clip.id)
        if item is None:
            raise ValueError(f"no TimelineItem supplied for clip {clip.id}")
        programs[clip.id] = build_fusion_clip_program(
            item, clip,
            canvas=spec.canvas, timebase=spec.timebase,
            incoming_pair=incoming_by_clip.get(clip.id),
            outgoing_pair=outgoing_by_clip.get(clip.id),
        )
    return programs


__all__ = ["compile_amv_spec"]
