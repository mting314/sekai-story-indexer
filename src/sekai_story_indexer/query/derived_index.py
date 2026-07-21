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

import gzip
import json
from pathlib import Path
from typing import Any

from ..source.relevance import weight_factor
from .local import LocalQueryEngine, build_local_engine, tokenize


def build_derived_index(
    engine: LocalQueryEngine, scene_sources: dict[str, dict] | None = None
) -> dict[str, Any]:
    """Serialize an engine's derived retrieval data — counts + idf + expansions +
    scene metadata (+ optional sekai.best fetch coords) — with NO prose. Safe to
    host publicly. ``scene_sources`` maps ``"arc/episode" -> {bundle,...}`` so the
    public UI can fetch a cited scene live for display."""
    scene_sources = scene_sources or {}
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
                "source": scene_sources.get(f"{m.arc_id}/{m.episode_name}"),  # live-fetch coords
                "tf": dict(engine._tf[i]),  # {token: count} — derived, non-reversible
            }
        )
    # expansions as [[sorted trigger tokens], [additions]] — JSON-friendly
    expansions = [[sorted(trig), list(adds)] for trig, adds in engine._expansions]
    return {"version": 1, "scenes": scenes, "idf": dict(engine._idf), "expansions": expansions}


def write_derived_index(index: dict[str, Any], path: str | Path) -> Path:
    """Write the derived index, gzip-compressed if the path ends in .gz (the tf
    dicts compress ~5x). Returns the path written."""
    path = Path(path)
    blob = json.dumps(index, ensure_ascii=False).encode("utf-8")
    if path.suffix == ".gz":
        path.write_bytes(gzip.compress(blob))
    else:
        path.write_bytes(blob)
    return path


def load_derived_index(path: str | Path) -> dict[str, Any]:
    """Load a derived index written by :func:`write_derived_index` (.json or .gz)."""
    path = Path(path)
    raw = path.read_bytes()
    if path.suffix == ".gz":
        raw = gzip.decompress(raw)
    return json.loads(raw)


def build_index_file(
    story_root: str | Path = "story",
    *,
    events_index_path: str | Path = "events_index.json",
    scene_sources_path: str | Path = "scene_sources.json",
    glossary_path: str | Path = "glossary.json",
    out_path: str | Path = "derived_index.json.gz",
) -> Path:
    """Build the prose-free derived index from the local corpus + fetch coords and
    write it. Run where the corpus exists; the output ships to the public host."""
    events_index = json.loads(Path(events_index_path).read_text(encoding="utf-8"))
    glossary = None
    if Path(glossary_path).exists():
        glossary = json.loads(Path(glossary_path).read_text(encoding="utf-8"))
    scene_sources = {}
    if Path(scene_sources_path).exists():
        scene_sources = json.loads(Path(scene_sources_path).read_text(encoding="utf-8"))
    engine = build_local_engine(Path(story_root), events_index, glossary)
    return write_derived_index(build_derived_index(engine, scene_sources), out_path)


def _expand(tokens: list[str], expansions: list[list[list[str]]]) -> list[str]:
    present = set(tokens)
    out = list(tokens)
    for trigger, additions in expansions:
        if set(trigger) <= present:
            out.extend(additions)
    return out


def score_query(
    index: dict[str, Any],
    question: str,
    *,
    top_k: int = 5,
    aux_query: str = "",
    arc_ids: tuple[str, ...] = (),
    unit: str | None = None,
) -> list[dict[str, Any]]:
    """Rank scenes for a query over the derived index. Returns scene refs + scores
    with NO prose — the caller fetches the transcript live for display. Optional
    ``arc_ids``/``unit`` restrict the candidate scenes (query scoping)."""
    idf = index["idf"]
    toks = tokenize(question) + (tokenize(aux_query) if aux_query else [])
    q_tokens = [t for t in _expand(toks, index.get("expansions", [])) if t in idf]
    if not q_tokens:
        return []
    arc_set = set(arc_ids)
    ranked: list[tuple[float, dict[str, Any]]] = []
    for sc in index["scenes"]:
        if arc_set and sc["arc_id"] not in arc_set:
            continue
        if unit and sc["unit"] != unit:
            continue
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
                        "source": sc.get("source"),  # live-fetch coords for the UI
                        "score": score,
                    },
                )
            )
    ranked.sort(key=lambda r: (-r[0], r[1]["arc_id"], r[1]["episode"]))
    return [ref for _, ref in ranked[:top_k]]
