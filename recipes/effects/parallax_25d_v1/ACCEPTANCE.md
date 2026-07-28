# parallax_25d_v1 — Acceptance (PENDING)

Status: **UNVERIFIED**. Do not mark `verified: true` until every item passes.

## What this Recipe does
Separate the measured subject foreground (W4a `subject_layers`) from the
background and push them at different rates within a single Fusion comp, creating
2.5D parallax depth (memo §二: 人物推进 8%, 背景 2%).

## Required artifacts (AGENTS R4)
- [ ] `parallax_25d_v1.comp` — GUI-built Fusion comp: MediaIn → subject matte →
      two Transforms (subject faster than background) → Merge → MediaOut.
      One comp only (multiple comps on a TimelineItem are versions, not a stack).
- [ ] `preview.mp4` — visual sample.
- [ ] This file, with a dated human verdict.

## Render checks before flipping `verified`
- [ ] Parallax is visibly present (subject and background displace by different
      amounts) on a real production shot.
- [ ] No edge tearing / halo at the subject matte boundary.
- [ ] Rendered frame count equals the source clip's frame count (no truncation).
- [ ] Identity frames outside the move window are unchanged.

## Verdict
_Not yet accepted._
