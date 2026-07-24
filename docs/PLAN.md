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
  Tier-1 nodes, retrievable). The **LLM Refine** event summarizer is now runnable
  standalone via `sekai summarize [--limit N]` (thinking_level=low for cost,
  fingerprint-cached + resumable, continuity-threaded); **136/209 event summaries
  built** into `summaries_cache.json` (the rest blocked on a Gemini spend cap —
  raise it + re-run to resume). Chroma upsert / manifest incrementality remains the
  inherited full-engine `indexer ingest` path (needs a keyed run).
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
  state (`webapp/sessions.py`: **sticky** arc focus — carry the current event/
  character across follow-ups until a *new* event is named, even for a bare
  question that only names characters ("when did Honami ask Kanade for help?"),
  with a **soft-scope global fallback** in `server.py` when the follow-up names a
  character absent from the remembered event or shares no evidence with it, so a
  real topic switch self-heals — wired across the local/derived/full backends);
  clarify-instead-of-guess gate
  (`query/disambiguation.py`); deterministic contextual-retrieval prefixes
  (`query/context.py`: nickname / "character X's Nth focus event" / unit / song) —
  **live now for the local TF-IDF (free, no re-embed)** and **teed up for the full
  engine** (prepended to the Chroma embedding + lexical text; takes effect on the
  next `indexer ingest` re-embed); history windowing (`condense.window_history`);
  and SSE streaming (`/api/query/stream` + `generate_answer_stream`). See
  `contextual_embeddings_plan.md`.
- [~] **Phase 5 — Content beyond events.** **Unit stories DONE** (`sekai
  fetch-unit-stories` → `story/<unit>/unit/…`). **Card side-stories + Area
  conversations: fetch, parent-resolution, and event-nesting all DONE** (PRs #40,
  #42); only summarization remains.
  - **Fetch** — `sekai fetch-card-stories` / `sekai fetch-area-conversations`
    (mirror `fetch_unit_stories`; EN sidecars, resumable). Asset paths verified
    live: card `character/member/<bundle>/<sid>.asset`, area
    `scenario/actionset/group<id//100>/<sid>.asset`. Run at full scale locally
    (2,708 card + 2,795 area episodes); the fetched data is **not committed** (large).
  - **Parent resolvers** — card→event via the `eventCards` FK
    (`build_card_parent_map`, with a re-run tie-break + birthday/other fallback);
    area→event via the `event_story` unlock condition
    (`build_area_event_map`, 3-way: **event** 1,517 / **campaign** 474 / **permanent**
    804 — deterministic, 0 unresolved).
  - **Nesting** — `sekai link-content` composes them into `content_parents.json`;
    the processor stamps `parent_event_id` / `parent_arc_id` / `content_group` on
    card/area nodes (no-op without the file), and the local engine surfaces them as
    **children of a scoped event** (`_candidate_indices`, `include_children`;
    `count_dialogue` opts out to keep its exact count).
  - **TODO — summarize the fetched card/area content (the only remaining piece).**
    Card = **per-card, WITH character arcs**; area = **per-event-group** (no arcs;
    keep continuity facts + a plain character list); generic permanent-area talks
    indexed raw (no summary). Large (~5.5k episodes) → an opt-in batched workflow
    like the event-summary run. Keyed off the parent-event metadata above so the
    summaries nest under their event.
- [~] **Phase 6 — Translation & audit.** Inherited full-engine feature: the
  translation prompts + `--audit` loop already exist (`query/audit.py`, prompts)
  and consume our Sekai `glossary.json` + State Ledger. Needs a keyed run to
  exercise (raised). Deterministic offline translation isn't attempted — name
  substitution inside JP sentences isn't genuinely useful without the LLM.

## 5. Known follow-ups / accuracy notes
* **Grounded-answer reliability + cost (done).** Generated answers no longer
  truncate mid-sentence: a flash "thinking" model bills reasoning against
  `max_output_tokens`, and a detailed summary spent ~3.8k tokens thinking of a flat
  4096 cap → only ~300 left → cut off. Now `max_output_tokens=8192` (a ceiling —
  only emitted tokens bill, so it's free) plus `thinking_level="low"` (~0.7k
  thinking, ~82% cheaper, and actually honored unlike `thinking_budget`), with a
  retry that drops `thinking_config` if a model rejects `thinking_level`.
  `query/generate.py`.
* **"What's the conclusion?" quick-action just returns the summary (TODO).** The
  scoped conclusion intent (`_scoped_event_intercept`, `_CONCLUSION_RE`) currently
  answers with the summary's Overview + Continuity Facts — which reads as "just the
  summary," not the event's actual ending/resolution. Give it a focused conclusion
  instead: e.g. the final beat from the last Episode Index entry (+ the resolution
  sentences of the Overview), or a dedicated short "how it ends" synthesis — not the
  whole Overview. Keyless: derive from the summary's tail (last episode + closing
  Continuity); with a key: synthesize a brief conclusion from the final scenes.
* **Live transcript fetch on the deployed site — no corpus in the repo (TODO).**
  Now that `story/` is untracked (copyrighted prose isn't checked in), a deployed
  site must obtain verbatim quotes / direct story references by fetching lines
  **live from sekai.best at click-time**, not by bundling transcripts. The infra
  largely exists — see `docs/derived-hosting.md`: `scene_sources.json`
  (`"arc/episode" -> {bundle, scenario_id, region}` — coords, NOT prose),
  `GET /api/scene`, and the UI's `openLiveScene`. TODO: validate end-to-end with
  real egress; ensure **every** citation carries live-fetch coords — events **and**
  card/area (their bundle/scenario ids: card `character/member/<bundle>/<sid>`,
  area `scenario/actionset/group<id//100>/<sid>`); make live-fetch the default
  quote path when `story/` is absent, falling back to the on-disk `.md`/`.md.en`
  for local dev. Lets the site show direct story references without rehosting
  SEGA/Colorful Palette's text.
* **Continuous daily ingestion + incremental re-embed (deferred).** The game ships
  a new event ~every 15 days; stand up a scheduled job that fetches new events
  (`indexer fetch --skip-existing`), ingests + embeds only the deltas (the
  inherited `IngestionManifest` content-hashing already supports idempotent
  incremental ingest), and refreshes the index — so chat/RAG stays current without
  a full re-embed. Mirror the original repo's always-fresh RAG loop; pair with the
  timeline's live master-DB read so timeline and chat never drift.
* **Game-style event timeline scrolling (webapp, deferred).** Rework the event
  timeline scroll to feel like Project Sekai's in-game event list — smooth
  momentum/inertia scrolling, snap-to-card, and the banner art (now on each row)
  as the visual anchor. Currently a plain vertical list (`renderTimeline` +
  `.event-card` in `webapp/static/`).
* **Timeline-click quick-action upsell above the composer (webapp, deferred).**
  Clicking an event in the timeline already sets the sticky chat focus
  (`sessions.py` focus + `renderTimeline` selection). Add a UX layer: when an event
  is focused, show a small "upsell" bar just above the composer with one-tap
  prompt buttons scoped to that event — e.g. **"Summarize this event"**,
  **"What's the conclusion?"**, **"Who's the focus character?"** — that fill/submit
  the composer with the corresponding query for the focused arc. Front-end work in
  `webapp/static/app.js` (render the action bar on focus-change, wire clicks to the
  existing ask flow) + `styles.css`; no backend change (the focus arc is already
  tracked and the queries route through the normal `/api/query` path). Dismiss the
  bar when focus is dropped (soft-scope self-heal).
* **Per-unit Virtual Singer identity for name highlighting (deferred, needs data
  spike).** The six Virtual Singers (Miku, Rin, Len, Luka, MEIKO, KAITO) are not a
  single character each — in-game they're **separate per-unit variants** with
  distinct icons/designs (Leo/need Luka ≠ Nightcord Luka ≠ VBS Luka, etc.). Our
  current name highlighting / character tagging collapses each VS into one entity,
  so a highlighted "Luka" can't show the right unit-flavored identity.
  - **Step 1 (data spike):** explore the master DB to see if the variants are
    modeled as distinct characters in the *data* — check `gameCharacters.json`,
    `gameCharacterUnits.json` / `character2ds.json` (2D model = unit-specific VS
    costume) and how scenario `TalkData` references a speaker (character id vs.
    2d-model id vs. plain display name). If the scenario carries a unit-specific
    id, we can map each VS line to its unit deterministically and render/highlight
    accordingly (icon + unit label).
  - **Step 2 (fallback heuristic):** if the text/data only gives a bare VS name,
    infer the unit from context — the event's owning unit, the Sekai the scene is
    set in, and co-present unit members in the scene — to guess which VS variant is
    speaking. Lower confidence; flag ambiguous cases rather than mislabel.
  Touches `source/` (parse/keep the speaker id if present), `models/story.py`
  (speaker→unit), and the webapp highlighter.
* **Richer summary display in chat (webapp, deferred).** The hierarchical event
  summary has fixed sections (Overview, per-episode index, character development),
  currently rendered as one flat markdown block. Give it a richer interface — e.g.
  **tabs** (Overview / Episodes / Characters) or collapsible sections — so a long
  summary is skimmable rather than a wall of text. The section structure is already
  parsed (`indexer/summary_sections.py::extract_summary_sections`; the webapp gets
  labeled `summary.sections`), so this is a front-end rendering upgrade in
  `app.js` (`renderAssistant` / the summary-section renderer), no backend change.
* **Official English story quotes (done — sidecars fetched + committed).**
  Answers attach the *verbatim* official-EN line to each citation (`quote_en`,
  shown in the transcript sidebar) instead of only the LLM's paraphrase, with JP as
  the source-of-truth fallback. The full EN fetch has been run and the 1534
  `*.md.en` sidecars are committed, so `quote_en` is populated in-repo (no live
  fetch needed). Pipeline: `constants.EN_ASSET_CDN` +
  `client.en_event_scenario`/`en_unit_story_scenario` (best-effort, `{}` when a scene
  isn't localized) → `transform.align_en_to_jp` (1:1 by `TalkData` index, count-guarded)
  → `fetcher` writes a co-located `foo.md.en` sidecar (off the `*.md` glob so it's
  never indexed as JP; backfills onto an existing corpus under `--skip-existing`) →
  `query/official_en.load_official_en` builds the JP→EN line map → the webapp attaches
  `quote_en` in `_finalize_citations`. Sidecars are already populated in-repo; a
  future `indexer fetch` refreshes them as new events localize.
  Follow-up: replace the *inline* answer quote (still the LLM's translation) with the
  official EN verbatim (the sidecars are now present).
* **Full multi-language support (deferred).** Make a chosen display language (EN
  where localized, JP fallback; later TW/KR) consistent across *every* surface, not
  just the transcript sidebar:
  - **raw transcripts / sidebar** — done: `episode-raw` prefers the `.md.en`
    sidecar and `/api/scene` prefers the EN CDN, with JP fallback + a language label.
  - **quotes / citations** — partial: inline `quote_en` shows the official EN line;
    the in-body highlight matches the shown language. Unlocalized scenes fall back to JP.
  - **event / song names in the timeline** — partial: `_overlay_en_titles` overlays
    official EN names (keeps `*_jp`).
  - **episode titles in citation labels** — done: `_overlay_en_titles` attaches
    `episode_titles_en` (from `client.en_episode_titles`) and the engine's
    `_episode_title` prefers it, so Sources show e.g. "Ep 1. A Melody That Doesn't
    Connect"; JP H1 fallback when unlocalized or egress is blocked.
  - **model responses** — English-only today (the generator is pinned to English via
    `answer_system.md`); real multi-language means generating in the selected locale.
  - **summaries** — English-only (the hierarchical summarizer writes English).
  A full impl needs a **language selector** + per-locale assets threaded through
  fetch → index → summaries → generation → UI. The EN/TW/KR CDNs + master DBs already
  exist (`REGION_DBS`, `EN_ASSET_CDN`), so the data is available; the work is plumbing
  the locale everywhere and (for answers/summaries) generating per-locale.
* **EN exact-line highlight in the derived (public) backend (deferred — has a cost).**
  `/api/scene` picks the exact quoted line lexically, which fails for pure-English
  questions (EN query tokens vs JP transcript — the same gap the local backend
  bridges with query translation). Fix: run `query.translate.translate_to_japanese`
  on the question inside `_fetch_scene_live`'s line-pick (retrieval/answers stay
  keyless). **Caveat:** this adds a per-click Gemini translation call → real API
  cost on a public host (cache per query; consider rate-limiting). Until then the
  scene still loads live for EN queries, just without the specific line highlighted.
* **Lyrics analysis via chat (deferred).** Let the chat answer about a
  commissioned song's lyrics — e.g. "what do the lyrics of BAKENOHANA mean / how
  do they tie to the event?" Needs a lyrics source (not in the current master-DB
  ingest — song jacket/title/composer are captured, lyrics are not), then a
  retrieval/answer path that links `song_title` ↔ event ↔ lyrics.
* **Character-persona chat mode (webapp, deferred).** Let the web-app chat answer
  *in character* — pick a Sekai character (e.g. Miku, Kohane, Tsukasa) and have
  answers written in their voice/speech style for more fun, in-world discussions,
  rather than the current neutral narrator. Persona = a per-character system-prompt
  overlay (tone, verbal tics, relationships) layered on top of the grounded-answer
  prompt (`query/generate.py::_STYLE`), selectable via a UI chip; still quote- and
  citation-grounded so it stays faithful to the source. Character roster + JP names
  already exist in `source/constants.py` (`CHARACTER_ID_TO_JP`) and nicknames in
  `source/nicknames.py`.
* **Agentic-lite scene selection (local backend, deferred).** A scoped content
  query currently feeds the WHOLE event (budget-bounded, head+tail) to the answer
  — complete for small events, but blunt for large scopes. A better design lets
  the model examine a compact scene "table of contents" (episode title + speakers
  + first line) and fetch only the scenes it needs (reuse the full engine's
  `get_scene` tool pattern). Deferred: costs an extra LLM call per turn. Cheaper
  deterministic alternative if precision is needed first: intent-directed ranking
  (bias "climax/ending" → late episodes, "beginning" → early) — no extra call.
* Card/Area **summarization** (Phase 5 remainder — fetch/resolvers/nesting done):
  card per-card with arcs, area per-event-group, via a batched workflow.
* Full-engine: join `events_index` plot_weight into node metadata at ingest so
  the boost applies there too; inject `chroma_where(scope)` into the query.
* The inherited test suite is still Hasunosora-shaped (needs chromadb to even
  collect); Sekai tests are the `test_sekai_source/local_query/scoping/eval_local/
  webapp_api/content_and_summaries` files.
