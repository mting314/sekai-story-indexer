"""Local, dependency-light query backend — no external API, fully deterministic.

Why this exists:
  * makes the chat genuinely queryable offline / in CI (no GOOGLE_API_KEY,
    no Chroma, no network), so the web app runs anywhere;
  * gives regression evals a stable, reproducible target;
  * serves as a graceful fallback when the full RAG stack isn't configured.

It does real lexical retrieval (TF-IDF over scene nodes) with unit / event /
nickname scoping and an extractive answer with citations. The production path
(Google embeddings + Gemini generation + Chroma) remains in engine.py; this is
the same query surface at lower fidelity.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from pathlib import Path

from ..indexer.processor import StoryProcessor
from ..models.story import StoryNode
from ..source.constants import UNIT_NAMES
from ..source.relevance import weight_factor
from .scoping import ScopeIndex

_WORD_RE = re.compile(r"[a-z0-9]+")
_CJK_RE = re.compile(r"[぀-ヿ㐀-鿿]")
_H1_RE = re.compile(r"^#\s*(.+)$")


def tokenize(text: str) -> list[str]:
    """ASCII words + CJK character bigrams — a language-agnostic lexical key set
    that needs no tokenizer dependency (works for JP and EN)."""
    text = text.lower()
    tokens = _WORD_RE.findall(text)
    cjk = _CJK_RE.findall(text)
    tokens += ["".join(pair) for pair in zip(cjk, cjk[1:])] if len(cjk) > 1 else cjk
    return tokens


def load_story_nodes(root: str | Path) -> list[StoryNode]:
    """Parse every ``*.md`` under ``root`` into scene StoryNodes."""
    root = Path(root)
    nodes: list[StoryNode] = []
    for path in sorted(root.rglob("*.md")):
        nodes.extend(StoryProcessor.process_file(path))
    return nodes


class LocalQueryEngine:
    def __init__(
        self,
        nodes: list[StoryNode],
        events_index: list[dict] | None = None,
        glossary: dict | None = None,
    ):
        self.nodes = nodes
        self._tokens: list[list[str]] = [tokenize(n.text) for n in nodes]
        self._tf: list[Counter] = [Counter(t) for t in self._tokens]
        df: Counter = Counter()
        for toks in self._tokens:
            df.update(set(toks))
        n_docs = max(1, len(nodes))
        self._idf: dict[str, float] = {
            term: math.log(1 + n_docs / (1 + count)) for term, count in df.items()
        }
        # shared scope resolver (nickname/unit/event) + plot-weight + human-name lookups
        self._scope_index = ScopeIndex(events_index)
        self._weight_by_arc: dict[str, str] = {}
        self._meta_by_arc: dict[str, dict] = {}
        for row in events_index or []:
            if row.get("arc_slug"):
                self._weight_by_arc[row["arc_slug"]] = row.get("plot_weight", "unrated")
                self._meta_by_arc[row["arc_slug"]] = row
        # Cross-lingual bridge: the corpus is JP but questions may be EN (or vice
        # versa). From the glossary (JP<->EN) build trigger->add token expansions
        # so a name in one language also searches the other.
        # - characters: trigger on ANY single name-token (>=3 chars), since users
        #   type "Mafuyu", not the full "Mafuyu Asahina".
        # - units/terms: require the FULL phrase, to avoid common-word triggers
        #   ("bad" in "Vivid BAD SQUAD").
        self._expansions: list[tuple[frozenset[str], list[str]]] = []
        glossary = glossary or {}
        for jp, en in (glossary.get("characters") or {}).items():
            jp_toks, en_toks = tokenize(jp), tokenize(en)
            for et in en_toks:
                if len(et) >= 3:
                    self._expansions.append((frozenset({et}), jp_toks))
            if jp_toks and en_toks:
                self._expansions.append((frozenset(jp_toks), en_toks))
        for section in ("units", "locations_and_terms"):
            for jp, en in (glossary.get(section) or {}).items():
                jp_toks, en_toks = tokenize(jp), tokenize(en)
                if jp_toks and en_toks:
                    self._expansions.append((frozenset(en_toks), jp_toks))
                    self._expansions.append((frozenset(jp_toks), en_toks))

    def _expand_tokens(self, tokens: list[str]) -> list[str]:
        """Augment query tokens with glossary equivalents whose trigger appears."""
        if not self._expansions:
            return tokens
        present = set(tokens)
        expanded = list(tokens)
        for trigger, additions in self._expansions:
            if trigger <= present:
                expanded.extend(additions)
        return expanded

    # -- scoping -------------------------------------------------------------
    def _candidate_indices(self, unit: str | None, arc_id: str | None) -> list[int]:
        out = []
        for i, node in enumerate(self.nodes):
            if unit and node.metadata.unit != unit:
                continue
            if arc_id and node.metadata.arc_id != arc_id:
                continue
            out.append(i)
        return out

    # -- retrieval -----------------------------------------------------------
    def retrieve(
        self,
        question: str,
        *,
        k: int = 5,
        unit: str | None = None,
        arc_id: str | None = None,
    ) -> list[tuple[StoryNode, float]]:
        q_tokens = [t for t in self._expand_tokens(tokenize(question)) if t in self._idf]
        candidates = self._candidate_indices(unit, arc_id)
        scored: list[tuple[float, int]] = []
        for i in candidates:
            tf = self._tf[i]
            score = sum(tf.get(t, 0) * self._idf[t] for t in q_tokens)
            if score > 0:
                # boost plot-heavy scenes, de-prioritize filler (never drop it)
                score *= weight_factor(self._weight_by_arc.get(self.nodes[i].metadata.arc_id))
                scored.append((score, i))
        # deterministic tie-break: score desc, then stable source order
        scored.sort(key=lambda s: (-s[0], self._sort_key(self.nodes[s[1]])))
        return [(self.nodes[i], score) for score, i in scored[:k]]

    @staticmethod
    def _sort_key(node: StoryNode) -> tuple:
        m = node.metadata
        return (m.unit, m.arc_id, m.episode_number, m.scene_index)

    # -- human-readable labels ----------------------------------------------
    def _episode_title(self, node: StoryNode) -> str:
        """The episode's human title, from the scene's H1 (e.g. '1. 感じていること')."""
        for ln in node.text.splitlines():
            match = _H1_RE.match(ln.strip())
            if match:
                return match.group(1).strip()
        return node.metadata.episode_name

    def human_location(self, node: StoryNode) -> dict:
        """Reader-facing names for a node: unit display name, event name,
        nickname, episode title, and a composed one-line label."""
        m = node.metadata
        row = self._meta_by_arc.get(m.arc_id, {})
        unit_name = UNIT_NAMES.get(m.unit, m.unit)
        if m.content_type == "unit_overview":
            return {
                "unit_name": unit_name, "event_name": f"{unit_name} — story overview",
                "nickname": None, "episode_title": "", "label": f"{unit_name} — story overview",
            }
        event_name = row.get("name") or m.arc_id
        nickname = row.get("nickname")
        ep_title = self._episode_title(node)
        wl = row.get("world_link_label")  # e.g. "World Link 3 Part 1"
        display_event = f"{event_name} ({wl})" if wl else event_name
        label = f"{unit_name} — {display_event}"
        if nickname:
            label += f" [{nickname}]"
        if ep_title:
            label += f" · Ep {ep_title}"
        return {
            "unit_name": unit_name, "event_name": event_name, "nickname": nickname,
            "episode_title": ep_title, "label": label,
        }

    # -- answer --------------------------------------------------------------
    def query(
        self,
        question: str,
        *,
        unit: str | None = None,
        event_id: int | None = None,
        k: int = 5,
    ) -> dict:
        scope = self._scope_index.resolve(question, unit=unit, event_id=event_id)
        unit, arc_id = scope.unit, scope.arc_id

        hits = self.retrieve(question, k=k, unit=unit, arc_id=arc_id)
        if not hits:
            candidates = self._candidate_indices(unit, arc_id) if arc_id else []
            if arc_id and not candidates:
                msg = (
                    f"That event ({arc_id}) is on the timeline but not indexed yet, "
                    "so it isn't chat-answerable until the next ingest."
                )
                return {
                    "answer": msg,
                    "answer_parts": [{"type": "text", "text": msg}],
                    "citations": [],
                    "scope": {"unit": unit, "arc_id": arc_id},
                    "backend": "local",
                }
            if candidates:
                # Scoped to an event (e.g. a nickname) but the query had no lexical
                # overlap (common for a generic EN question over JP text) — show the
                # event's opening scenes rather than nothing.
                candidates.sort(key=lambda idx: self._sort_key(self.nodes[idx]))
                hits = [(self.nodes[idx], 0.0) for idx in candidates[:k]]
            else:
                msg = "No matching story content found for that query."
                return {
                    "answer": msg,
                    "answer_parts": [{"type": "text", "text": msg}],
                    "citations": [],
                    "scope": {"unit": unit, "arc_id": arc_id},
                    "backend": "local",
                }

        top, _ = hits[0]
        q_tokens = set(self._expand_tokens(tokenize(question)))
        # Extractive answer: gather query-overlapping lines from across the top
        # hits (not just #1), ranked by overlap × scene score, so supporting
        # evidence in a lower-ranked scene of the same arc still surfaces. Track
        # which hit each quote came from so the UI can link quote -> excerpt.
        scored_lines: list[tuple[float, str, int]] = []
        seen_lines: set[str] = set()
        for hit_idx, (node, node_score) in enumerate(hits):
            for ln in node.text.splitlines():
                stripped = ln.strip()
                if not stripped or stripped.startswith("#") or stripped in seen_lines:
                    continue
                overlap = len(q_tokens & set(tokenize(stripped)))
                if overlap:
                    scored_lines.append((overlap * node_score, stripped, hit_idx))
                    seen_lines.add(stripped)
        scored_lines.sort(key=lambda s: -s[0])
        quotes = scored_lines[:6]
        if not quotes:  # fall back to the head of the top scene
            head = [
                ln.strip()
                for ln in top.text.splitlines()
                if ln.strip() and not ln.startswith("#")
            ][:3]
            quotes = [(0.0, ln, 0) for ln in head]

        # citations: every hit, ref = 1-based rank, with the full scene as an
        # excerpt (for the click-to-open sidebar) and its best quoted line.
        best_quote: dict[int, str] = {}
        for _, line, hit_idx in quotes:
            best_quote.setdefault(hit_idx, line)
        citations = []
        for i, (h, score) in enumerate(hits):
            loc = self.human_location(h)
            citations.append(
                {
                    "ref": i + 1,
                    "label": loc["label"],  # human one-liner for the UI
                    "unit_name": loc["unit_name"],
                    "event_name": loc["event_name"],
                    "nickname": loc["nickname"],
                    "episode_title": loc["episode_title"],
                    # raw ids retained for programmatic use
                    "unit": h.metadata.unit,
                    "arc_id": h.metadata.arc_id,
                    "episode": h.metadata.episode_name,
                    "scene_index": h.metadata.scene_index,
                    "score": round(score, 4),
                    "plot_weight": self._weight_by_arc.get(h.metadata.arc_id, "unrated"),
                    "quote": best_quote.get(i, ""),
                    "excerpt": h.text,
                }
            )

        label = citations[0]["label"]
        answer_parts: list[dict] = [{"type": "text", "text": f"From {label}:"}]
        for _, line, hit_idx in quotes:
            answer_parts.append({"type": "quote", "ref": hit_idx + 1, "text": line})
        answer = f"From {label}:\n" + "\n".join(q[1] for q in quotes)

        return {
            "answer": answer,
            "answer_parts": answer_parts,
            "citations": citations,
            "scope": {"unit": unit, "arc_id": arc_id},
            "backend": "local",
        }


def _load_glossary(story_root: Path) -> dict | None:
    """Find glossary.json near the story tree or in the cwd (best-effort)."""
    import json

    for candidate in (Path("glossary.json"), story_root.parent / "glossary.json"):
        if candidate.exists():
            try:
                return json.loads(candidate.read_text(encoding="utf-8"))
            except Exception:
                return None
    return None


def build_local_engine(
    story_root: str | Path,
    events_index: list[dict] | None = None,
    glossary: dict | None = None,
) -> LocalQueryEngine:
    """Build the engine over the story tree, restricted to arcs the index marks
    ``indexed`` (the queryable contract: timeline may list more than chat can
    answer). If no index is given, all parsed nodes are queryable. A glossary
    (JP<->EN) enables cross-lingual queries; auto-loaded from glossary.json if
    not passed."""
    story_root = Path(story_root)
    nodes = load_story_nodes(story_root)
    if events_index:
        indexed_arcs = {r["arc_slug"] for r in events_index if r.get("indexed") and r.get("arc_slug")}
        if indexed_arcs:
            # The indexed-only contract governs EVENT content (timeline may lead
            # ingest). Non-event content (unit/card/area) is queryable once on disk.
            nodes = [
                n
                for n in nodes
                if n.metadata.content_type != "event" or n.metadata.arc_id in indexed_arcs
            ]
    if glossary is None:
        glossary = _load_glossary(story_root)
    # Tier-1 unit overviews (synopsis-level) are always available, even for
    # events whose full text isn't indexed yet.
    if events_index:
        from .summaries import build_unit_overviews

        nodes = nodes + build_unit_overviews(events_index)
    return LocalQueryEngine(nodes, events_index, glossary)
