# Architecture

One product use case: Demo + materials (+ music) → AMVSpec → Resolve render → QA.
No LLM in the deterministic path (see AGENTS.md R5). Everything below is real,
runnable code — this document describes what exists, not a plan.

## Data flow

```
Demo video ──▶ ReferenceAnalyzer ──▶ ReferenceBlueprint
Target music ─▶ MusicAnalyzer ─────▶ MusicTimeline
Materials dir ─▶ index_materials ──▶ assets/shots/embeddings in engine.v2.sqlite

ReferenceBlueprint + MusicTimeline + shot library
        │
        ▼
  RhythmStyleMapper → TimelineSlots (SlotRequirement fields, §5.2)
        │
        ▼
  GlobalSequencePlanner (beam search over studio.selection's ShotWindows)
        │      ▲
        │      └── studio.selection: technical gate → portrait/action
        │          analysis → temporal window generation → slot-conditioned
        │          scoring + reference fit, per candidate Shot
        ▼
  MotionPlanner → per-clip Motion + TransitionPair
        │
        ▼
  build_amv_spec → AMVSpec (studio/spec/amv.py, v3.1.0, extra="forbid")
        │
        ▼
  compile_amv_spec → one Fusion comp per clip via ResolveAdapter
        │
        ▼
  render_amv → preview.mov
        │
        ▼
  run_rendered_qa (technical hard gates + Demo-relative StyleSummary diff)
        │
        ▼
  optimize_test_interval (bounded gain-parameter search, on a representative window)
        │
        ▼
  aes amv release → release.mov (only if qa.json passed)
```

## Modules

- `studio/spec/` — the three versioned schemas: `ReferenceBlueprint`,
  `MusicTimeline`, `AMVSpec`. All pydantic v2, `extra="forbid"`. Nothing
  downstream constructs an `AMVSpec` except `studio.planning.amv_spec_builder`.
- `studio/analysis/` — `reference_analyzer.py` (cut detection, two-tier global
  motion estimation: LK Flow+RANSAC primary, ECC fallback, cross-cut motion
  pairing, blur/audio-visual relationship) and `music_analyzer.py` (beats,
  sections, accents).
- `studio/asset_intelligence/` — shot detection, embeddings, tagging, motion
  and temporal-quality analysis, character appearance evidence, search
  indexing. Feeds the shot database that `GlobalSequencePlanner` retrieves
  from. General-purpose, not tied to any specific AMV project.
- `studio/selection/` — turns per-Shot analysis into scored `ShotWindow`
  candidates (REFACTOR.md §4): `schemas.py` (`ShotWindow` and its nested
  technical/subject/portrait/action/editability profiles), `technical_gate.py`
  (hard gate: bad-frame ratio, subtitle/watermark/black-white-clip,
  clarity/compression, subject visibility/safe-crop, longest unusable run —
  independent of and never overridden by soft scoring), `portrait_analyzer.py`
  + `action_analyzer.py` (character-showcase and residual-motion-after-camera-
  compensation analysis, `backends/anime_face.py`/`backends/*`), `editability.py`,
  `temporal_windows.py` (locates portrait/action spans inside a Shot),
  `candidate_provider.py` (caches windows in `shot_windows`, coarse shortlist),
  `reference_fit.py` (real Demo-vs-candidate comparison, not a placeholder),
  `sequence_scoring.py` (slot-conditioned weighting: Portrait/Action-Impact/
  Transition/Hold each reward a different profile), `selector.py` (bridges a
  `TimelineSlot` to ranked `ShotWindow`s for `global_sequence_planner.py`).
  `config.py` + `config/selection_thresholds.yaml` hold versioned, calibratable
  gate thresholds.
- `studio/planning/` — turns a `ReferenceBlueprint` + `MusicTimeline` into an
  `AMVSpec`: `rhythm_style_mapper.py` (Exact Replica vs Style Transfer slot
  mapping, now carrying screen-language fields per shot), `global_sequence_planner.py`
  (beam search over `studio.selection`'s ShotWindow candidates, with identity/
  series-scope/motion-direction continuity and source-range-overlap dedup),
  `motion_planner.py` (per-clip `Motion` + `TransitionPair` for continuous
  cross-cut motion: carry/reverse/reset), `amv_spec_builder.py` (the single
  `AMVSpec` constructor — uses each choice's exact `source_in_sec`/
  `source_out_sec`, never `shot.start_sec`).
- `studio/editing/` — `retrieval/` (coarse, deterministic shot candidate
  prefilter reused by `studio.selection.selector`), `music/` (beat/section/
  accent detection feeding `music_analyzer.py`). The old generic `ranking/`
  engine was removed once nothing called it (REFACTOR.md §24).
- `studio/execution/resolve/` — the **only** package allowed to
  `import DaVinciResolveScript` (AGENTS.md R1). `adapter.py` is the facade
  (project/timeline/media-pool operations); `fusion_program.py` is the single
  Fusion-compilation entry point — one comp per clip, fixed node chain
  (`MediaIn → BaseTransform → MotionTransform → NativeMotionBlur →
  DirectionalBlur → PostColor → MediaOut`); `amv_render.py` builds the
  timeline and renders; `connection.py` handles Resolve process discovery.
- `studio/execution/amv_compiler.py` — walks an `AMVSpec`'s clips and calls
  `fusion_program.build_fusion_clip_program` for each, against the timeline
  items `ResolveAdapter.append_clips` already placed.
- `studio/critic/technical/qa.py` — `run_technical_qa`: 13 hard gates
  (duration, resolution, fps, black/freeze frames, corruption, loudness,
  silence, …) any render must pass.
- `studio/qa/` — `rendered_qa.py` (`run_rendered_qa`: hard gates +
  Demo-relative `StyleSummary` comparison, never a blended score) and
  `optimizer.py` (bounded 9-parameter gain search, max 4 rounds, over a
  representative interval picked by `pick_representative_interval`).
- `studio/workflows/create_amv.py` — the only workflow: `index_materials`,
  `build_amv_spec_workflow` (Resolve-independent), `render_amv_preview`
  (Resolve-dependent: builds timeline, compiles Fusion, renders, runs QA +
  optimizer), `release_amv` (copies preview → release only if QA passed).
- `studio/core/` — `database.py` (versioned SQLite schema, `engine.v2.sqlite`),
  `capabilities.py` (loads `config/resolve_capabilities.yaml`, the single
  source of truth for what Resolve can actually do), `timecode.py` (the one
  seconds↔frame conversion implementation, AGENTS.md R3), `assets.py`,
  `cache.py`, `hashing.py`, `env.py`.
- `studio/cli.py` — `aes doctor {env,capabilities,assets,vision}`,
  `aes library index [--profile coarse|full]`,
  `aes amv create [--selector-profile fast|balanced|quality]`, `aes amv release`.
  Parses arguments and prints results; no business logic lives here.

## Database

`library/engine.v2.sqlite`, versioned via `studio/core/database.py`'s
migration chain (`SCHEMA_VERSION`, currently 17). Tables in current use:
`assets`, `shots`, `shot_tracks`, `shot_temporal_quality`, `shot_windows`/
`shot_window_embeddings`/`shot_window_tracks` (ShotWindow cache, keyed by
shot_id + analysis_version — REFACTOR.md §6), `subject_layers`,
`source_records`, `characters`, `music_tracks`, `reference_blueprints`,
`music_timelines`, `amv_projects`, `amv_runs`, `renders`/`qa_results`
(optional technical-QA logging), `workflow_states`, `shots_fts*` (search
index). `shot_scores`/`candidate_scores` remain in the schema (harmless,
no longer written) after the generic ranking engine that used them was
removed; `review_decisions` was dropped in migration 17 along with the
retrieval engine's runtime query against it — the old Review UI is gone
and the automated selection flow does not depend on it. There is no v1
database and no runtime ETL — the one-time `library/engine.sqlite` →
`engine.v2.sqlite` conversion already happened.

## What deliberately doesn't exist

No EditSpec IR, no manual Candidate A/B/C workflow, no Preference/Growth
learning loop, no Review UI, no Recipe Zoo with per-effect `.comp`/`.drx`
artifacts, no per-project hardcoded shot lists or character branches. See
AGENTS.md §0 and §1 R4 for why, and `git log` for what was removed and when.
