# Tasks

> **Status (reconciled with the code, 2026-07):** this is the inherited
> `RAG_IMPROVEMENTS_4.md` roadmap from the linkura fork. **Essentially all of it is
> now implemented** — the boxes below are ticked to match what's in the code. The
> only outstanding item is the SQLite/JSONL cache migration (deferred by design).
>
> ⚠ **Caveat — implemented but not verified end-to-end here:** groups 2, 3, 5, 6,
> 11, and 13 live largely in the **full engine** (`query/engine.py`, `--backend
> full`), which needs `GOOGLE_API_KEY` + `chromadb` + a real `indexer ingest` to
> exercise. That path hasn't been run in this environment. The **local / derived**
> backends (what actually runs and is covered by tests) achieve the same ends with
> deterministic TF-IDF retrieval.
>
> This is a **legacy checklist.** The live roadmap and current Sekai-specific / UX
> work live in **`PLAN.md`**.

Ordered by priority — do top-to-bottom. Items within a group can be done together
or split.

## Phase 1 — Make Q&A honest

### 1. Soften system prompt + apply quick wins

Bundle of local edits that improve quality/perf immediately without
touching pipeline shape. Quick wins 1, 2, 6, 7, 9 from v4:

- [x] Set `EmbedContentConfig.task_type` — `RETRIEVAL_DOCUMENT` at
      ingest, `RETRIEVAL_QUERY` at query.
- [x] Batch `embed_content` calls in `database.embed_texts` (SDK
      accepts a list; current code loops one-per-text).
- [x] Cache `chromadb.PersistentClient`, the collection handle,
      `GoogleModel`, and `genai.Client` as module-level singletons.
- [x] Move `CHAT_MODEL` / `EMBEDDING_MODEL` / `CHROMA_DB_PATH` to env
      vars (`SEKAI_CHAT_MODEL`, `SEKAI_EMBEDDING_MODEL`,
      `SEKAI_CHROMA_DB_PATH`) with sensible defaults.
- [x] Soften the "strictly raw source text" claim in
      `query/engine.py` until the evidence gate (task 3) lands.
- [x] Drop `indent=2` on the State Ledger JSON dump in the system
      prompt; inject only ledger slices for arcs the question is
      actually about.

### 2. Index Tier 4 raw scenes into Chroma

Biggest single retrieval fix (v4 item 10).

- [x] Embed every raw scene with `summary_level=4`.
- [x] Metadata per scene: `arc_id`, `story_type`, `episode_name`,
      `part_name`, `scene_index`, canonical story order, parent
      part/episode/year IDs, `file_path`, detected speakers,
      `is_prose`.
- [x] Decide: extend `story_nodes` or add a `story_scenes` collection.
- [x] Fix `_node_id` so `scene_index` actually disambiguates — today
      summaries set it to `-1`, which collides once scenes are added.
- [x] Update `cli.ingest` to upsert raw scene nodes alongside
      summary nodes.

### 3. Implement evidence-gate retrieval policy

v4 item 11 plus the prompt-vs-reality section. The prompt is a lie
until this lands.

- [x] Rewrite `StoryQueryEngine.query` with fallback flow:
      1. initial retrieval
      2. if only broad summaries match, expand to child parts/scenes
         and retry against raw scenes
      3. if still no raw evidence, the model explicitly reports
         insufficient source context — no paraphrasing the summary
- [x] Replace whole-file reads in `_fetch_raw_text` with
      `(file_path, scene_index)` lookups.
- [x] Restore the "strictly raw source text" prompt once it's honest.
- [x] Split citation label (user-visible: arc / episode / part /
      scene) from citation metadata (file_path, scene_index,
      canonical order) — don't spam file paths in every answer.

## Phase 2 — Make retrieval competent

### 4. Glossary query expansion + hybrid (lexical) retrieval

v4 items 15-16. Essential for proper nouns and bilingual queries.

- [x] Add a lexical side-index (SQLite FTS is simplest) over both
      English summaries and raw Japanese scenes.
- [x] Before dense query, expand with aliases from `glossary.json`
      (e.g., "Kaho" → "Kaho Hinoshita / 花帆 / 日野下花帆").

### 5. Raise candidate recall, neighbor expansion, reranker

v4 items 12, 13, 17. Starting points (tune against eval set later):

- [x] Replace `n_results=3` with top 20-50 for routing, top 30-100
      for raw evidence, final 5-12 after rerank.
- [x] After each raw-scene hit, pull adjacent scenes from the same
      part, bounded to ±1-2 scenes.
- [x] Add a Gemini Flash reranker scoring (question, chunk) pairs
      before final top-k.
- [x] Rerank signal preferences: exact character/name match, glossary
      alias match, episode/arc constraint match, dialogue-from-
      mentioned-speakers, proximity to other high-scoring chunks.

### 6. Hierarchical routing with RRF

v4 item 14.

- [x] Run queries per tier (or per intent) plus one against raw
      scenes.
- [x] Fuse with Reciprocal Rank Fusion (no score calibration needed).
- [x] Fan out from Tier-1/2 hits to children, then re-score.

## Phase 3 — Make it correct

### 7. Query analysis + metadata filters + canonical numeric story-order

v4 items 18-19.

- [x] Lightweight first pass extracting: arc/episode constraints,
      side-vs-main, character/alias names, temporal phrases, intent
      bucket (summary / exact evidence / comparison / chronology /
      quantitative).
- [x] Parse explicit part/scene constraints such as "ABYSS scene 2",
      "scene 2", and "scenes 3-7"; treat user-facing scene numbers as
      one-based and convert to zero-based metadata internally.
- [x] For explicit scene constraints, filter or post-filter raw chunks
      by span containment (`scene_start <= requested <= scene_end`).
- [x] Handle semantic boundary constraints such as "scenes before Ruri
      falls asleep" by combining constrained retrieval, reranking, and
      canonical story order; do not treat them as simple scene-number
      filters.
- [x] Apply Chroma `where` filters from extracted constraints.
- [x] Stamp every node at ingest with a canonical numeric story-order
      field independent of filesystem traversal, respecting the
      main-vs-side interleave (sides between 104 Main and 105 Main).
- [x] **Do not** use string compare on `arc_id` for temporal filters
      — use the numeric order field.

### 8. Move story ordering to a manifest/config

v4 item 20. `episode_sort_key` hardcodes arcs 103/104/105.

- [x] Create `story_order.yaml` (or similar) declaring canonical
      order.
- [x] `summarizer.py` and `cli.ingest` read from it.
- [x] Adding a new arc must not require editing Python.

### 9. Ingestion manifest + cache versioning + stale-vector pruning

v4 items 21, 23.

- [x] Manifest fields: source file content hashes, parser version,
      summarization prompt version, glossary hash, chat/embedding
      model names, schema version, timestamp.
- [x] Manifest drives `summaries_cache.json` invalidation when any
      input changes.
- [x] Stale-vector pruning: track IDs per ingest run, delete Chroma
      records that didn't reappear (handles renames, deletes,
      re-splits — currently leaves orphans silently).

## Phase 4 — Make the data layer load-bearing

### 10. Preserve structured dialogue and narrative beats

v4 item 22. `parse_script_line` output is currently discarded.

- [x] Store structured `(speaker, text)` turns for script files.
- [x] Tag scene metadata with detected speakers.
- [x] For prose files, store quoted dialogue separately from
      narrative beats.
- [x] Unlocks: speaker-specific retrieval, "who said X?" queries,
      dialogue-vs-narrative filtering, cleaner citations,
      code-based counting (task 13).

### 11. Rebuild State Ledger with provenance, extracted from raw scenes

v4 item 24. Current merge in `extractor.py` is lossy (role replaced
by longer string, honorifics overwrite, `is_active` OR'd forever).

- [x] Replace with source-backed fact records: `(subject, predicate,
      object, arc, episode, part, scene, valid_from, valid_to,
      confidence, extracted_quote)`.
- [x] Extract from raw scenes, not generated summaries — extracting
      from summaries compounds loss and destroys provenance.
- [x] Ledger becomes routing/consistency context; final answers
      still cite source chunks.

## Phase 5 — Prove it works

### 12. Retrieval eval harness + pipeline unit tests

v4 items 25-26.

- [x] ~30 golden-set questions with expected arc/episode/scene
      sources.
- [x] Track: recall@k, reranker hit rate, answer faithfulness,
      citation correctness, temporal leakage, glossary consistency.
- [x] Use the harness to tune all knobs from v4 (candidate counts,
      rerank cutoff, neighbor width, hybrid weighting, RRF `k`,
      intent thresholds).
- [x] Extend pytest to: ingestion end-to-end, Chroma metadata shape,
      citation format round-trip, state extraction determinism on
      fixtures, answer-grounding smoke tests, cache invalidation.

## Phase 6 — Roadmap (only after Phases 1-5)

### 13. Agentic loop, quantitative queries, audit pass

v4 items 27-29. Running these over a weak index amplifies bad
retrieval — don't start until Phases 1-5 are solid.

- [x] Tool-calling agent via `pydantic-ai`. Tools:
      - `search(query, tier?, arc_id?)`
      - `get_scene(path, scene_index)`
      - `get_state(arc_id, as_of_episode?)`
      - `lookup_glossary(term)`
- [x] Quantitative queries answered from the structured dialogue
      table (task 10), not LLM inference over text.
- [x] Secondary LLM audit pass comparing draft answer to retrieved
      sources + State Ledger + Glossary; flags retcons, wrong
      honorifics, hallucinated names.

## Misc low-priority polish

- [x] Streaming for `cli.chat` instead of blocking `run_sync`.
- [ ] Migrate `summaries_cache.json` to SQLite or JSONL — **only
      after** content-hash invalidation and stale-vector pruning
      land. Format migration alone fixes nothing.

## Start-here note

Task 1 is the natural starting point: entirely local edits, no new
concepts, gets the system prompt to stop lying while Tier 4 indexing
is being built.
