"""Deterministic Action Sync: land a measured action peak on a drum marker.

Cut Sync (the strict drum-locked workflow) already guarantees the *source
change* sits on the marker.  Action Sync is the second half: the sword actually
*landing* on the beat, not merely a new shot starting there.

Given a clip whose source window contains a measured action peak, this solver
produces a ``speed_ramp`` :class:`Retime` whose rendered peak coincides with the
target marker.  It reuses the exact source-time model the compiler and
``ResolveAdapter.configure_speed_ramp`` implement — a three-point velocity curve
that Fusion normalizes so the last timeline frame maps to the last source frame.
Because that normalization is scale-invariant, the peak's rendered position is
fixed entirely by the ratio ``first_area / total_area``; this module solves the
entry speed that hits that ratio, holding the impact and exit speeds at
stylistic defaults (a slow-motion hold on the hit, a fast exit).

Everything here is pure arithmetic on frame counts, unit-testable without
Resolve, and honest: when the required entry speed clamps against its bounds the
solver reports the residual so the acceptance table shows the real error.
"""
from __future__ import annotations

from dataclasses import dataclass

from studio.core.timecode import Timebase
from studio.editspec.schema import Retime

ACTION_SYNC_VERSION = "action-sync-1.0.0"

# Stylistic defaults for the two speeds we do not solve for.  Slow the hit
# (impact hold) and exit fast; the entry speed is solved to place the peak.
DEFAULT_IMPACT_SPEED = 0.5
DEFAULT_EXIT_SPEED = 1.3
# Fusion truncates on absurd retimes; keep the solved entry speed sane.
ENTRY_SPEED_MIN = 0.15
ENTRY_SPEED_MAX = 4.0
# Below this we treat the peak as already on the marker and stay at constant
# speed rather than inventing a ramp that buys nothing.
MIN_SYNC_FRAMES = 1


@dataclass(frozen=True)
class ActionSyncSolution:
    retime: Retime
    impact_frame: int
    peak_source_frame: int
    # Signed timeline-frame error: where the peak lands minus the target
    # marker, in delivery frames (0 when the hit sits exactly on the beat).
    residual_frames: int
    clamped: bool
    reason: str

    def to_dict(self) -> dict:
        return {
            "version": ACTION_SYNC_VERSION,
            "type": self.retime.type,
            "entry_speed": round(self.retime.entry_speed, 6),
            "impact_speed": round(self.retime.impact_speed, 6),
            "exit_speed": round(self.retime.exit_speed, 6),
            "impact_at_sec": self.retime.impact_at_sec,
            "impact_frame": self.impact_frame,
            "peak_source_frame": self.peak_source_frame,
            "residual_frames": self.residual_frames,
            "clamped": self.clamped,
            "reason": self.reason,
        }


def _source_position(
    time: float,
    *,
    duration_frames: int,
    impact_frame: int,
    entry_speed: float,
    impact_speed: float,
    exit_speed: float,
) -> float:
    """Source frame shown at timeline frame ``time`` under the normalized curve.

    Mirrors ``ResolveAdapter.configure_speed_ramp`` exactly so the solver and
    the executor agree on where every source frame lands on the timeline.
    """
    tail = duration_frames - 1 - impact_frame
    first_slope = (impact_speed - entry_speed) / impact_frame
    second_slope = (exit_speed - impact_speed) / max(tail, 1)
    first_area = entry_speed * impact_frame + 0.5 * first_slope * impact_frame**2
    total_area = first_area + impact_speed * tail + 0.5 * second_slope * tail**2
    if total_area <= 0:
        return float(time)
    scale = (duration_frames - 1) / total_area
    if time <= impact_frame:
        return scale * (entry_speed * time + 0.5 * first_slope * time * time)
    delta = time - impact_frame
    return scale * (
        first_area + impact_speed * delta + 0.5 * second_slope * delta * delta
    )


def _timeline_frame_of_source(
    peak_source_frame: int,
    *,
    duration_frames: int,
    impact_frame: int,
    entry_speed: float,
    impact_speed: float,
    exit_speed: float,
) -> int:
    """Scan the curve for the timeline frame that renders the peak source frame.

    The acceptance error is a *timeline* distance (how far from the beat the hit
    visibly lands), so we invert the monotonic source-time curve by a cheap scan
    over the clip's frames rather than approximating with a local speed.
    """
    best_frame = impact_frame
    best_gap = float("inf")
    for time in range(duration_frames):
        source = _source_position(
            float(time),
            duration_frames=duration_frames,
            impact_frame=impact_frame,
            entry_speed=entry_speed,
            impact_speed=impact_speed,
            exit_speed=exit_speed,
        )
        gap = abs(source - peak_source_frame)
        if gap < best_gap:
            best_gap, best_frame = gap, time
    return best_frame


def solve_action_sync(
    *,
    timebase: Timebase,
    clip_duration_sec: float,
    marker_offset_sec: float,
    peak_source_offset_sec: float,
    impact_speed: float = DEFAULT_IMPACT_SPEED,
    exit_speed: float = DEFAULT_EXIT_SPEED,
) -> ActionSyncSolution:
    """Solve a speed ramp placing ``peak_source_offset`` on ``marker_offset``.

    ``marker_offset_sec`` is the marker position measured from the clip's start
    on the timeline; ``peak_source_offset_sec`` is the measured peak measured
    from the start of the clip's *source* window.  Both are clip-relative.
    """
    duration_frames = timebase.to_frames(clip_duration_sec)
    if duration_frames < 3:
        return ActionSyncSolution(
            retime=Retime(type="constant", speed=1.0),
            impact_frame=0,
            peak_source_frame=0,
            residual_frames=0,
            clamped=False,
            reason="clip too short for a speed ramp",
        )
    # The marker is the timeline frame where the peak must appear.
    impact_frame = max(1, min(timebase.to_frames(marker_offset_sec), duration_frames - 2))
    peak_source_frame = max(
        0, min(timebase.to_frames(peak_source_offset_sec), duration_frames - 1)
    )
    shift = peak_source_frame - impact_frame
    if abs(shift) < MIN_SYNC_FRAMES:
        return ActionSyncSolution(
            retime=Retime(type="constant", speed=1.0),
            impact_frame=impact_frame,
            peak_source_frame=peak_source_frame,
            residual_frames=0,
            clamped=False,
            reason="peak already on marker at constant speed",
        )
    tail = duration_frames - 1 - impact_frame
    target_ratio = peak_source_frame / (duration_frames - 1)
    # r = m(e+i) / [m(e+i)+tail(i+x)]  ->  e = k*tail*(i+x)/m - i, k = r/(1-r).
    if target_ratio >= 1.0:
        entry_unclamped = ENTRY_SPEED_MAX
    elif target_ratio <= 0.0:
        entry_unclamped = ENTRY_SPEED_MIN
    else:
        k = target_ratio / (1.0 - target_ratio)
        entry_unclamped = k * tail * (impact_speed + exit_speed) / impact_frame - impact_speed
    entry_speed = max(ENTRY_SPEED_MIN, min(entry_unclamped, ENTRY_SPEED_MAX))
    # Where the peak actually lands on the timeline, and how far that is from
    # the target marker in delivery frames — the honest acceptance error.
    landed_frame = _timeline_frame_of_source(
        peak_source_frame,
        duration_frames=duration_frames,
        impact_frame=impact_frame,
        entry_speed=entry_speed,
        impact_speed=impact_speed,
        exit_speed=exit_speed,
    )
    residual = landed_frame - impact_frame
    # "Clamped" means the retime could not seat the peak on the beat: either the
    # solved entry speed hit a bound, or the target sat past a reachable ratio.
    clamped = abs(entry_speed - entry_unclamped) > 1e-9 or abs(residual) > 1
    retime = Retime(
        type="speed_ramp",
        entry_speed=round(entry_speed, 6),
        impact_speed=round(impact_speed, 6),
        exit_speed=round(exit_speed, 6),
        impact_at_sec=round(float(timebase.to_seconds(impact_frame)), 6),
        interpolation="optical_flow",
    )
    return ActionSyncSolution(
        retime=retime,
        impact_frame=impact_frame,
        peak_source_frame=peak_source_frame,
        residual_frames=residual,
        clamped=clamped,
        reason="entry speed clamped" if clamped else "solved",
    )


__all__ = [
    "ACTION_SYNC_VERSION",
    "ActionSyncSolution",
    "solve_action_sync",
]
