# Implementation Plan: Project Sekai Tiered Story RAG

Fork of [`linkura-story-indexer`](https://github.com/ahuei123456/linkura-story-indexer),
retargeted from Link! Like! Love Live! (Hasunosora) to **Hatsune Miku: Colorful
Stage! (Project Sekai)**. Same hierarchical-RAG core; the differences are all in
scale, structure, and source.

## 1. What we inherit from linkura

* **Tiered Hierarchical RAG.** Tier 1 (Arc) → Tier 2 (Volume) → Tier 3
  (Chapter) → Tier 4 (Scene/raw, the source of truth).
* **Data handling.** Scripts as `(speaker, text)`; prose as hybrid
  narrative+dialogue beats; a JSON **State Ledger** fact table to prevent
  hallucination and retcons.
* **Pipeline.** Ingest/parse → bottom-up "Refine" summarization → Chroma
  embeddings with hard relational links between tiers → State Ledger extraction
  → retrieval orchestration (off / heuristic / llm_router / agentic) →
  translation + audit loop.

## 2. What changes for Project Sekai

### 2.1 Source of truth (recovered from `autosub/projects/scripts/fetch_event.py`)

| Layer | URL | Contents |
|---|---|---|
| Master DB (metadata) | `https://sekai-world.github.io/sekai-master-db-diff` | `events.json`, `eventStories.json`, `eventMusics.json`, `musics.json`, `gameCharacters.json`, `unitStories.json` |
| Asset CDN (text) | `https://storage.sekai.best/sekai-jp-assets` | `event_story/{assetBundle}/scenario/{scenarioId}.asset` → `TalkData[]` of `{WindowDisplayName, Body}` |

JP is the source of truth (most complete/current); translation is downstream.
Implemented in `src/sekai_story_indexer/source/` (`constants`, `transform`,
`client`, `fetcher`). `indexer fetch` materializes the story tree.

### 2.2 Hierarchy re-mapping

linkura is one school with school **years** (`102/103/104/105`). Sekai has
**five parallel unit storylines** plus Virtual Singers plus crossovers, told
through Main stories, Event stories, Unit stories, Card side-stories, and Area
conversations.

Canonical on-disk layout the fetcher writes and the processor reads:

```
story/<unit>/<content_type>/<arc_slug>/<NN_episode-slug>.md
```

Tier mapping onto the reused machinery:

| Sekai concept | Tier | Field |
|---|---|---|
| Unit (leo_need … virtual_singer, mixed) | 1 (facet) | `unit` |
| Event / Main-arc ("Volume") | 2 | `arc_id` (= `NNNN-slug`) |
| Episode 話 ("Chapter") | 3 | `episode_name` |
| Scene (`---`-split) / raw turns | 4 | scene nodes |

`unit` starts as a **metadata facet + query filter**, not its own summary tier;
a Unit-level rollup summary above the Event tier is the first Phase-2 upgrade.

### 2.3 Chronological order is data-driven

linkura hand-maintains `story_order.yaml`. Sekai has too many events for that,
and release order *is* in-universe order. `indexer fetch` **auto-generates**
`story_order.yaml` from event `startAt` timestamps (zero-padded `arc_slug`s keep
even the filesystem sorted chronologically). Hand-authored content still uses
`part_order_overrides`.

## 3. The three Sekai challenges

### Challenge 1 — Length (much longer than linkura, frequent updates)
* Tiered summarization already tames length; the new requirement is **idempotent
  incremental ingest** so a new event adds only deltas. The inherited
  `IngestionManifest` (content hashing) supports this — the fetcher's
  `events_index.json` records what's been pulled.
* Add a **Unit-tier** and optional **global-timeline** summary above Event so
  broad "what is Leo/need's overall arc" queries don't fan out over hundreds of
  events.

### Challenge 2 — Quantity (5 interweaving units)
* One index, `unit` facet on every node, `--unit <slug>` query scoping →
  per-unit "interfaces" for free while preserving crossover/global queries.
  (See DESIGN.md §"Per-unit vs unified" for why we do **not** build 5 projects.)
* Crossover / World Link / VS events resolve to `unit: mixed` (or
  `virtual_singer`) so they surface for every relevant unit query.

### Challenge 3 — Event-story relevance (filler vs plot)
* **Everything is indexed in full** — filler included, per the requirement.
* **Native prior:** capture the game's own "key story" tag as `is_key_story`
  (from `eventStoryUnits.json` main-relation, sekai.best's `isKeyEventStory`).
  It's overinclusive, so it's only an input feature — not the verdict.
* **Our verdict:** a `plot_weight` tag (`high | medium | filler | unrated`) per
  episode via an LLM classification pass at ingest, scoring character-development
  and plot-progression, using `is_key_story` as one feature but free to disagree.
  Retrieval **boosts** high-weight content for thematic / character-arc queries
  but returns everything for "what happened in X" / exhaustive queries. State
  Ledger extraction prioritizes high/medium.
* `eventStoryUnits` also drives **authoritative unit resolution** (main-relation
  unit) — the character-count heuristic is now just a fallback.

## 4. Plan of attack (phased)

- [x] **Phase 0 — Fork & retarget.** Clone, rename package `sekai_story_indexer`,
  env prefix `SEKAI_`, drop Hasunosora data. Seed Sekai `glossary.json`.
- [x] **Phase 1 — Ingestion source layer.** `source/` package: taxonomy,
  pure transforms (unit resolution, slugging, scenario→scenes, tree paths),
  network client, fetcher (writes tree + `story_order.yaml` + `events_index.json`).
  `indexer fetch` CLI command. Unit tests green.
- [x] **Phase 1b — Processor & model.** `unit`/`content_type`/`plot_weight`/
  `event_id`/`started_at` on `StoryMetadata`; `extract_hierarchy` reads the
  Sekai tree.
- [~] **Phase 2 — Bottom-up indexing.** Unit-tier summaries DONE for the local
  backend (`query/summaries.py`: deterministic overviews from event outlines,
  Tier-1 nodes, retrievable). The **LLM Refine** summarizer + Chroma upsert /
  manifest incrementality is the inherited full-engine path — needs a keyed run
  (raised).
- [x] **Phase 3 — Relevance classifier.** `source/relevance.py` heuristic
  `plot_weight` (high/medium/filler), set on every fetch, wired into the local
  retrieval boost + citations; `sekai classify` command. (LLM refinement of the
  weights is a later upgrade using the same features.)
- [x] **Phase 1c — Event context enrichment.** Focus character (eventCards→cards
  featured limited card), commissioned song (eventMusics→musics), and CDN image
  URLs (event logo/banner, music jacket) captured into `events_index.json`.
- [x] **Phase 1d — Community nicknames.** `kasa5`/`mizu3` system: data-driven
  per-character focus numbering + editable abbreviation map (`nicknames.py` /
  `nicknames.json`), resolver, and nicknames written into the index.
- [x] **Phase 1e — Web app.** `webapp/` FastAPI + vanilla-JS SPA: chat over the
  query engine + unit-filterable event timeline with CDN visuals, nicknames,
  focus character, and song.
- [x] **Phase 1f — Local query backend + web app chat.** Dependency-light,
  deterministic lexical engine (`query/local.py`) with unit + **nickname**
  (`kasa5`) scoping and the indexed-only queryable contract; wired into
  `/api/query` (default backend) and the `sekai` CLI (`ask`/`serve`/`eval`).
  Runs with no API key / no Chroma, so the app is live anywhere.
- [x] **Phase 1g — Regression evals.** Golden set (`eval/golden_local.json`) +
  runner (`eval/local_eval.py`) gating retrieval/scoping/answer content, run as a
  pytest (`test_eval_local.py`) + API tests (`test_webapp_api.py`).
- [x] **Phase 1h — Grounded/quoting chat.** Query response returns structured
  `answer_parts` (text + clickable quote blocks) and `citations[].excerpt`; the
  web app renders quotes inline and opens a side panel with the full scene on
  click. (Full engine gains the same structure in Phase 4.)
- [x] **Phase 4 — Full-engine scoping.** Shared resolver `query/scoping.py`
  (`ScopeIndex` + `chroma_where`) wired into the local engine + per-unit golden
  cases. **Full-engine scope now wired + verified live:** `engine.query(question,
  arc_ids=…)` threads a caller-resolved scope into the arc-filter machinery
  (works even in `routing_mode="off"`), and `webapp/server.py` passes the resolved
  event's arc(s) (union, so comparisons aren't locked to one). Fixed the
  scope-drop that switched a follow-up to a different arc (airi1→airi2).
- [x] **Phase 7 — Natural-chat conversation layer.** Server-side per-session focus
  state (`webapp/sessions.py`: carry the current event/character across pronoun
  follow-ups, reset on topic switch); clarify-instead-of-guess gate
  (`query/disambiguation.py`); deterministic contextual-retrieval prefixes
  (`query/context.py`: nickname / "character X's Nth focus event" / unit / song) —
  **live now for the local TF-IDF (free, no re-embed)** and **teed up for the full
  engine** (prepended to the Chroma embedding + lexical text; takes effect on the
  next `indexer ingest` re-embed); history windowing (`condense.window_history`);
  and SSE streaming (`/api/query/stream` + `generate_answer_stream`). See
  `docs/contextual_embeddings_plan.md`.
- [~] **Phase 5 — Content beyond events.** **Unit stories DONE** (real fetch:
  `sekai fetch-unit-stories` → `story/<unit>/unit/…`, tested); non-event content
  is always-queryable. Card side-stories + Area conversations: modeled/scaffolded,
  fetch flows not yet built (raised — same pattern as unit stories).
- [~] **Phase 6 — Translation & audit.** Inherited full-engine feature: the
  translation prompts + `--audit` loop already exist (`query/audit.py`, prompts)
  and consume our Sekai `glossary.json` + State Ledger. Needs a keyed run to
  exercise (raised). Deterministic offline translation isn't attempted — name
  substitution inside JP sentences isn't genuinely useful without the LLM.

## 5. Known follow-ups / accuracy notes
* **Agentic-lite scene selection (local backend, deferred).** A scoped content
  query currently feeds the WHOLE event (budget-bounded, head+tail) to the answer
  — complete for small events, but blunt for large scopes. A better design lets
  the model examine a compact scene "table of contents" (episode title + speakers
  + first line) and fetch only the scenes it needs (reuse the full engine's
  `get_scene` tool pattern). Deferred: costs an extra LLM call per turn. Cheaper
  deterministic alternative if precision is needed first: intent-directed ranking
  (bias "climax/ending" → late episodes, "beginning" → early) — no extra call.
* Card/Area fetch flows (Phase 5 remainder) — mirror `fetch_unit_stories`.
* Full-engine: join `events_index` plot_weight into node metadata at ingest so
  the boost applies there too; inject `chroma_where(scope)` into the query.
* The inherited test suite is still Hasunosora-shaped (needs chromadb to even
  collect); Sekai tests are the `test_sekai_source/local_query/scoping/eval_local/
  webapp_api/content_and_summaries` files.
