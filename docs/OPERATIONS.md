# Operations

## Setup

```bash
cd ~/Desktop/anime-edit-studio
uv venv --python 3.11   # Resolve 21's fusionscript module rejects 3.14+ (AGENTS.md P3)
source .venv/bin/activate
uv pip install -e ".[dev]"
uv pip install -e ".[ml]"   # optional: CLIP retrieval + WD tagger
```

Resolve must be **Studio 21.0.3.7**, running in the foreground (no true
headless mode — AGENTS.md P5). Connection env vars are injected by
`studio/execution/resolve/connection.py`; no manual `export` needed.

## Day-to-day commands

```bash
aes doctor env                    # Python / Resolve / external tool health
aes doctor capabilities           # verified / unverified Resolve capability matrix
aes doctor assets                 # which asset_ids are resolvable right now
aes doctor vision                 # ShotWindow selection vision backends: running / degraded

aes library index /path/to/materials [--profile coarse|full]   # incremental: ingest + shot detect + analyze

aes amv create \
  --project my-amv --demo demo.mp4 --materials /path/to/materials \
  --music track.wav [--focus TEXT] [--aspect 9:16] [--fps 24000/1001] \
  [--selector-profile fast|balanced|quality] [--launch] [--json]

aes amv release --project my-amv [--json]
```

`aes amv create` runs the Resolve-independent phase (index → analyze → plan →
`AMVSpec`) unconditionally; if Resolve is unavailable it says so honestly
(`Resolve 真机验收未执行，不能宣称完整完成`) rather than claiming success.
Pass `--launch` to auto-start Resolve if it isn't running.

## Testing

```bash
pytest -q -m "not requires_resolve"     # offline, no Resolve needed
pytest -q -m requires_resolve           # needs a running local Resolve — run alone,
                                         # not concurrently with another Resolve session
                                         # (see "Resolve concurrency" below)
python -m compileall studio
```

## Resolve real-machine acceptance

`tests/execution/test_amv_acceptance.py` is the end-to-end proof: builds a
real two-clip `AMVSpec` with a carry `TransitionPair` spanning the cut from
real proxy assets already in the library, places it on an actual Resolve
timeline, compiles Fusion, renders a real window, and checks the output with
the same `run_technical_qa` hard gates the release path uses.

```bash
.venv/bin/python -m pytest -m requires_resolve tests/execution/test_amv_acceptance.py -v
```

### Resolve concurrency

Only run one process against Resolve at a time. Two simultaneous connections
(e.g. a second `aes amv create` or a second pytest run against
`requires_resolve` tests) produce spurious `MediaPool`/`GetClipList` races
that look like new bugs but are actually cross-process contention — see
pitfall P22 in `config/resolve_capabilities.yaml`.

## Database

`library/engine.v2.sqlite` is the only database. Schema is versioned via
`studio/core/database.py`'s migration chain — `connect()` applies any
un-applied migration on open, idempotently.

Before any schema-changing migration or bulk edit, back it up:

```bash
mkdir -p library/backups
cp library/engine.v2.sqlite "library/backups/engine-$(date +%Y%m%d-%H%M%S).sqlite"
```

Verify row counts are unchanged after any migration:

```bash
sqlite3 library/engine.v2.sqlite \
  "SELECT (SELECT COUNT(*) FROM assets), (SELECT COUNT(*) FROM shots), \
          (SELECT COUNT(*) FROM shots WHERE embedding IS NOT NULL);"
```

`library/proxies/` (asset proxies) is a long-term shared resource across all
projects — never delete, move, or clear it as part of "cleanup" unless a
human explicitly asks for that specific directory (AGENTS.md R6).

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `aes doctor env` reports Resolve FAIL | Resolve not running, or wrong Python | `pgrep -x Resolve`; confirm `.venv` is Python 3.11 |
| `ResolveUnavailable` from `ResolveAdapter.open()` | Resolve not running / just started | pass `--launch`, or start Resolve and wait for it to fully load |
| Random `TypeError: 'NoneType' object is not iterable` right after project create | P22 race (`GetClipList()`/`GetSubFolderList()` returning `None` transiently) | already handled in `adapter.py`; if it resurfaces elsewhere, guard with `or []` |
| Audio track has 2 items instead of the expected explicit music track | A video clip request omitted `media_type: 1`, so Resolve auto-linked its source audio (P19) | check `build_amv_timeline` in `amv_render.py` |
| `qa.json` fails `unexpected_silence`/`loudness` only | Real symptom, or a silent placeholder music fixture (tests only) | for real deliveries this is a genuine gate failure — investigate the music track |
