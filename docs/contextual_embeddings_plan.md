# Scope: Contextual Embeddings (nickname + focus-event context in the index)

> **Status.** Tier A is **implemented**. The **local** backend gets it for free
> (folded into the in-memory TF-IDF at load — `query/context.py` + `query/local.py`,
> no re-embed). The **full** backend injection is **wired but dormant**: the prefix
> is prepended to the Chroma embedding + lexical text (`cli.py::_embedding_document`),
> and it takes effect only after a **user-triggered re-embed** — run
> `indexer ingest` (re-embeds every node via Google embeddings; needs
> `GOOGLE_API_KEY`; costs one full pass over the corpus). Tier B (LLM-generated
> per-chunk context) remains a future option.


**Goal.** Make event **nicknames** (`airi1`) and **focus-event context** ("Airi
Momoi's 1st focus event"), plus unit / event name / commissioned song,
*semantically searchable*. Today the embeddings don't know any of this, so
`airi1`, "Airi's first event", or "MORE MORE JUMP's independence arc" don't match
the right scenes on their own — retrieval only lands them via the deterministic
scope resolver, not by meaning. This is Anthropic's **Contextual Retrieval**
pattern (prepend chunk-situating context before embedding), in a Sekai-specific,
mostly-deterministic form.

Companion to the scope fix (which *confines* retrieval once an entity is
resolved). This makes retrieval *find* the entity by meaning in the first place.

## What exists today (anchors)

* Embedding input is **already header-augmented**: `_embedding_document(node,
  glossary)` (`cli.py:235`) prepends a static location header — `Year: {arc_id}`,
  `Story type`, `Episode`, `Part`, `Scene span`, `Speakers`, `Aliases` — then the
  raw scene text. Summary nodes (levels 1–3) use `_summary_location_header`
  (`cli.py:218`).
* Chroma stores **raw `node.text` as the document** (`cli.py:338`) but embeds the
  **header+text** (`cli.py:349`). So the situating header shapes the vector
  without polluting the displayed excerpt. This is the extension point.
* `StoryMetadata` has **no** nickname / focus fields (`models/story.py:31`). The
  join key is `arc_id == arc_slug` → `events_index.json`, which *does* carry
  `nickname`, `focus_character`, `focus_character_id`, `focus_index`, `name`,
  `unit`, `song_title`, `outline` (built in `catalog.py` + `nicknames.py`).
* The shared prompt (`source/summary_prompt.py::build_prompt`) already accepts
  `focus_id`/`song` and folds them into the **summary prompt** — but that context is
  used only at summarization time, never carried into the embedding header or
  metadata.
* Two lexical systems exist and both would need the same prefix for parity:
  full-engine `LexicalIndex` (`cli.py:788`) and the local backend's in-memory
  TF-IDF built from `node.text` (`local.py:75`).
* Query side embeds the **raw question** with no header (`engine.py:461`).
  Contextual Retrieval contextualizes *documents only* — so **no query-side
  change is needed** (asymmetry is expected for `RETRIEVAL_DOCUMENT` vs `_QUERY`).
* No contextual-retrieval logic exists yet (grep clean).

## Approach — two tiers

### Tier A — deterministic contextual prefix (recommended first; = the literal ask)

Build an `arc_id → situating sentence` map from `events_index.json` and inject it
into the embedding header (and both lexical indices). No LLM, fully deterministic.

Example prefix for `0005-kokokara-re-start`:
> `Event: ここからRE:START！ (airi1) — Airi Momoi's 1st focus event, MORE MORE JUMP!. Commissioned song: <title>.`

Changes:
1. **New helper** `arc_context_line(arc_id, events_index) -> str` (new small
   module, e.g. `source/context_prefix.py`, or beside `summaries.py`). Pure,
   unit-testable. Omits focus clause for non-focus events (`focus_character_id==0`,
   `catalog.py:123`); keeps name + unit.
2. **Inject into embeddings**: prepend the line in `_embedding_document`
   (`cli.py:235`, both the level-4 and summary branches) — one line above the
   existing header.
3. **Contextual BM25 / lexical parity**: prepend the same line to the text fed to
   `LexicalIndex` (`cli.py:788`) and to the local TF-IDF source (`local.py:75`,
   in `LocalQueryEngine.__init__` tokenization) so lexical retrieval gains the
   same nickname/focus signal. (Anthropic: contextual BM25 stacks with contextual
   embeddings — the two together cut their retrieval-failure rate ~49%.)
4. **No query-side change.**

### Tier B — Anthropic full (optional follow-on)

Per-chunk **LLM-generated** situating sentence (50–100 tokens) that also
summarizes what the *scene* contributes to its event, with prompt caching. Larger
general-recall gain for thematic queries, but adds per-chunk LLM cost + latency +
nondeterminism at ingest. Only pursue if Tier A's structured prefix leaves
thematic recall wanting. Cache generated context to a JSON (like
`event_summaries.json`) so re-ingest is cheap and deterministic thereafter.

## Critical gotcha — forcing a re-embed

Changing the header does **not** change `node.text` or the file hash, so the
incremental manifest (`hash_files` → `write_manifest`, `cli.py:707/841`) will
**skip** re-embedding and the index won't update. Must force one of:
* add an **embedding/context version** constant (mirror `CHUNKER_VERSION`,
  `chunker.py:5`) folded into the manifest hash, so a version bump invalidates
  every node; **or**
* a one-time full `--prune` re-ingest.

Re-embedding is a one-time cost over the whole corpus (~211 events × scenes) via
Google embeddings — needs `GOOGLE_API_KEY`.

## Testing

* **Deterministic, offline (CI):**
  * `arc_context_line` unit tests (focus event, non-focus event, missing arc).
  * `_embedding_document` includes the nickname/focus line for a level-4 node
    whose `arc_id` is in the index (stubbed events_index).
  * **Local backend** retrieval cases (no key): after prefixing `local.py`'s
    TF-IDF, `airi1` / "Airi's first focus event" / "MORE MORE JUMP" retrieve
    `0005-kokokara-re-start`. Add to `eval/golden_local.json`.
* **Keyed / live (like the scope verification):** before/after top-hit arc for
  `{"airi1", "Airi's first focus event", "MMJ independence"}` against the real
  Chroma index; add a small full-engine retrieval golden.

## Risks / constraints

* **Header bloat** dilutes the scene's own semantics — keep the line short
  (≤ ~40 tokens); don't dump the whole `outline`.
* **Consistency**: the *same* builder must feed embeddings + both lexical indices,
  or they diverge.
* **Determinism** preserved in Tier A; Tier B must cache generated context.
* Purely additive — the stored Chroma *document* stays raw `node.text`, so
  citations/excerpts are unchanged.

## Effort & recommendation

Tier A is **small–medium**: one pure helper + 3 injection points (embeddings,
LexicalIndex, local TF-IDF) + a version bump + tests + one keyed re-ingest & live
eval. Tier B is larger (LLM pass + cache + prompt + ongoing cost).

**Recommendation: do Tier A.** It delivers exactly the requested behavior
(nicknames + "character X's Nth focus event" become searchable), is
deterministic, is testable offline for the local backend, and composes with the
scope fix. Defer Tier B unless thematic recall specifically needs it.
