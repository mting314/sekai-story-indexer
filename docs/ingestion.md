# Constant ingestion

Project Sekai ships a new event roughly every 15 days. `sekai ingest` is the one
command that catches the whole corpus up with the game, and the web app is wired
to notice when it lands.

```bash
sekai ingest                      # Tier 1 only — keyless, safe to schedule
sekai ingest --with-llm           # + Tier 2, 5 new items per pass
sekai ingest --with-llm --batch-limit 0   # + Tier 2, drain the whole backlog
```

## The two tiers

**Tier 1 — keyless, always runs.** `fetch` → `fetch-card-stories` →
`fetch-area-conversations` → `link-content` → `build-lyric-map` → `classify` →
`build-index` → `record-state`. This is everything the lexical backend needs, so a
brand-new event is queryable the moment Tier 1 lands — no API key, no waiting on
an LLM.

**Tier 2 — needs `GOOGLE_API_KEY`, best-effort.** `summarize` → `conclusions` →
`resonance`, each capped at `--batch-limit` new items (default 5). The cap is the
point: a scheduled nightly run drains a backlog a few events at a time instead of
tripping a spend cap or a CI timeout. Every Tier-2 pass is fingerprint-cached and
resumable, so a capped run and an interrupted run are the same thing.

Failure policy is per-step and deliberate:

| Step | On failure |
|---|---|
| `fetch`, `classify`, `build-index`, `record-state` | **abort** — the rest is reported `skipped`, exit 1 |
| `link-content`, `build-lyric-map`, card/area fetches | log and continue (network side-quests) |
| all of Tier 2 | log and continue — a missing key is a normal Tuesday |

Nothing is ever silently dropped: `run_steps` reports every step, including the
ones an abort skipped.

## Freshness in the app

`record-state` writes `ingest_state.json` — `{event_id: first-seen timestamp}`.
The server turns that into two flags on `/api/events`:

* `is_new` — first seen within `SEKAI_NEW_EVENT_DAYS` (default 14). Falls back to
  the release date when there's no state file, and never fires for an
  announced-but-unreleased event.
* `summary_status` — `none` (no transcript) / `pending` (indexed and searchable,
  Tier 2 hasn't reached it) / `complete`. `indexed` gates it, so a summary
  without a transcript never reads as ready.

The timeline renders those as a **NEW** badge and a **Summary pending** badge
(`frontend/src/lib/freshness.ts`), plus a backlog count in the legend. A pending
card is still clickable — it searches the raw transcript.

**The first run is a baseline.** It sees the whole back catalogue at once, so it
stamps first-seen from release dates and announces nothing. Otherwise adopting
the pipeline would badge 200+ events as NEW for two weeks.

## Picking up a run without a restart

Every on-disk artifact the server caches is keyed on its mtime — the events
index, the summaries/conclusions/resonance caches, `content_parents.json`, the
derived index, the built lexical engine. An ingest run shows up on the next
request rather than after the 6h timeline TTL.

`POST /api/admin/reload` is the explicit hammer (it also forces the next request
to re-pull the master DB). It drops caches only and returns immediately —
repopulating inline would block the caller on a multi-second live pull.
Unauthenticated by default; set `SEKAI_ADMIN_TOKEN` to require a matching
`X-Admin-Token` header.

```bash
sekai ingest --reload-url http://127.0.0.1:8000/api/admin/reload
```

## Scheduling

* **GitHub Actions** — `.github/workflows/ingest.yml`, nightly at 04:00 UTC. Tier 1
  only on a schedule; `workflow_dispatch` can turn on Tier 2 and the (slow)
  card/area fetches. The story corpus is copyrighted game text and is *not*
  committed — it lives in the Actions cache between runs so each night only
  downloads the delta. Only our derived artifacts get committed back.
* **cron / launchd / systemd** — `scripts/run_ingestion.py`, env-driven, always
  runs from the repo root:

  ```cron
  0 4 * * * cd /path/to/sekai-story-indexer && .venv/bin/python scripts/run_ingestion.py >> ingest.log 2>&1
  ```

Exit code is 0 when every *required* step landed, 1 otherwise. Tier-2 failures
never change it.

## Note on paths

`sekai ingest` writes to repo-root-relative paths (`events_index.json`,
`derived_index.json.gz`, `lyric_page_map.json`, …), like the individual commands
it composes. Run it from the repo root — or from a scratch directory if you want
a throwaway run that can't touch the committed artifacts.
