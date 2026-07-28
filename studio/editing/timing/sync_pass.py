"""Attach Action Sync retimes to a planned EditSpec and score the result.

This is a post-planning pass, analogous to ``apply_recipe_plan``: it walks the
clips of a validated spec, and for each clip that (a) sits on a musical target
inside its own window and (b) carries a measured action peak, solves a speed
ramp that lands the peak on the target.  It emits a deterministic acceptance
table (`cut_accuracy_report.json`) reporting, per clip, the target hit, the
rendered peak, and the frame error — the Action Sync analogue of the Cut Sync
acceptance table the drum-locked workflow already requires.

Clips without a measured peak are reported as ``estimated`` and left as hard
cuts; the workflow never claims a frame-exact landing it did not measure.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from studio.asset_intelligence.motion.action_peak import ActionPeak
from studio.core.timecode import Timebase
from studio.editing.music import MusicMap
from studio.editspec.schema import EditSpec

from .action_sync import ACTION_SYNC_VERSION, solve_action_sync

# A target hit must sit at least this far past the cut so we sync to a beat
# *inside* the shot, not to the source change already on the cut.
MIN_TARGET_OFFSET_SEC = 0.12

# Only retime when the peak can actually be seated on the beat.  On a short
# drum-locked clip the peak cannot be moved far; a badly clamped speed ramp
# would distort motion without landing on the beat — strictly worse than the
# hard cut.  Above this residual we keep the hard cut and report the attempt.
MAX_RETIME_ERROR_FRAMES = 2


class CutAccuracyRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    clip_id: str
    shot_id: str
    timeline_in_sec: float
    # Cut Sync: does the source actually change on the clip boundary.  In a
    # rebuilt timeline every clip is a real source change, so this is a report
    # column carried for the acceptance table, not a re-derivation.
    source_change: bool = True
    # Action Sync columns.
    target_hit_sec: float | None = None
    target_kind: str | None = None
    measured_peak: bool = False
    peak_source_offset_sec: float | None = None
    action_peak_error_frames: int | None = None
    retimed: bool = False
    clamped: bool = False
    note: str = ""


class CutAccuracyReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str = ACTION_SYNC_VERSION
    clip_count: int
    measured_clips: int
    retimed_clips: int
    # Clips whose action peak lands within one delivery frame of the target.
    on_beat_clips: int
    max_action_peak_error_frames: int
    rows: list[CutAccuracyRow] = Field(default_factory=list)


def _target_hits(music: MusicMap) -> list[tuple[float, str]]:
    hits: list[tuple[float, str]] = []
    hits.extend((sec, "impact") for sec in music.impact_points)
    hits.extend((sec, "downbeat") for sec in music.downbeats)
    hits.extend((sec, "beat") for sec in music.beats)
    return hits


def _best_peak_in_window(
    peaks: list[ActionPeak],
    *,
    shot_start_sec: float,
    source_in_sec: float,
    source_out_sec: float,
) -> ActionPeak | None:
    window = source_out_sec - source_in_sec
    best: ActionPeak | None = None
    best_weight = 0.0
    for peak in peaks:
        offset = (shot_start_sec + peak.sec) - source_in_sec
        if offset < 0 or offset > window:
            continue
        weight = peak.magnitude * max(peak.confidence, 1e-3)
        if weight > best_weight:
            best, best_weight = peak, weight
    return best


def _nearest_target(
    hits: list[tuple[float, str]],
    *,
    window_start: float,
    window_end: float,
    prefer_sec: float,
) -> tuple[float, str] | None:
    lo = window_start + MIN_TARGET_OFFSET_SEC
    inside = [(sec, kind) for sec, kind in hits if lo <= sec <= window_end]
    if not inside:
        return None
    # Prefer the strongest target class present, then the one nearest to where
    # the peak already falls (least retime distortion).
    priority = {"impact": 0, "downbeat": 1, "beat": 2}
    best_class = min(priority[kind] for _, kind in inside)
    candidates = [(sec, kind) for sec, kind in inside if priority[kind] == best_class]
    return min(candidates, key=lambda item: abs(item[0] - prefer_sec))


def apply_action_sync(
    spec: EditSpec,
    *,
    music: MusicMap,
    peaks_by_shot: dict[str, list[ActionPeak]],
    shot_starts: dict[str, float],
) -> tuple[EditSpec, CutAccuracyReport]:
    """Return a spec with Action Sync retimes and its acceptance report."""
    timebase = Timebase.from_fps(spec.timebase.fps, drop_frame=spec.timebase.drop_frame)
    hits = _target_hits(music)
    rows: list[CutAccuracyRow] = []
    retimed = measured = on_beat = 0
    max_error = 0
    for clip in spec.clips:
        t0 = clip.timeline.in_sec
        t1 = t0 + clip.timeline.duration_sec
        shot_start = shot_starts.get(clip.shot_id)
        peaks = peaks_by_shot.get(clip.shot_id, [])
        peak = (
            _best_peak_in_window(
                peaks,
                shot_start_sec=shot_start,
                source_in_sec=clip.source.in_sec,
                source_out_sec=clip.source.out_sec,
            )
            if shot_start is not None
            else None
        )
        row = CutAccuracyRow(
            clip_id=clip.id,
            shot_id=clip.shot_id,
            timeline_in_sec=round(t0, 4),
        )
        if peak is None:
            # No measured landmark: leave the hard cut, mark honestly.
            existing = clip.retime.type == "speed_ramp"
            row.note = "no measured peak; estimated phase only"
            row.retimed = existing
            rows.append(row)
            continue
        measured += 1
        row.measured_peak = True
        peak_source_offset = (shot_start + peak.sec) - clip.source.in_sec
        natural_render_sec = t0 + peak_source_offset  # constant-speed position
        target = _nearest_target(
            hits, window_start=t0, window_end=t1, prefer_sec=natural_render_sec
        )
        row.peak_source_offset_sec = round(peak_source_offset, 4)
        if target is None:
            row.note = "measured peak but no in-window musical target"
            rows.append(row)
            continue
        target_sec, target_kind = target
        solution = solve_action_sync(
            timebase=timebase,
            clip_duration_sec=clip.timeline.duration_sec,
            marker_offset_sec=target_sec - t0,
            peak_source_offset_sec=peak_source_offset,
        )
        row.target_hit_sec = round(target_sec, 4)
        row.target_kind = target_kind
        row.action_peak_error_frames = abs(solution.residual_frames)
        row.clamped = solution.clamped
        achievable = row.action_peak_error_frames <= MAX_RETIME_ERROR_FRAMES
        if solution.retime.type == "speed_ramp" and achievable:
            clip.retime = solution.retime
            retimed += 1
            row.retimed = True
            row.note = solution.reason
            # Only retimed clips claim a sync, so only they count toward the
            # delivered peak-error ceiling.
            max_error = max(max_error, row.action_peak_error_frames)
        elif solution.retime.type == "speed_ramp":
            # Reachable target but the clip is too short to seat the peak;
            # keep the hard cut rather than distort motion off the beat.
            row.note = f"kept hard cut: peak unreachable ({row.action_peak_error_frames}f)"
        else:
            row.note = solution.reason
        if row.action_peak_error_frames <= 1:
            on_beat += 1
        rows.append(row)
    report = CutAccuracyReport(
        clip_count=len(spec.clips),
        measured_clips=measured,
        retimed_clips=retimed,
        on_beat_clips=on_beat,
        max_action_peak_error_frames=max_error,
        rows=rows,
    )
    revalidated = EditSpec.model_validate(spec.model_dump(mode="python", by_alias=True))
    return revalidated, report


__all__ = [
    "CutAccuracyRow",
    "CutAccuracyReport",
    "apply_action_sync",
    "MIN_TARGET_OFFSET_SEC",
]
