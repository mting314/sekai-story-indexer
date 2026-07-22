# CLAUDE.md — sekai-story-indexer

Hierarchical-RAG story indexer for **Hatsune Miku: Colorful Stage! (Project
Sekai)**. Fork of `ahuei123456/linkura-story-indexer`, retargeted from
Hasunosora to Project Sekai. Read `PLAN.md` (roadmap) and `DESIGN.md` (why)
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

## Key decisions (see DESIGN.md)
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
  `fetch-unit-stories`, `build-index`, `ask`, `serve`, `eval`. Uses the **local**
  lexical engine (`query/local.py`): deterministic TF-IDF retrieval + unit/nickname
  (`kasa5`) scoping + indexed-only queryable contract. This is what makes the app
  runnable + evals stable anywhere.
  * `sekai summarize [--limit N]` is the one command that needs `GOOGLE_API_KEY` +
    generation deps: it runs the LLM Refine event-tier summarizer into
    `summaries_cache.json` (fingerprint-cached, resumable, continuity-threaded;
    `--limit N` generates at most N new summaries; `thinking_level=low` for cost).
    Graceful stop on a spend-cap 429.
* `/api/query` picks backend via `SEKAI_QUERY_BACKEND` (`local` default, `full`).

## Run / test locally (no keys)
```bash
uv sync --extra web
sekai serve --story-root sample/story --events-index sample/events_index.json  # web app
sekai eval        # regression gate
uv run pytest     # unit + API + eval tests
```
`sample/story` + `sample/events_index.json` are a committed fixture corpus so the
app + evals work with no fetch/keys.

## Env note (restricted sandboxes)
No PyPI-egress? `PYTHONPATH=src <python-with-pydantic> -m pytest tests/`. The
`sekai` paths need only typer + fastapi/uvicorn (for serve); no chromadb.

## Phase status (see PLAN.md)
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
CI runs the **full** suite (`uv run pytest -q`) — **417 passing**. chromadb is
installed in CI, so the inherited linkura tests collect + run too; **run the full
`uv run pytest` locally before pushing, not just a Sekai subset** (a subset-only
run once missed a `test_database.py` break that CI caught). Sekai-specific files:
`test_sekai_source test_local_query test_scoping test_eval_local test_webapp_api
test_content_and_summaries test_sessions test_generate_config test_summarize_limit`.
