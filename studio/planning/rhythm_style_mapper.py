"""RhythmStyleMapper (REFACTOR.md §7): ReferenceBlueprint + MusicTimeline -> TimelineSlots.

Two modes:

* Exact Replica (§7.1) — no independent target track, or the target track
  *is* the Demo's own audio (``music.source_hash == blueprint.music_timeline_ref``).
  Slot boundaries are the Demo's real, measured cut times; nothing here is
  hardcoded per-Demo, it is read straight off the blueprint's own shots.

* Style Transfer (§7.2) — a different target track. Cut *times* come from
  the target MusicTimeline's own accents (never a linear stretch of the
  Demo's timeline); what migrates from the Demo is its statistical grammar:
  cut density, energy-per-slot shape, and the entry-motion distribution
  (carry/reverse/reset ratios) from ``style_summary``. Selection of which
  accents become cuts is deterministic given the two source hashes, so
  identical inputs reproduce the identical slot plan (§8.5 "相同输入必须得到稳定结果").
"""
from __future__ import annotations

import hashlib
import random
from typing import Literal

from studio.planning.slots import EntryMotion, TimelineSlot
from studio.spec.music_timeline import MusicTimeline
from studio.spec.reference_blueprint import ReferenceBlueprint

MappingMode = Literal["exact_replica", "style_transfer"]

_ACCENT_PRIORITY = {
    "section_boundary": 5,
    "downbeat": 4,
    "impact": 4,
    "break_exit": 3,
    "riser_peak": 3,
    "break_entry": 2,
    "beat": 1,
    "silence_hit": 1,
}


def choose_mode(blueprint: ReferenceBlueprint, music: MusicTimeline) -> MappingMode:
    if blueprint.music_timeline_ref is not None and blueprint.music_timeline_ref == music.source_hash:
        return "exact_replica"
    return "style_transfer"


def _entry_motion_distribution(blueprint: ReferenceBlueprint) -> list[EntryMotion]:
    summary = blueprint.style_summary
    weights: dict[EntryMotion, float] = {
        "reverse": summary.reversal_ratio,
        "carry": max(0.0, 1.0 - summary.reversal_ratio - summary.hold_ratio),
        "reset": summary.hold_ratio * 0.5,
        "none": summary.hold_ratio * 0.5,
    }
    total = sum(weights.values()) or 1.0
    return [kind for kind, weight in weights.items() for _ in range(max(1, round(weight / total * 20)))]


def _exact_replica(blueprint: ReferenceBlueprint) -> list[TimelineSlot]:
    relation_by_cut_sec = {c.sec: c.relation for c in blueprint.cuts}
    slots = []
    for shot in blueprint.shots:
        relation = relation_by_cut_sec.get(shot.start_sec, "none")
        entry_motion: EntryMotion = relation if relation in ("carry", "reverse", "reset") else "none"
        slots.append(
            TimelineSlot(
                index=shot.index,
                start_sec=shot.start_sec,
                duration_sec=shot.duration_sec,
                target_energy=shot.visual_energy,
                hold=shot.global_motion_estimate.value < 0.4,
                entry_motion=entry_motion,
            )
        )
    return slots


def _select_boundaries(music: MusicTimeline, count: int, seed: int) -> list[tuple[float, str]]:
    candidates = sorted(
        {(a.sec, a.kind) for a in music.accents} | {(0.0, "section_boundary")},
        key=lambda item: item[0],
    )
    if not candidates:
        return [(0.0, "section_boundary")]
    count = max(1, min(count, len(candidates)))
    scored = sorted(
        candidates,
        key=lambda item: (-_ACCENT_PRIORITY.get(item[1], 0), item[0]),
    )
    chosen = sorted(scored[:count], key=lambda item: item[0])
    rng = random.Random(seed)
    rng.shuffle(chosen)  # only affects tie-break order feeding entry-motion draw below
    return sorted(chosen, key=lambda item: item[0])


def _style_transfer(blueprint: ReferenceBlueprint, music: MusicTimeline) -> list[TimelineSlot]:
    target_cut_count = max(1, round(blueprint.style_summary.cut_density * music.duration_sec))
    seed = int(
        hashlib.sha256(f"{blueprint.source_hash}:{music.source_hash}".encode()).hexdigest()[:8], 16
    )
    boundaries = _select_boundaries(music, target_cut_count, seed)
    boundary_secs = sorted({sec for sec, _kind in boundaries} | {0.0, music.duration_sec})

    energies = [shot.visual_energy for shot in blueprint.shots] or [0.5]
    motions = _entry_motion_distribution(blueprint)
    rng = random.Random(seed)

    slots = []
    for index, (start, end) in enumerate(zip(boundary_secs, boundary_secs[1:])):
        duration = end - start
        if duration <= 0:
            continue
        relative_position = index / max(1, len(boundary_secs) - 2)
        energy_index = min(len(energies) - 1, int(relative_position * len(energies)))
        kind_for_start = next((kind for sec, kind in boundaries if sec == start), None)
        slots.append(
            TimelineSlot(
                index=len(slots),
                start_sec=start,
                duration_sec=duration,
                target_energy=energies[energy_index],
                hold=rng.random() < blueprint.style_summary.hold_ratio,
                entry_motion=rng.choice(motions) if index > 0 else "none",
                music_event_sec=start if kind_for_start else None,
                music_event_kind=kind_for_start,
            )
        )
    return slots


def map_rhythm_to_slots(
    blueprint: ReferenceBlueprint, music: MusicTimeline, *, mode: MappingMode | None = None,
) -> list[TimelineSlot]:
    """Produce TimelineSlots. ``mode`` is auto-detected from source hashes if omitted."""
    resolved_mode = mode or choose_mode(blueprint, music)
    if resolved_mode == "exact_replica":
        return _exact_replica(blueprint)
    return _style_transfer(blueprint, music)


__all__ = ["MappingMode", "choose_mode", "map_rhythm_to_slots"]
