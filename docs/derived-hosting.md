# Copyright-clean public hosting: derived index + live quotes

## Goal / constraint
Host the chat publicly **without rehosting SEGA / Colorful Palette's copyrighted
story prose**. The server may hold only *derived* data; the readable transcript is
fetched live from sekai.best when a user opens a citation.

## What's safe to host vs. not
| Data | Hostable? | Why |
|---|---|---|
| TF-IDF index (per-scene token counts, IDF) | ✅ | derived numbers, not prose |
| Event/scene metadata (titles, unit, dates, nickname) | ✅ | already public via sekai.best |
| Our hierarchical summaries (`summaries_cache.json`) | ✅ | our own generated/transformative text |
| Scene fetch coords (asset_bundle, scenario_id, region) | ✅ | addressing info, not content |
| **Readable transcript prose** (`story/**/*.md`, `.md.en`, excerpts) | ❌ | the copyrighted content — never store/serve |

## Architecture
1. **Retrieval** runs on the derived index (counts) → returns scene *refs* (arc,
   episode), scores, labels, and fetch coords. **No prose in the response.**
2. **Answers** (keyless) are built from our summaries + scene refs, not from
   embedded scene text.
3. **Display / quotes**: when the user opens a citation, the browser (or a thin
   server proxy) fetches that one scene's `.asset` live from sekai.best and renders
   it. The prose transits, it is never stored by us.

## Phases
- **Phase 1 (this doc's companion PR — testable offline):** the prose-free derived
  index. `query/derived_index.py`: `build_derived_index(nodes, events_index)`
  serializes `{scenes: [{id, arc_id, episode, unit, label, plot_weight, tf}],
  idf}` with **no text**; `score_query(index, q)` ranks scenes by TF-IDF. Tests
  assert correct ranking **and** that no prose field exists in the artifact/output.
- **Phase 2 (code landed; live build/validation needs egress):** `indexer fetch`
  now writes `scene_sources.json` (`"arc/episode" -> {bundle, scenario_id, region}`
  — coords, not content). The webapp `GET /api/scene?arc=&episode=` resolves those
  coords and fetches the transcript **live from sekai.best, transiently (never
  stored)** — a thin proxy (client-direct would be cleaner but depends on sekai.best
  CORS). Building `scene_sources.json` for real + validating the live fetch need a
  machine with sekai.best egress.
- **Phase 3 (landed):** `SEKAI_QUERY_BACKEND=derived` serves retrieval over the
  derived index (`query/derived_index.py` + `_query_derived`), answers from our
  summaries, and citations carry sekai.best coords; the UI's `openLiveScene` fetches
  the scene live and highlights the exact line. Build the artifact with
  `sekai build-index` (→ `derived_index.json.gz`, no egress needed) and deploy with
  `Dockerfile.derived`, which ships `derived_index.json.gz` + `summaries_cache.json`
  + `events_index.json` + `scene_sources.json` + `glossary.json` — **no `story/`**,
  zero transcript prose in the image.

## Deploy (prose-free public)
```bash
uv run sekai build-index                       # -> derived_index.json.gz (from local corpus)
docker build -f Dockerfile.derived -t sekai-public .
docker run --rm -p 8000:8000 sekai-public      # SEKAI_QUERY_BACKEND=derived
```
The running host needs sekai.best egress (for the live-scene fetch). Answers are
summary-grounded + scene-precise; clicking a citation reads the exact line live.

## Note on the existing Dockerfile
The current `Dockerfile` bakes the full corpus — that's fine for **private/localhost**
self-hosting, but must NOT be used for a public deploy. Phase 3 replaces its data
COPY with the derived artifacts only.
