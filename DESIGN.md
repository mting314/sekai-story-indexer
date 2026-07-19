# Design Notes — Project Sekai Story Indexer

Decisions and rationale that don't fit in PLAN.md's checklist. Read alongside
`PLAN.md`.

## Per-unit vs unified index (the brainstorm question)

**Decision: one unified index + a `unit` metadata facet. Do NOT build five
separate projects/models/indices.**

The instinct to split by unit is understandable — each unit *reads* like a
self-contained story. But splitting is the wrong cut:

* **Crossovers break it.** Mixed events, Virtual Singer events, and especially
  **World Link / "Sekai" arcs** deliberately interleave units. A hard per-unit
  split has nowhere to put them and can't answer "when did Leo/need and MMJ
  interact?" Five indices means five places to look and manual union logic.
* **Maintenance multiplies.** Five ingest configs, five Chroma collections, five
  eval sets, five State Ledgers to keep coherent. The game ships events
  continuously; 5× the surface area is 5× the drift.
* **You get unit scoping for free anyway.** linkura already filters on
  `story_type` (Main/Side). We extend that exact pattern with `unit`. A
  `--unit leo_need` query is just a metadata filter on one collection — the
  "separate interface per unit" the brainstorm wanted, without the duplication.

**What we keep from the idea:** unit-scoped *views*. `--unit <slug>` narrows
retrieval; a per-unit **summary rollup** (Phase 2) gives each unit its own
"overall arc" Tier-1 summary. If a single collection ever becomes a scaling
problem, Chroma supports per-unit collections behind the same `unit` filter API —
so this stays a reversible, internal decision, not an architecture commitment.

## Tier mapping (why `unit` is a facet, not the top tier — yet)

linkura's tiers are Year ⊃ Episode ⊃ Part ⊃ Scene. Sekai wants Unit ⊃ Event ⊃
Episode ⊃ Scene — one level deeper. Rather than rewrite the summarizer up front,
Phase 1 maps **Event → `arc_id` (the existing "Year"/Volume tier)** and carries
`unit` as a facet. This keeps the whole inherited pipeline running immediately.
The genuinely new tier — **Unit-level synthesis above Event** — is the first
Phase-2 addition, built by rolling up event summaries per `unit`.

Consequence: the "Part" tier is currently degenerate (one file per episode, so
`part_name == episode_name`). Harmless duplicate summary work; revisit if
multi-file episodes appear.

## The three challenges — where each is handled

| Challenge | Mechanism | Phase |
|---|---|---|
| **Length** | inherited tiered summaries + idempotent manifest ingest + new Unit/global-timeline top summaries | 1 (base), 2 (top tiers) |
| **Quantity (5 units)** | `unit` facet + `--unit` scoping + data-driven chronological `story_order.yaml` | 1 (facet), 4 (scoping UX) |
| **Filler vs plot** | index everything; `plot_weight` LLM tag; retrieval boost for thematic queries, full recall for factual ones | 3 |

### Native "key story" signal (`is_key_story`)
The game/sekai.best already tags events via `eventStoryUnits.json`: an event is a
"key story" (`isKeyEventStory`) when it has a `main`-relation unit. We ingest
this as `is_key_story` — a **prior**, stored separately from our own
`plot_weight`, because the native tag is overinclusive (nearly every unit event
qualifies, filler included). So:

* `is_key_story` = raw native signal, always captured at fetch time.
* `plot_weight` = **our** classifier's verdict (Phase 3), the final say. It uses
  `is_key_story` as one input feature but can downgrade an "overzealous" key
  event to `filler`, or upgrade a non-key event that actually moves an arc.
* Interim (before the classifier): retrieval may use `is_key_story` as a light
  boost so thematic queries already favor main-arc events.

`eventStoryUnits` is also the **authoritative unit resolver** (main-relation
unit), superseding the earlier character-count heuristic (kept only as a
fallback when the table is unavailable).

### On filler specifically
The requirement is explicit: **include everything, but focus on character
development and plot progression.** So `plot_weight` never *excludes* — it's a
ranking signal. Two query intents, two behaviors:

* *Thematic / arc* ("how does Kohane grow across VBS?") → boost `high`/`medium`.
* *Factual / exhaustive* ("what happens in <filler event>?") → no boost, full
  recall; filler is right there at Tier 4.

State Ledger extraction (the expensive LLM pass) is where we actually *save*
effort on filler: prioritize `high`/`medium` episodes for fact extraction, since
filler rarely changes canonical world-state.

## Source & translation

* JP (`sekai-jp-assets`) is source of truth. EN CDN lags and is incomplete, so
  translation is an LLM step downstream (inherited translation + audit loop),
  grounded by `glossary.json` + the State Ledger to prevent retcons and honorific
  drift.
* Speaker labels come straight from `TalkData[].WindowDisplayName`; narration has
  an empty speaker and is written as a bare line (parsed as a narrative beat).

## Non-goals (for now)
* No EN-asset ingestion (translate JP instead).
* No card/area/unit-story fetch yet (Phase 5) — event + main stories are the
  plot core.
* No web summary reader retheme yet (inherited linkura reader still ships).
