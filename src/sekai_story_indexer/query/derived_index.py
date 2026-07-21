"""Prose-free derived retrieval index for copyright-clean public hosting.

Serializes only what retrieval needs — per-scene token *counts*, IDF, the
glossary expansions, and scene *metadata* (labels/coords) — and deliberately NO
transcript prose. Bag-of-token counts (unordered CJK unigrams/bigrams) can't be
reconstructed into readable text, so this is a search index, not the content.

A public server can rank queries over this index and return scene *refs* (arc +
episode + label + score); the actual transcript is fetched live from sekai.best
when a user opens a citation (see docs/derived-hosting.md). Phase 1 here is the
offline-testable core: build + score. Fetch coords + live-quote wiring are Phase 2.
"""

from __future__ import annotations

from typing import Any

from ..source.relevance import weight_factor
from .local import LocalQueryEngine, tokenize


def build_derived_index(engine: LocalQueryEngine) -> dict[str, Any]:
    """Serialize an engine's derived retrieval data — counts + idf + expansions +
    scene metadata — with NO prose. Safe to host publicly."""
    scenes: list[dict[str, Any]] = []
    for i, node in enumerate(engine.nodes):
        m = node.metadata
        loc = engine.human_location(node)
        scenes.append(
            {
                "id": i,
                "arc_id": m.arc_id,
                "episode": m.episode_name,  # slug, for addressing the source scene
                "unit": m.unit,
                "label": loc["label"],
                "nickname": loc.get("nickname"),
                "plot_weight": engine._weight_by_arc.get(m.arc_id, "unrated"),
                "tf": dict(engine._tf[i]),  # {token: count} — derived, non-reversible
            }
        )
    # expansions as [[sorted trigger tokens], [additions]] — JSON-friendly
    expansions = [[sorted(trig), list(adds)] for trig, adds in engine._expansions]
    return {"version": 1, "scenes": scenes, "idf": dict(engine._idf), "expansions": expansions}


def _expand(tokens: list[str], expansions: list[list[list[str]]]) -> list[str]:
    present = set(tokens)
    out = list(tokens)
    for trigger, additions in expansions:
        if set(trigger) <= present:
            out.extend(additions)
    return out


def score_query(
    index: dict[str, Any], question: str, *, top_k: int = 5, aux_query: str = ""
) -> list[dict[str, Any]]:
    """Rank scenes for a query over the derived index. Returns scene refs + scores
    with NO prose — the caller fetches the transcript live for display."""
    idf = index["idf"]
    toks = tokenize(question) + (tokenize(aux_query) if aux_query else [])
    q_tokens = [t for t in _expand(toks, index.get("expansions", [])) if t in idf]
    if not q_tokens:
        return []
    ranked: list[tuple[float, dict[str, Any]]] = []
    for sc in index["scenes"]:
        tf = sc["tf"]
        score = sum(tf.get(t, 0) * idf[t] for t in q_tokens)
        if score > 0:
            # same plot-weight boost the live engine applies, for ranking parity
            score *= weight_factor(sc.get("plot_weight"))
            ranked.append(
                (
                    score,
                    {
                        "arc_id": sc["arc_id"],
                        "episode": sc["episode"],
                        "unit": sc["unit"],
                        "label": sc["label"],
                        "nickname": sc.get("nickname"),
                        "score": score,
                    },
                )
            )
    ranked.sort(key=lambda r: (-r[0], r[1]["arc_id"], r[1]["episode"]))
    return [ref for _, ref in ranked[:top_k]]
