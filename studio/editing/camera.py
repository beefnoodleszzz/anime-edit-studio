"""Assign a per-shot camera move that rides the footage and links the cut.

The compiler has been able to render a per-shot camera curve since the
camera-curve commit, but nothing ever wrote ``clip.camera``, so first cuts
shipped with every shot frozen and the capability only lit up when a probe
hand-fed it a uniform pan.  A uniform pan is the wrong fix twice over: it fights
shots whose subject is already moving the other way, and it makes every cut carry
identically, which reads as a conveyor belt rather than the reference's mix of
drags and slams.

So the direction of each move is *derived*, from three measured things:

1. The shot's own flow (``motion_dir``/``motion_mag`` measured at ingest).  A
   shot that already moves left is panned left — the camera rides the subject
   instead of shearing against it.  A shot with no usable direction of its own
   gets a push, sized by how close the frame already is.
2. The editorial relation to the previous clip (``incoming_cut.kind``), used
   only where the footage offers no direction of its own: a match-action or
   continuation join continues the previous move, a contrast or reveal join
   reverses it.
3. Musical energy at the cut, which sets amplitude — impacts move further.

Measuring the reference first (``docs/probes/camera_flow_reference_a.json``)
overturned the obvious design.  a.mp4 carries direction through only 17.5% of
its cuts and *reverses* across 45%, with direction entropy 0.91 — near-uniform
across all eight bins.  Its pull does not come from every shot sliding the same
way; it comes from strong, highly varied per-shot movement whose direction flips
hard at the join.  So riding the footage is the rule and same-direction carry is
the exception, not the reverse — which is also what keeps a uniform pan (entropy
0.0, carry rate 1.0) from ever scoring as a pass.

Deterministic, no LLM (AGENTS.md R6): every decision traces to a measured field.
"""
from __future__ import annotations

import sqlite3
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from studio.editing.music import MusicMap
from studio.editspec.schema import EditSpec

CAMERA_ASSIGNMENT_VERSION = "camera-assignment-1.0.0"

#: Below this measured flow magnitude the shot's own direction is noise, so a
#: pan would be inventing movement the footage does not support.
MIN_RIDEABLE_MOTION = 1.2
#: Cut kinds that positively assert the motion continues across the join.
#: ``continuation`` is deliberately excluded: it is ``CutRelation.kind``'s
#: default, so an unmarked join lands there and treating it as a carry claim
#: made 40% of adjacent shots move identically — far past the reference's 17.5%.
_CARRY_KINDS = {"match_action", "graphic_match", "parallel"}
#: Cut kinds that exist to break the flow — reversing reads as the accent.
#: Unmarked joins fall here too: the reference reverses 2.6× more often than it
#: carries, so reversal is the honest default when nothing claims continuity.
_BREAK_KINDS = {
    "contrast", "reveal", "reaction", "ellipsis", "continuation", "establish",
}

_PanMove = Literal["pan_left", "pan_right", "pan_up", "pan_down"]
_OPPOSITE: dict[str, str] = {
    "pan_left": "pan_right",
    "pan_right": "pan_left",
    "pan_up": "pan_down",
    "pan_down": "pan_up",
}
#: Measured ``motion_dir`` bins to the axis the camera should ride.  Diagonals
#: resolve to their dominant component; the Transform curve is single-axis.
_DIRECTION_TO_MOVE: dict[str, _PanMove] = {
    "left": "pan_left",
    "right": "pan_right",
    "up": "pan_up",
    "down": "pan_down",
    "up-left": "pan_left",
    "down-left": "pan_left",
    "up-right": "pan_right",
    "down-right": "pan_right",
}
#: Roles whose job is to arrive somewhere, so a push reads better than a slide.
_PUSH_IN_ROLES = {"impact", "pre_drop", "character_intro"}


class CameraDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    clip_id: str
    move: str
    magnitude: float = Field(..., ge=0)
    curve: str
    basis: Literal[
        "footage_flow", "role_push", "carry_previous", "break_previous", "skipped"
    ]
    reason: str


class CameraAssignment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: str = CAMERA_ASSIGNMENT_VERSION
    assigned: int = Field(..., ge=0)
    skipped_occupied: int = Field(
        ..., ge=0, description="Fusion 槽位已被 effect/ramp/transition 占用"
    )
    carried_joins: int = Field(..., ge=0)
    broken_joins: int = Field(..., ge=0)
    move_histogram: dict[str, int]
    decisions: list[CameraDecision]


def _fusion_slot_occupied(clip) -> bool:
    """The compiler allows exactly one Fusion comp per clip.

    Effects, speed ramps and motion-blur transitions all claim it, and they
    already carry their own movement, so writing a camera move onto those clips
    would be a silent no-op in the render and a lie in the spec.
    """
    return bool(
        clip.effects
        or clip.retime.type == "speed_ramp"
        or any(
            end.recipe not in {"hard_cut", "none"}
            for end in (clip.transition.in_, clip.transition.out)
        )
    )


def _load_shot_motion(
    conn: sqlite3.Connection, shot_ids: list[str]
) -> dict[str, tuple[str | None, float]]:
    unique = sorted({value for value in shot_ids if value})
    if not unique:
        return {}
    placeholders = ",".join("?" for _ in unique)
    return {
        row["id"]: (row["motion_dir"], float(row["motion_mag"] or 0.0))
        for row in conn.execute(
            f"SELECT id,motion_dir,motion_mag FROM shots WHERE id IN ({placeholders})",
            unique,
        )
    }


def _energy_at(music: MusicMap, sec: float, *, window: float = 0.12) -> float:
    """How hard the music hits at this cut, 0..1."""
    if any(abs(sec - value) <= window for value in music.impact_points):
        return 1.0
    if music.beats and min(abs(sec - value) for value in music.beats) <= window:
        return 0.6
    return 0.3


def assign_camera_moves(
    spec: EditSpec,
    *,
    conn: sqlite3.Connection,
    music: MusicMap,
    min_magnitude: float = 0.06,
    max_magnitude: float = 0.24,
) -> tuple[EditSpec, CameraAssignment]:
    """Write a measured, cut-aware camera move onto every eligible clip."""
    if not 0 < min_magnitude <= max_magnitude <= 0.4:
        raise ValueError("magnitude 区间必须满足 0 < min <= max <= 0.4")
    updated = spec.model_copy(deep=True)
    motion_by_shot = _load_shot_motion(
        conn, [clip.shot_id for clip in updated.clips]
    )
    decisions: list[CameraDecision] = []
    histogram: dict[str, int] = {}
    carried = broken = skipped = 0
    previous_move: str | None = None

    for index, clip in enumerate(updated.clips):
        if _fusion_slot_occupied(clip):
            skipped += 1
            decisions.append(
                CameraDecision(
                    clip_id=clip.id, move="none", magnitude=0.0, curve="linear",
                    basis="skipped",
                    reason="Fusion 槽位已被 effect/speed_ramp/transition 占用",
                )
            )
            # A motion-phrase clip still moves, so the next clip may carry from
            # it; but we cannot know its axis here, so break the chain instead
            # of guessing.
            previous_move = None
            continue

        direction, magnitude_measured = motion_by_shot.get(
            clip.shot_id or "", (None, 0.0)
        )
        rideable = (
            direction in _DIRECTION_TO_MOVE
            and magnitude_measured >= MIN_RIDEABLE_MOTION
        )
        kind = clip.incoming_cut.kind if clip.incoming_cut is not None else None

        if rideable:
            move = _DIRECTION_TO_MOVE[direction]
            basis: str = "footage_flow"
            reason = (
                f"素材自身运动 {direction} (mag {magnitude_measured:.2f})，顺其而动"
            )
            # Deliberately no carry override here.  The reference reverses across
            # 45% of its cuts and carries across 17.5%; overriding measured
            # footage direction to match the previous shot would both shear
            # against the subject and collapse direction variety.
        elif previous_move is not None and kind in _BREAK_KINDS:
            move = _OPPOSITE[previous_move]
            basis = "break_previous"
            reason = f"{kind} 断点：反向于前镜 {previous_move}，切点做重音"
        elif previous_move is not None and kind in _CARRY_KINDS:
            move = previous_move
            basis = "carry_previous"
            reason = f"{kind} 承接：素材无可骑方向，延续前镜 {previous_move}"
        else:
            move = (
                "push_in"
                if (clip.role in _PUSH_IN_ROLES or clip.framing.scale >= 1.0)
                else "push_out"
            )
            basis = "role_push"
            reason = (
                f"素材静止 (mag {magnitude_measured:.2f})，按 role={clip.role} 推镜"
            )

        energy = _energy_at(music, clip.timeline.in_sec)
        magnitude = round(
            min_magnitude + (max_magnitude - min_magnitude) * energy, 6
        )
        clip.camera.move = move
        clip.camera.from_scale = 1.0
        clip.camera.to_scale = round(1.0 + magnitude, 6)
        # Ease *into* the cut on a carried join so the drag accelerates out of
        # the shot; decelerate in on a break so the accent lands.
        clip.camera.curve = "ease_in" if basis == "carry_previous" else "ease_in_out"

        if basis == "carry_previous":
            carried += 1
        elif basis == "break_previous":
            broken += 1
        histogram[move] = histogram.get(move, 0) + 1
        decisions.append(
            CameraDecision(
                clip_id=clip.id, move=move, magnitude=magnitude,
                curve=clip.camera.curve, basis=basis, reason=reason,
            )
        )
        previous_move = move if move in _OPPOSITE else None

    assignment = CameraAssignment(
        assigned=sum(1 for item in decisions if item.basis != "skipped"),
        skipped_occupied=skipped,
        carried_joins=carried,
        broken_joins=broken,
        move_histogram=dict(sorted(histogram.items())),
        decisions=decisions,
    )
    return updated, assignment


__all__ = [
    "CAMERA_ASSIGNMENT_VERSION",
    "CameraAssignment",
    "CameraDecision",
    "assign_camera_moves",
]
