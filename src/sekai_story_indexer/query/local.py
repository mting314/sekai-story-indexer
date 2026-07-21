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
from ..source.constants import CHARACTER_ID_TO_JP, CHARACTER_ID_TO_UNIT, UNIT_NAMES
from ..source.relevance import weight_factor
from .context import arc_context_line
from .scoping import Scope, ScopeIndex

_WORD_RE = re.compile(r"[a-z0-9]+")
_CJK_RE = re.compile(r"[぀-ヿ㐀-鿿]")
_H1_RE = re.compile(r"^#\s*(.+)$")

# When a content query is scoped to a single event, feed the whole event to the
# answer in reading order (bounded by this char budget) instead of a top-k cut,
# so endings/climaxes aren't dropped. If an event exceeds the budget, keep its
# head AND tail (drop the middle) so the opening and finale both survive. ~4
# chars/token, kept under generate._context's own cap.
_SCOPED_CTX_CHARS = 80_000

# Unit references in questions (substring, lowercased) -> unit slug.
_UNIT_KEYWORDS = {
    "leo/need": "leo_need", "leoneed": "leo_need", "leo need": "leo_need",
    "more more jump": "more_more_jump", "moremorejump": "more_more_jump",
    "mmj": "more_more_jump", "momojan": "more_more_jump",
    "vivid bad squad": "vivid_bad_squad", "vbs": "vivid_bad_squad", "vivid": "vivid_bad_squad",
    "wonderlands": "wonderlands_showtime", "wxs": "wonderlands_showtime", "wonder show": "wonderlands_showtime",
    "nightcord": "nightcord", "25-ji": "nightcord", "niigo": "nightcord", "n25": "nightcord",
    "virtual singer": "virtual_singer", "vocaloid": "virtual_singer",
}


def tokenize(text: str) -> list[str]:
    """ASCII words + CJK unigrams AND bigrams — a language-agnostic lexical key set
    that needs no tokenizer dependency (works for JP and EN). Unigrams let a short,
    standalone kanji word (弟, 兄) be found even when it fuses with a following
    particle into a bigram (弟も); bigrams keep multi-char phrase precision."""
    text = text.lower()
    tokens = _WORD_RE.findall(text)
    cjk = _CJK_RE.findall(text)
    tokens += cjk  # unigrams (single CJK chars)
    if len(cjk) > 1:
        tokens += ["".join(pair) for pair in zip(cjk, cjk[1:])]  # adjacent bigrams
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
        event_summaries: dict[str, str] | None = None,
    ):
        self.nodes = nodes
        # pre-computed event summaries {arc_id: text} (from event_summaries.json) —
        # used to answer 'summarize X' cheaply instead of re-reading raw scenes.
        self._event_summaries: dict[str, str] = event_summaries or {}
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
        # character list (jp full name, en) + id->(jp,en) for count targeting
        self._characters: list[tuple[str, str]] = list((glossary.get("characters") or {}).items())
        _jp_to_en = dict(self._characters)
        self._char_by_id: dict[int, tuple[str, str]] = {
            cid: (jp, _jp_to_en.get(jp, jp)) for cid, jp in CHARACTER_ID_TO_JP.items()
        }
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
        # General EN→JP vocabulary (kinship, occupations, …) is bridged at query
        # time by translating the whole question to Japanese (query/translate.py),
        # so no hand-maintained per-category dictionary lives here.
        # Contextual retrieval (deterministic, free): index each scene as its
        # situating context (nickname / "character X's Nth focus event" / unit /
        # song) + the raw text, so those queries match by meaning. Only the token
        # source is augmented — node.text (shown/quoted) is untouched. Requires the
        # meta/char maps above, hence built here.
        self._tokens: list[list[str]] = [tokenize(self._index_text(n)) for n in nodes]
        self._tf: list[Counter] = [Counter(t) for t in self._tokens]
        df: Counter = Counter()
        for toks in self._tokens:
            df.update(set(toks))
        n_docs = max(1, len(nodes))
        self._idf: dict[str, float] = {
            term: math.log(1 + n_docs / (1 + count)) for term, count in df.items()
        }

    def _context_line(self, arc_id: str | None) -> str:
        """The deterministic contextual-retrieval prefix for an arc (or "")."""
        meta = self._meta_by_arc.get(arc_id or "")
        if not meta:
            return ""
        fcid = meta.get("focus_character_id")
        en = self._char_by_id.get(fcid, (None, None))[1] if fcid else None
        return arc_context_line(meta, focus_name_en=en)

    def _index_text(self, node: StoryNode) -> str:
        """Text used for TF-IDF indexing: situating context + the scene text. The
        context is index-only; node.text (shown/quoted) is never modified."""
        ctx = self._context_line(node.metadata.arc_id)
        return f"{ctx}\n{node.text}" if ctx else node.text

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
    def _scoped(
        self,
        question: str,
        *,
        unit: str | None = None,
        event_id: int | None = None,
        arc_ids: tuple[str, ...] = (),
    ) -> Scope:
        """Resolve scope from the question, but let caller-supplied ``arc_ids``
        (explicit references or carried conversation focus) take precedence — so a
        follow-up stays on the remembered event without an event_id round-trip."""
        scope = self._scope_index.resolve(question, unit=unit, event_id=event_id)
        if arc_ids:
            return Scope(
                unit=scope.unit or unit,
                arc_id=arc_ids[0] if len(arc_ids) == 1 else None,
                arc_ids=tuple(arc_ids) if len(arc_ids) > 1 else (),
                nickname=scope.nickname,
                label=scope.label,
            )
        return scope

    def _candidate_indices(
        self,
        unit: str | None,
        arc_id: str | None,
        arc_ids: tuple[str, ...] = (),
    ) -> list[int]:
        arc_set = set(arc_ids)
        out = []
        for i, node in enumerate(self.nodes):
            m = node.metadata
            if unit and m.unit != unit:
                continue
            if arc_id and m.arc_id != arc_id:
                continue
            if arc_set and m.arc_id not in arc_set:
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
        arc_ids: tuple[str, ...] = (),
        aux_query: str = "",
    ) -> list[tuple[StoryNode, float]]:
        q_tokens = [t for t in self._expand_tokens(self._query_tokens(question, aux_query)) if t in self._idf]
        candidates = self._candidate_indices(unit, arc_id, arc_ids)
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

    def _budget_cover(self, idxs: list[int], budget_chars: int) -> list[int]:
        """Select scenes within a char budget, always keeping the HEAD and TAIL so a
        scoped event's opening AND ending (climax) both survive; drop from the
        middle. ``idxs`` must already be in reading order. Returns reading order."""
        total = sum(len(self.nodes[i].text) for i in idxs)
        if total <= budget_chars or len(idxs) <= 2:
            return idxs
        picked: list[tuple[int, int]] = []  # (position, node index)
        lo, hi, used, take_low = 0, len(idxs) - 1, 0, True
        while lo <= hi:
            pos = lo if take_low else hi
            cost = len(self.nodes[idxs[pos]].text)
            if picked and used + cost > budget_chars:
                break
            picked.append((pos, idxs[pos]))
            used += cost
            if take_low:
                lo += 1
            else:
                hi -= 1
            take_low = not take_low
        picked.sort(key=lambda t: t[0])  # restore reading order
        return [i for _, i in picked]

    def _query_tokens(self, question: str, aux_query: str = "") -> list[str]:
        """Tokens used for retrieval scoring: the question plus an optional
        translated form (query/translate.py), so an EN query also matches the JP
        corpus. Scoping/intent stay on the original question, not this."""
        toks = tokenize(question)
        if aux_query:
            toks = toks + tokenize(aux_query)
        return toks

    def _scoped_event_hits(
        self, question: str, unit: str | None, arc_id: str, aux_query: str = ""
    ) -> list[tuple[StoryNode, float]]:
        """Whole scoped event in reading order (budget-bounded), each scored by
        query overlap so the extractive quote picker still highlights relevant
        lines. Reading order (not score) so the answer sees the arc start→finale."""
        idxs = self._candidate_indices(unit, arc_id)
        idxs.sort(key=lambda i: self._sort_key(self.nodes[i]))
        idxs = self._budget_cover(idxs, _SCOPED_CTX_CHARS)
        q_tokens = [t for t in self._expand_tokens(self._query_tokens(question, aux_query)) if t in self._idf]
        hits: list[tuple[StoryNode, float]] = []
        for i in idxs:
            tf = self._tf[i]
            score = sum(tf.get(t, 0) * self._idf[t] for t in q_tokens)
            score *= weight_factor(self._weight_by_arc.get(self.nodes[i].metadata.arc_id))
            hits.append((self.nodes[i], score))
        return hits

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
        arc_ids: tuple[str, ...] = (),
        aux_query: str = "",
    ) -> dict:
        scope = self._scoped(question, unit=unit, event_id=event_id, arc_ids=arc_ids)
        unit, arc_id, arc_ids = scope.unit, scope.arc_id, scope.arc_ids

        # Scoped to a single event: answer over the WHOLE event in reading order
        # (bounded by a char budget, keeping head+tail so the ending/climax always
        # survives), not a top-k cut. The query is often cross-lingual (EN over JP
        # scenes), so nothing lexically favors the finale and a top-k would return
        # the first k episodes — hiding the climax from the answer.
        if arc_id and not arc_ids:
            hits = self._scoped_event_hits(question, unit, arc_id, aux_query=aux_query)
        else:
            hits = self.retrieve(
                question, k=k, unit=unit, arc_id=arc_id, arc_ids=arc_ids, aux_query=aux_query
            )
        if not hits:
            candidates = (
                self._candidate_indices(unit, arc_id, arc_ids) if (arc_id or arc_ids) else []
            )
            if arc_id and not candidates:
                msg = (
                    f"That event ({arc_id}) is on the timeline but not indexed yet, "
                    "so it isn't chat-answerable until the next ingest."
                )
                return {
                    "answer": msg,
                    "answer_parts": [{"type": "text", "text": msg}],
                    "citations": [],
                    "scope": scope.as_dict(),
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
                    "scope": scope.as_dict(),
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
        citations = [
            self._citation(h, i + 1, score=score, quote=best_quote.get(i, ""))
            for i, (h, score) in enumerate(hits)
        ]

        label = citations[0]["label"]
        answer_parts: list[dict] = [{"type": "text", "text": f"From {label}:"}]
        for _, line, hit_idx in quotes:
            answer_parts.append({"type": "quote", "ref": hit_idx + 1, "text": line})
        answer = f"From {label}:\n" + "\n".join(q[1] for q in quotes)

        return {
            "answer": answer,
            "answer_parts": answer_parts,
            "citations": citations,
            "scope": scope.as_dict(),
            "backend": "local",
        }

    def _citation(self, node: StoryNode, ref: int, *, score: float = 0.0, quote: str = "") -> dict:
        loc = self.human_location(node)
        m = node.metadata
        return {
            "ref": ref,
            "label": loc["label"],
            "unit_name": loc["unit_name"],
            "event_name": loc["event_name"],
            "nickname": loc["nickname"],
            "episode_title": loc["episode_title"],
            "unit": m.unit,
            "arc_id": m.arc_id,
            "episode": m.episode_name,
            "scene_index": m.scene_index,
            "score": round(score, 4),
            "plot_weight": self._weight_by_arc.get(m.arc_id, "unrated"),
            "quote": quote,
            "excerpt": node.text,
        }

    # -- intent-routed paths -------------------------------------------------
    def summarize(
        self, question: str, *, unit: str | None = None, event_id: int | None = None,
        max_scenes: int = 16, arc_ids: tuple[str, ...] = (),
    ) -> dict:
        """Deterministic 'summarize <entity>' path: resolve the entity and pull
        its WHOLE scope in reading order (no lexical top-k), so the summary is
        complete. Falls back to general retrieval if no entity is resolved."""
        scope = self._scoped(question, unit=unit, event_id=event_id, arc_ids=arc_ids)
        idxs = (
            self._candidate_indices(scope.unit, scope.arc_id, scope.arc_ids)
            if (scope.unit or scope.arc_id or scope.arc_ids)
            else []
        )
        if not idxs:
            # No entity resolved for a 'summarize X' request -> we fall back to
            # general lexical retrieval and answer from the top-ranked arc. Flag it
            # so callers/logs can see this guess (the "rise as one" failure mode).
            fallback = self.query(question, unit=unit, event_id=event_id, arc_ids=arc_ids)
            fallback["summarize_fell_back"] = True
            return fallback
        # chronological across parts (arc_slugs are zero-padded by date)
        idxs.sort(key=lambda i: (self.nodes[i].metadata.arc_id, *self._sort_key(self.nodes[i])[2:]))
        if scope.arc_ids:  # multi-part: sample evenly so every part is represented
            per_arc = max(1, max_scenes // len(scope.arc_ids))
            picked, seen = [], {}
            for i in idxs:
                a = self.nodes[i].metadata.arc_id
                if seen.get(a, 0) < per_arc:
                    picked.append(i)
                    seen[a] = seen.get(a, 0) + 1
            idxs = picked
        idxs = idxs[:max_scenes]
        citations = [self._citation(self.nodes[i], r + 1) for r, i in enumerate(idxs)]
        label = scope.label or citations[0]["label"]

        # Prefer PRE-COMPUTED event summaries (the point of ingest-time summaries):
        # return them directly — no re-reading raw scenes, no per-query LLM cost.
        scoped_arcs = list(scope.arc_ids) or ([scope.arc_id] if scope.arc_id else [])
        pre = [(a, self._event_summaries[a]) for a in scoped_arcs if a in self._event_summaries]
        if pre:
            if len(pre) == 1:
                body = pre[0][1]
            else:  # multi-part (World Link): stitch the per-part summaries
                body = "\n\n".join(
                    f"**{self._meta_by_arc.get(a, {}).get('name', a)}**\n{t}" for a, t in pre
                )
            return {
                "answer": body,
                "answer_parts": [{"type": "text", "text": body}],
                "citations": citations,
                "scope": scope.as_dict(),
                "backend": "local",
                "intent": "summarize",
                "pre_summarized": True,  # webapp: don't re-generate over raw scenes
            }

        return {
            "answer": f"Summary of {label}",
            "answer_parts": [{"type": "text", "text": f"Summary of {label}"}],
            "citations": citations,
            "scope": scope.as_dict(),
            "backend": "local",
            "intent": "summarize",
        }

    def _named_chars(self, question: str) -> list[tuple[str, str]]:
        """All characters explicitly named in the question (JP fragment or EN token)."""
        q_tokens = set(tokenize(question))
        jp_runs = _CJK_RE.findall(question)
        jp_bigrams = {"".join(pair) for pair in zip(jp_runs, jp_runs[1:])}
        out = []
        for jp, en in self._characters:
            if (jp in question or any(bg in jp for bg in jp_bigrams)
                    or any(len(t) >= 3 and t in q_tokens for t in tokenize(en))):
                out.append((jp, en))
        return out

    def _units_in_question(self, question: str) -> set[str]:
        ql = question.lower()
        return {slug for kw, slug in _UNIT_KEYWORDS.items() if kw in ql}

    def _resolve_count_targets(self, question, scope) -> list[tuple[str, str]]:
        """Characters to count: an 'each/all <unit>' phrase expands to that unit's
        members; otherwise any explicitly named character(s)."""
        ql = question.lower()
        wants_all = any(w in ql for w in ("each ", "every ", "all ", "per "))
        units = self._units_in_question(question) or ({scope.unit} if scope.unit else set())
        if wants_all and units:
            return [
                self._char_by_id[cid]
                for cid, u in CHARACTER_ID_TO_UNIT.items()
                if u in units and cid in self._char_by_id
            ]
        return self._named_chars(question)

    def count_dialogue(
        self, question: str, *, unit: str | None = None, event_id: int | None = None
    ) -> dict:
        """Exact dialogue-line count for one or more characters in scope —
        deterministic, never an LLM estimate."""
        scope = self._scope_index.resolve(question, unit=unit, event_id=event_id)
        targets = self._resolve_count_targets(question, scope)
        if not targets:
            msg = "Tell me which character (or unit) to count lines for."
            return {"answer": msg, "answer_parts": [{"type": "text", "text": msg}],
                    "citations": [], "scope": scope.as_dict(), "backend": "local",
                    "intent": "count"}
        idxs = self._candidate_indices(scope.unit, scope.arc_id, scope.arc_ids)
        counts = []
        for jp, en in targets:
            en_tokens = {t for t in en.lower().split() if len(t) >= 2}
            n = sum(
                1
                for i in idxs
                for turn in self.nodes[i].dialogue_turns
                if _speaker_is(turn.speaker, jp, en_tokens)
            )
            counts.append((en, n))

        where = ""
        if scope.label:
            where = f" in {scope.label}"
        elif scope.arc_id:
            where = f" in {self._meta_by_arc.get(scope.arc_id, {}).get('name', scope.arc_id)}"
        elif scope.unit:
            where = f" in {UNIT_NAMES.get(scope.unit, scope.unit)}"

        if len(counts) == 1:
            en, n = counts[0]
            answer = f"{en} has {n} dialogue line{'s' if n != 1 else ''}{where}."
        else:
            counts.sort(key=lambda c: -c[1])
            answer = f"Dialogue lines{where}:\n" + "\n".join(f"- {en}: {n}" for en, n in counts)
        return {
            "answer": answer,
            "answer_parts": [{"type": "text", "text": answer}],
            "citations": [], "counts": dict(counts),
            "count": counts[0][1] if len(counts) == 1 else None,  # single-target convenience
            "scope": scope.as_dict(), "backend": "local", "intent": "count",
        }


def _speaker_is(speaker: str, jp_full: str, en_tokens: set[str]) -> bool:
    """Match a scene speaker (JP given name like 'こはね', or an EN name in the
    sample corpus) to a target character (jp full name + EN name tokens)."""
    if not speaker:
        return False
    if speaker in jp_full:  # JP given-name is a substring of the full name
        return True
    return speaker.lower() in en_tokens


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
    # Pre-computed event summaries (arc_id -> text), if built by `indexer ingest`.
    event_summaries = _load_event_summaries(story_root)
    return LocalQueryEngine(nodes, events_index, glossary, event_summaries)


def _load_event_summaries(story_root: Path) -> dict[str, str]:
    """Pre-computed event summaries (arc_id -> text) for the summarize shortcut —
    ONLY the current hierarchical store (``summaries_cache.json`` ``EVENT|<arc>``).

    The frozen legacy ``event_summaries.json`` is no longer served: its summaries
    were low-quality (truncated / thinking-leaks) and stale. Events without a
    hierarchical summary intentionally have none here, so the generative backends
    answer them by retrieving the scenes and synthesizing on the fly ("pull from
    embedding"), rather than serving pre-baked prose.
    """
    import json
    import os
    import re

    override = os.environ.get("SEKAI_SUMMARIES_CACHE")
    candidates = (
        [Path(override)] if override
        else [Path("summaries_cache.json"), story_root.parent / "summaries_cache.json"]
    )
    cache: dict = {}
    for candidate in candidates:
        if candidate.exists():
            try:
                cache = json.loads(candidate.read_text(encoding="utf-8"))
            except Exception:
                cache = {}
            break

    def _text(v) -> str:  # normalize + strip inline {char_id=N} tags
        s = v if isinstance(v, str) else (v or {}).get("summary", "")
        return re.sub(r"\{char_id=\d+\}", "", s)

    out: dict[str, str] = {}
    for key, v in cache.items():
        if key.startswith("EVENT|") and _text(v).strip():
            out[key.split("|", 1)[1]] = _text(v)
    return out
