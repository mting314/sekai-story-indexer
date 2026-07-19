# sekai-story-indexer

Hierarchical-RAG story indexer for **Hatsune Miku: Colorful Stage! (Project
Sekai)**. Fork of [`linkura-story-indexer`](https://github.com/ahuei123456/linkura-story-indexer),
retargeted from Hasunosora to Project Sekai. See **[PLAN.md](PLAN.md)** for the
roadmap, **[DESIGN.md](DESIGN.md)** for architecture rationale, and
**[CLAUDE.md](CLAUDE.md)** for a quick orientation.

## Fetching story data

Story text comes from the Sekai-World master DB + sekai.best asset CDN (same
source as the `autosub` project's `fetch_event.py`):

```bash
indexer fetch --limit 5          # earliest 5 events, for a smoke test
indexer fetch                     # all events
indexer fetch --event-id 151      # a specific event
```

This writes `story/<unit>/<content_type>/<arc_slug>/<NN_episode>.md`,
auto-generates `story_order.yaml` in chronological (release) order, and writes
`events_index.json`. The CDN hosts are external — run where egress is allowed.

Then run `indexer ingest` / `indexer query` as below.

## Run it now (no API key needed)

Two CLIs: **`indexer`** = full Google/Chroma RAG (needs deps + `GOOGLE_API_KEY`);
**`sekai`** = dependency-light, no-API path (lexical engine) that runs anywhere.

```bash
uv sync --extra web

# ask from the terminal (local lexical engine; supports nickname scoping)
sekai ask "What happens in koha1?" --story-root sample/story --events-index sample/events_index.json

# launch the web app (chat + timeline) against the bundled sample corpus
sekai serve --port 8000 --story-root sample/story --events-index sample/events_index.json
# -> http://127.0.0.1:8000

# regression eval (deterministic; non-zero exit on regression)
sekai eval --golden eval/golden_local.json
```

Point `--story-root`/`--events-index` at your real `story/` + `events_index.json`
(from `indexer fetch`) once you've fetched. For the production RAG answer quality,
run `sekai serve --backend full` with the full deps + `GOOGLE_API_KEY`.

## Tests & evals

```bash
uv run pytest                 # unit + API + regression-eval tests
sekai eval                    # standalone regression gate
```

Regression evals live in `eval/golden_local.json` and run against the local
backend in `tests/test_eval_local.py`, so retrieval/scoping/answer regressions
fail CI. See `DESIGN.md` for the two-backend + eval strategy.

---

_(Inherited linkura usage follows; env vars, commands, and prompts are shared.)_

## Generation Models

Ingest summarization and `extract-state` world-state generation use the
configured generation provider:

- `SEKAI_INGEST_PROVIDER=google|openai` (default: `google`)
- `SEKAI_INGEST_MODEL=<model name>`
- `OPENAI_BASE_URL=<compatible endpoint>` (optional, for OpenAI-compatible Chat
  Completions endpoints)

If `SEKAI_INGEST_MODEL` is unset, generation falls back to
`SEKAI_CHAT_MODEL`, then the repo default Gemini chat model. Query answer
generation uses the same configured provider and model, and LLM routing uses
the same provider. When
`SEKAI_INGEST_PROVIDER=openai`, set `SEKAI_INGEST_MODEL` or set
`SEKAI_CHAT_MODEL` to an OpenAI model name so the default Gemini model is not
sent to OpenAI. Google generation requires `GOOGLE_API_KEY`. OpenAI generation
requires `OPENAI_API_KEY`.

Embeddings are separate and still use the Google GenAI embedding path controlled
by `SEKAI_EMBEDDING_MODEL` (default: `gemini-embedding-2`). Running `ingest`
and retrieval through `query` or `chat` therefore always requires
`GOOGLE_API_KEY`, even when `SEKAI_INGEST_PROVIDER=openai`.

## Querying

By default, `query` and `chat` use raw hybrid retrieval only. The default query
path retrieves raw source evidence with `summary_level: 4`, does not add
analyzer-derived metadata filters, and does not perform summary-tier retrieval
or summary fanout.

Use `--routing-mode heuristic` when you want targeted scoped workflows that
apply analyzer-derived filters or structured helpers. Use
`--routing-mode llm_router` to let the configured router model select one
typed query tool before retrieval. Use `--routing-mode agentic` for a capped,
multi-step tool-calling loop that can combine glossary lookup, retrieval,
point-in-time State Ledger checks, and exact SQL dialogue counts. Counting
questions in agentic mode must use `count_dialogue`; the answer is never an
LLM estimate. Agentic mode uses at most eight model requests by default; set
`SEKAI_AGENT_REQUEST_LIMIT` to change the cap. The router model is controlled
by `SEKAI_ROUTER_MODEL`. With the Google provider it defaults to
`gemini-3.1-flash-lite-preview`; with the OpenAI provider it defaults to the
configured generation model.

```powershell
indexer query "What happened in the 105th term?" --routing-mode heuristic
indexer chat --routing-mode llm_router
indexer query "How many dialogue turns does 花帆 have in 103?" --routing-mode agentic
```

Add `--audit` to `query` or `chat` for a secondary answer check against the
retrieved evidence, State Ledger, and official Glossary. Audit is opt-in for
interactive use and reports possible retcons, wrong honorifics, or
hallucinated names without changing the draft answer.

```powershell
indexer query "What does 花帆 call Sayaka in episode 1?" --routing-mode agentic --audit
indexer chat --routing-mode agentic --audit
```

Use `--routing-mode off` to make the default explicit:

```powershell
indexer query "What happened in the 105th term?" --routing-mode off
indexer chat --routing-mode off
```

### Answer prompts

Static answer-generation instructions are Markdown resources in
`src/sekai_story_indexer/prompts`. Edit those files to revise answer policy;
keep questions, retrieved context, glossary entries, and State Ledger facts as
dynamic renderer inputs. Increment `PROMPT_VERSION` in the prompt module for a
behavioral change so evaluations and future traces can identify the revision.
When `summaries_cache.json` is present, every valid Year-level summary is also
loaded into the system prompt as a generated Story Overview. These summaries
can ground broad Year/Arc synthesis; narrower claims should continue drilling
down to Episode, Part, or raw-scene evidence.

Run the prompt and query-engine tests after a prompt change:

```powershell
uv run pytest tests/test_prompts.py tests/test_query_engine.py
```

## Retrieval Evaluation

Run the checked-in retrieval golden set without answer generation:

```powershell
uv run indexer eval run --golden-set eval/golden_questions.json --routing-mode off --output runs/baseline.json
```

Use `--routing-mode heuristic` to compare analyzer-derived filters,
`--routing-mode llm_router` to evaluate typed router dispatch, or
`--routing-mode agentic --answer-mode` to evaluate the multi-step agent. Add
`--audit` to the answer-mode run to aggregate audit cleanliness and flag counts.
The audit flag requires answer mode. Compare router and agentic runs with the
same golden set:

```powershell
indexer eval run --routing-mode llm_router --answer-mode --output runs/router.json
indexer eval run --routing-mode agentic --answer-mode --output runs/agentic.json
indexer eval diff runs/router.json runs/agentic.json
```

Until the reranker lands, reranker metrics are emitted as unavailable. The
current golden set remains single-scene; multi-hop and expanded quantitative
coverage should be added in a follow-up evaluation issue.

## Index Rebuilds

Raw evidence is embedded as coalesced retrieval chunks over adjacent source
scenes. The original parsed scene indexes remain in metadata as
`scene_start`, `scene_end`, and `source_scene_count` for citations.

Changing retrieval chunk thresholds or raw metadata schema requires rebuilding
the Chroma index, or pruning stale vectors once Task 9 stale-vector pruning
exists. Otherwise older one-scene records can remain active beside the new
chunk IDs.

Changing the embedding model or embedding input format also requires rebuilding
the Chroma index. The default `gemini-embedding-2` path uses inline retrieval
instructions (`title: ... | text: ...` for documents and
`task: search result | query: ...` for queries), which should not be mixed with
older vectors created from raw text or from a different embedding model.

## Summary Reader

Export the cached Year, Episode, and Part summaries to a static reader:

```powershell
.\scripts\export-summary-reader.ps1
```

```bash
./scripts/export-summary-reader.sh
```

Preview locally with:

```powershell
.\scripts\export-summary-reader.ps1 -Serve -Port 8000
```

```bash
./scripts/export-summary-reader.sh --serve --port 8000
```

Deployment notes for self-hosting and GitHub Pages are in
`docs\summary-reader-deploy.md`.

The original production page from `Linkura Summaries.zip` is also preserved
under `web\summary-reader-production`. Export that variant with:

```powershell
.\scripts\export-production-summary-reader.ps1
```

```bash
./scripts/export-production-summary-reader.sh
```
