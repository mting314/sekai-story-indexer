"""Plot-weight classification — our own relevance verdict per event.

The native ``is_key_story`` tag is overinclusive (see docs/DESIGN.md). ``plot_weight``
is *our* rating used to prioritize retrieval for thematic / character-arc queries
while never excluding filler.

This module ships a deterministic **heuristic** classifier that needs no LLM, so
it runs in CI / offline. The design's LLM classifier (Phase 3 proper) plugs in as
``llm_classify`` later, using these same features; until then the heuristic is
the verdict. Retrieval multiplies scene scores by ``PLOT_WEIGHT_FACTOR`` so
high-weight content ranks up and filler ranks down but stays retrievable.
"""

from __future__ import annotations

PLOT_WEIGHTS: tuple[str, ...] = ("high", "medium", "filler", "unrated")

# Retrieval multipliers. Filler is de-prioritized, never dropped.
PLOT_WEIGHT_FACTOR: dict[str, float] = {
    "high": 1.25,
    "medium": 1.0,
    "filler": 0.85,
    "unrated": 1.0,
}


def classify_event(row: dict) -> str:
    """Heuristic plot weight from catalog signals (no LLM).

    Reasoning:
      * not a key story  -> filler (side/collab/anniversary; still indexed).
      * key + single-unit + has a focus character + commissioned song
        -> high (the shape of a unit's main-arc plot event).
      * key crossover ('mixed') -> medium (plot happens but is usually lighter /
        shared, not a single unit's core development).
      * otherwise key    -> medium.
    """
    if not row.get("is_key_story"):
        return "filler"
    if row.get("unit") == "mixed":
        return "medium"
    if row.get("focus_character") and row.get("song_title"):
        return "high"
    return "medium"


def classify_catalog(rows: list[dict]) -> list[dict]:
    """Set ``plot_weight`` on each catalog row in place; return the rows."""
    for row in rows:
        row["plot_weight"] = classify_event(row)
    return rows


def weight_factor(plot_weight: str | None) -> float:
    return PLOT_WEIGHT_FACTOR.get(plot_weight or "unrated", 1.0)
