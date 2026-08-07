# CLAUDE.md — sekai-story-indexer

Hierarchical-RAG story indexer for **Hatsune Miku: Colorful Stage! (Project
Sekai)**. Fork of `ahuei123456/linkura-story-indexer`, retargeted from
Hasunosora to Project Sekai. Read `docs/PLAN.md` (roadmap) and `docs/DESIGN.md` (why)
first; `AGENTS.md` has the hard repo policy.

## What this is
Parses Sekai story text into a 4-tier index (Unit → Event → Episode → Scene),
builds bottom-up summaries + a State Ledger, embeds into Chroma, and serves
grounded queries with optional translation. Package: `sekai_story_indexer`,
CLI entry point `indexer`, env var prefix `SEKAI_`.

## Data source (do not rediscover — it's recovered and documented)
Same source as `~/github/autosub/projects/scripts/fetch_event.py`:
* Master DB: `https://sekai-world.github.io/sekai-master-db-diff` (events, eventStories, gameCharacters, …)
* Asset CDN: `https://storage.sekai.best/sekai-jp-assets` (`event_story/{bundle}/scenario/{id}.asset` → `TalkData[]`)

These hosts are **external**; run `indexer fetch` where egress to them is allowed
(the standard restricted Meta harness blocks them).

## Layout the fetcher writes / processor reads
```
story/<unit>/<content_type>/<arc_slug>/<NN_episode-slug>.md
```
`unit` ∈ leo_need, more_more_jump, vivid_bad_squad, wonderlands_showtime,
nightcord, virtual_singer, mixed. Scenes split by `---`; lines `speaker: text`.

## Sekai-specific code (what the fork added)
* `src/sekai_story_indexer/source/` — `constants` (taxonomy), `transform` (pure,
  tested), `client` (network), `fetcher` (writes tree + `story_order.yaml` +
  `events_index.json`).
* `indexer/processor.py::extract_hierarchy` — reads the Sekai tree.
* `models/story.py::StoryMetadata` — added `unit`, `content_type`, `plot_weight`,
  `event_id`, `started_at`.
* `indexer fetch` CLI command.

## Key decisions (see docs/DESIGN.md)
* **One unified index + `unit` facet**, not five separate per-unit projects.
* `unit` is a facet now; **Unit-tier summary** is the next tier to add (Phase 2).
* **Index all filler**; `plot_weight` only re-ranks, never excludes.
* `story_order.yaml` is **auto-generated** from event release dates.

## Dev / test policy (AGENTS.md)
Use `uv`. Every source-modifying task must pass `uv run ruff check . --fix`,
`uv run pyrefly check .`, `uv run pytest` before it's done.

Environment note: in restricted harnesses without PyPI egress, `uv sync` may
fail. The pure Sekai modules (`source/`, `processor`, `models`) can be tested
with any interpreter that has `pydantic`+`pyyaml`:
`PYTHONPATH=src <python> -m pytest tests/test_sekai_source.py`.

## Two CLIs / two query backends
* `indexer` (cli.py) — full Google/Chroma RAG; needs deps + `GOOGLE_API_KEY`.
* `sekai` (localcli.py) — dependency-light, no-API core: `fetch`,
  `fetch-unit-stories`, `build-index`, `build-lyric-map`, `ingest`, `ask`, `serve`, `eval`. Uses the **local**
  lexical engine (`query/local.py`): deterministic TF-IDF retrieval + unit/nickname
  (`kasa5`) scoping + indexed-only queryable contract. This is what makes the app
  runnable + evals stable anywhere.
  * `sekai summarize [--limit N]` and `sekai conclusions [--limit N]` are the two
    commands that need `GOOGLE_API_KEY` + generation deps. `summarize` runs the LLM
    Refine event-tier summarizer into `summaries_cache.json` (fingerprint-cached,
    resumable, continuity-threaded; `--limit N` caps new summaries;
    `thinking_level=low` for cost; graceful stop on a spend-cap 429). `conclusions`
    is a cheap second pass over each summary's Overview + Episode Index (not full
    scenes) that writes a climax episode + "how it ends" into `conclusions_cache.json`
    (same fingerprint/resume/spend-cap semantics), served keyless by the conclusion
    intent. Run `summarize` first.
  * `sekai resonance [--limit N]` derives a lyric↔story "resonance" note per event
    into `resonance_cache.json` (fetches lyrics live from Sekaipedia, never rehosted;
    content-only fingerprint so Gemini/Claude-subagent notes coexist). Provider-
    agnostic; served keyless by the resonance intent. Needs `build-lyric-map` +
    `summarize` first.
* `/api/query` picks backend via `SEKAI_QUERY_BACKEND` (`local` default, `full`).

## Constant ingestion (`sekai ingest`) — see docs/ingestion.md
One scheduled command catches the corpus up with the game (a new event ~every 15
days). `src/sekai_story_indexer/ingest.py` is a plain ordered `Step` list run by
`run_steps`; **Tier 1** (fetch → card/area → link-content → build-lyric-map →
classify → build-index → record-state) is keyless and always runs, so a new event
is queryable immediately. **Tier 2** (`--with-llm`: summarize → conclusions →
resonance) is capped by `--batch-limit` (default 5 new items/pass) so a nightly
run drains a backlog without tripping the spend cap. Required steps abort the run;
network side-quests and all of Tier 2 log and continue — a missing key is normal.
* `record-state` writes `ingest_state.json` (first-seen per event id). The server
  derives `is_new` + `summary_status` (`none`/`pending`/`complete`, gated on
  `indexed`) onto `/api/events`; the timeline renders NEW / "Summary pending"
  badges (`frontend/src/lib/freshness.ts`). The **first** run is a baseline:
  first-seen is backdated to release dates so adoption doesn't badge the whole
  back catalogue.
* Server caches are mtime-keyed (events index, summary caches, derived index,
  built lexical engine), so a run lands without a restart. `POST
  /api/admin/reload` is the explicit flush. A flush costs an engine rebuild + a
  live master-DB pull, so it's **loopback-only unless `SEKAI_ADMIN_TOKEN` is set**
  (then token-required everywhere, no loopback bypass) and rate-limited by
  `SEKAI_ADMIN_RELOAD_COOLDOWN` (default 5s).
* Schedulers: `.github/workflows/ingest.yml` (nightly, Tier 1, corpus in the
  Actions cache — transcripts are never committed) and `scripts/run_ingestion.py`
  (env-driven, for cron/launchd).
* `ingest` writes repo-root-relative paths like the commands it composes — run it
  from the repo root, or from a scratch dir for a throwaway run.

## Web frontend (`frontend/`)
The web UI is a **React + Vike + Panda CSS** app in `frontend/` (Ask · Timeline · Summaries ·
Setlist). It replaced the old vanilla `webapp/static/{index.html,app.js}` page. It's a static
bundle served by FastAPI at `/`; it calls the same `/api/*` endpoints + reuses
`/static/{meta.json,units,chara}`. Backend (`webapp/server.py` + `/api/*`) is unchanged.
Build it before serving: `cd frontend && bun install && bun run build` (→ `dist/client`, picked
up automatically; override with `SEKAI_FRONTEND_DIST`). `bun run fetch-songs` populates the
Setlist catalog from the Sekai master DB. See `frontend/README.md`.

## Run / test locally (no keys)
```bash
uv sync --extra web
cd frontend && bun install && bun run build && cd ..   # build the web UI (once / after FE changes)
sekai serve --story-root sample/story --events-index sample/events_index.json  # web app at /
sekai eval        # regression gate
uv run pytest     # unit + API + eval tests
```
`sample/story` + `sample/events_index.json` are a committed fixture corpus so the
app + evals work with no fetch/keys.

## Env note (restricted sandboxes)
No PyPI-egress? `PYTHONPATH=src <python-with-pydantic> -m pytest tests/`. The
`sekai` paths need only typer + fastapi/uvicorn (for serve); no chromadb.

## Pre-push hook (mirror CI)
CI (`.github/workflows/ci.yml`) has three jobs: `ruff check .`, `uv run pytest -q`, and
`Frontend (build)` (`bun install --frozen-lockfile && bun run build && bun test src`). The
tracked `.githooks/pre-push` runs all three locally (frontend step skipped if bun/deps absent).
Enable once per clone: `git config core.hooksPath .githooks` (bypass with `git push --no-verify`).
Note: the **pytest** job does not build the frontend, so `test_index_html_served` tolerates the
"Frontend not built" fallback — don't reintroduce a hard `"Sekai" in /` assertion.

## Phase status (see docs/PLAN.md)
- Local backend fully implemented + tested (no API key):
  - Phase 2: Tier-1 unit overviews (`query/summaries.py`, deterministic).
  - Phase 3: `plot_weight` heuristic classifier + retrieval boost (`source/relevance.py`).
  - Phase 4: shared `query/scoping.py` (unit/nickname/event → Scope, `chroma_where`).
  - Phase 5: unit stories fetched (`fetch-unit-stories`); card/area still TODO.
  - Phase 7: **sticky** conversation focus + **soft-scope global fallback**
    (`sessions.py`/`server.py`): carry the event across follow-ups; drop it when the
    turn names a character absent from it (`engine.names_absent_character`) or shares
    no evidence — so a topic switch self-heals. Wired for local/derived/full.
  - Cross-lingual glossary bridge; quote-grounded answers + excerpt sidebar;
    official-EN episode titles in citation labels (`_episode_title` +
    `episode_titles_en` serve-time overlay, JP H1 fallback).
  - **Focused conclusions** (`query/conclusion.py`): "what's the conclusion?" serves
    a climax-episode + "how it ends" from `conclusions_cache.json` (built keyed by
    `sekai conclusions`), keyless; no cache → a heuristic that picks the resolution
    beat over the epilogue (Sekai events close on a coda after the climax, so "last
    episode" is the wrong pick). Replaces the old Overview + Continuity Facts dump.
  - **Lyric↔story resonance** (`source/lyrics.py`, `indexer/resonance.py`,
    `query`→server intercept): "how does the theme song relate to the story?" serves a
    resonance note from `resonance_cache.json`, keyless. Lyrics are fetched **live**
    from Sekaipedia (`sekai build-lyric-map` → `lyric_page_map.json` joins song_id→
    pageid; parser reads `{{Lyrics line}}`) and **never rehosted** — only derived notes
    are cached. Notes built by `sekai resonance` (Gemini) OR Claude subagents (same
    content-only fingerprint). Use the wiki `english` song name, never the JP title.
- **LLM Refine event summarizer**: runnable standalone via `sekai summarize
  [--limit N]` (`thinking_level=low`); **136/209 event summaries built** into
  `summaries_cache.json` (rest blocked on a Gemini spend cap — resume with
  `sekai summarize`). Chroma upsert stays the full-engine `indexer ingest` path.
- Answer generation (`query/generate.py`): no mid-sentence truncation
  (`max_output_tokens=8192`) + `thinking_level=low` (~82% cheaper), with a retry
  that drops `thinking_config` on models that reject it.
- **Full engine (needs GOOGLE_API_KEY + chromadb) — raised as untested here:**
  Phase 4 `chroma_where` injection into engine.py, Phase 6 translation/audit
  (inherited, consumes our glossary). `unit`/`arc_id` already flow into Chroma
  metadata, so the filters are ready to wire.
- Fetch is resilient (retries IncompleteRead) + resumable (`--skip-existing`).

## Tests
CI runs the **full** suite (`uv run pytest -q`) — **542 passing**. chromadb is
installed in CI, so the inherited linkura tests collect + run too; **run the full
`uv run pytest` locally before pushing, not just a Sekai subset** (a subset-only
run once missed a `test_database.py` break that CI caught). Sekai-specific files:
`test_sekai_source test_local_query test_scoping test_eval_local test_webapp_api
test_content_and_summaries test_sessions test_generate_config test_summarize_limit
test_conclusion test_conclusions_extractor test_lyrics test_resonance
test_ingest_pipeline test_webapp_freshness`.
