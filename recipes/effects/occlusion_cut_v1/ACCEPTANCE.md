# occlusion_cut_v1 — Acceptance (PENDING)

Status: **UNVERIFIED**. Do not mark `verified: true` until every item passes.

## What this Recipe does
Use the outgoing subject sweeping across the frame (W4a `horizontal_sweep`) to
wipe a hard-cut join — a foreground-occlusion transition instead of a
half-transparent dissolve (memo §二: 遮挡式切镜).

## Required artifacts (AGENTS R4)
- [ ] Two-sided implementation: `out` comp on the left clip, `in` comp on the
      right clip. Do **not** merge TimelineItems with `CreateFusionClip`.
- [ ] `preview.mp4` — visual sample across a real cut.
- [ ] This file, with a dated human verdict.

## Render checks before flipping `verified`
- [ ] The join is hidden by the moving subject; no cross-dissolve ghosting.
- [ ] Both clips keep their own frame counts and identities (scalable to
      consecutive occlusion cuts).
- [ ] Planner only emits this where a supporting CutRelation + measured sweep
      exists (see `detect_layering_opportunities`), never on sweep alone.

## Verdict
_Not yet accepted._
