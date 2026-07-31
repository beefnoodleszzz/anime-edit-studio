# motion_blur_transition_v2 acceptance

Human status: **PENDING**
Reviewer: (none)
Reviewed at: (none)
Decision / notes: Adds `settle_scale` — a SettleTransform node that eases the
incoming clip's zoom from `1+settle_scale` down to `1.0` over the transition
window (cubic ease-out), landing the cut instead of just whip-blurring
through it. `preview.mp4` here is a placeholder stand-in, not a real render —
re-render it in Resolve against a real cut before reviewing, then replace
this status with ACCEPTED/REJECTED per the usual recipe workflow.
