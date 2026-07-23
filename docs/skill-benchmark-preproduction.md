# Pre-production skill release check

Date: 2026-07-23

Scope: `anime-edit-studio` new-work intake, before vs. after the director-contract update.

This is an instruction-coverage benchmark, not a cross-model behavioral claim. A live cross-model
rerun remains appropriate after future model changes.

| Scenario | Required behavior | Before | After |
|---|---|---:|---:|
| “做炼狱，燃一点，抖音” | separate content lane from edit mode; ask promise/payoff/audience | partial | pass |
| “做一个没看过原作也能懂的情绪剪辑” | persist audience context and observable comprehension criterion | missing | pass |
| “你决定，直接做” | propose a concrete contract and request confirmation instead of skipping alignment | ambiguous | pass |
| “Showcase，要炸” | clarify what the opening promises and which climax fulfils it | partial | pass |
| New project with incomplete answers | block gap/direct until durable contract validates | missing | pass |
| Revision or diagnosis | skip new-work interrogation and preserve existing validated contract | pass | pass |

Critical criteria after update:

- director contract is a top-level non-negotiable;
- two exchanges maximum;
- library coverage is inspected before questions;
- content lane and edit mode are separate;
- promise, payoff, aftertaste, audience and success criterion cannot be omitted;
- owner-supplied facts are not re-asked;
- integrated realistic example is present;
- `anime brief validate` provides an executable release gate;
- old briefs remain readable and are simply reported incomplete until upgraded.

Validation:

- targeted creative-contract tests pass;
- legacy brief/scoring tests pass;
- Review Web typecheck and production build pass;
- `SKILL.md` links `references/preproduction-dialogue.md`;
- unresolved follow-up: live with-skill/without-skill matrix across future model versions.
