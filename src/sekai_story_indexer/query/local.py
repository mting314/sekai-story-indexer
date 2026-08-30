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
from .scoring import (
    UNIT_KEYWORDS as _UNIT_KEYWORDS,
)
from .scoring import (
    concept_score,
    name_surfaces,
    named_characters,
    scoring_groups,
    tokenize,
    topic_terms,
)
from .turns import TIER_REPLY, find_turn_hits, window_lines

_WORD_RE = re.compile(r"[a-z0-9]+")
_CJK_RE = re.compile(r"[぀-ヿ㐀-鿿]")
_H1_RE = re.compile(r"^#\s*(.+)$")

# When a content query is scoped to a single event, feed the whole event to the
# answer in reading order (bounded by this char budget) instead of a top-k cut,
# so endings/climaxes aren't dropped. If an event exceeds the budget, keep its
# head AND tail (drop the middle) so the opening and finale both survive. ~4
# chars/token, kept under generate._context's own cap.
_SCOPED_CTX_CHARS = 80_000

# Positional intent: bias scoped scene selection/ranking toward one END of the
# event (deterministic, no LLM) — "how does it end / the climax" -> late scenes,
# "how does it begin / the opening" -> early scenes. Conservative patterns so a
# stray "first"/"end" doesn't trigger it.
_LATE_INTENT_RE = re.compile(
    r"\b(climax|finale|ending|end of|conclusion|denouement|aftermath|"
    r"final (scene|episode|moment|part|arc)|at the end|"
    r"how (does )?(it|the (event|story|arc)) ends?|"
    r"what happens (at the end|in the end))\b",
    re.IGNORECASE,
)
_EARLY_INTENT_RE = re.compile(
    r"\b(beginning|opening|premise|prologue|at the (start|beginning|outset)|"
    r"first (scene|episode|part)|"
    r"how (does )?(it|the (event|story|arc)) (starts?|begins?)|"
    r"what happens (at the start|at the beginning))\b",
    re.IGNORECASE,
)
_POS_BOOST = 1.0  # max extra weight applied to scenes at the asked-about end



# Speech verbs. Turn-level attribution only takes over when the question asks what
# a character *said*, and only when the character is the SUBJECT of that verb —
# "when does Honami mention her brother" (Honami speaks) but not "where does she
# tell Kohane to meet" (Kohane is the recipient). Subject position is approximated
# by the name appearing before the verb, which is what separates the two.
_SPEECH_VERB_RE = re.compile(
    r"\b(mention(s|ed)?|say(s)?|said|talk(s|ed)?|speak(s)?|spoke|"
    r"tell(s)?|told|call(s|ed)?|describe(s|d)?|refer(s|red)?|"
    r"bring(s)? up|brought up)\b",
    re.IGNORECASE,
)





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
        official_en: dict[str, str] | None = None,
    ):
        self.nodes = nodes
        # JP line -> official EN line. Folded into the *index* text (so an English
        # question matches a Japanese transcript with no translation round-trip) and
        # used by turn retrieval to read/quote utterances in English. node.text is
        # never modified — JP stays the quoted source of truth.
        self._en: dict[str, str] = official_en or {}
        # pre-computed event summaries {arc_id: text} (hierarchical summaries_cache) —
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
        # Main + named side characters bridge both ways; a rare EN surname token
        # (>=3 chars) alone maps to the JP name so "Shindo" finds 真堂 scenes.
        for section in ("characters", "side_characters"):
            for jp, en in (glossary.get(section) or {}).items():
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
        self._df: Counter = df
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

    def _english_text(self, node: StoryNode) -> str:
        """The node's official-EN rendering, line-for-line, or "" when unlocalized."""
        if not self._en:
            return ""
        out = [
            self._en[s]
            for s in (ln.strip() for ln in node.text.splitlines())
            if s and s != "---" and not s.startswith("#") and s in self._en
        ]
        return "\n".join(out)

    def _index_text(self, node: StoryNode) -> str:
        """Text used for TF-IDF indexing: situating context + the scene text + its
        official-EN rendering. All three are index-only; node.text (shown/quoted)
        is never modified. Indexing EN alongside JP is what puts English content
        words in the vocabulary at all — without it every non-name term in an
        English question is dropped before scoring, leaving the query to rank on
        the character name alone."""
        parts = [self._context_line(node.metadata.arc_id), node.text, self._english_text(node)]
        return "\n".join(p for p in parts if p)

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
        *,
        include_children: bool = True,
    ) -> list[int]:
        scoped = set(arc_ids) | ({arc_id} if arc_id else set())
        out = []
        for i, node in enumerate(self.nodes):
            m = node.metadata
            if scoped:
                # Own-arc match (respects the unit filter) OR a nested card/area
                # child whose parent event is in scope. Children ride in via their
                # parent regardless of their OWN unit — an event's area talks are
                # often 'mixed' and cards sit under the character's unit — so a
                # scope on event X surfaces its card side-stories + area talks too.
                # `include_children=False` keeps the scope to the event's OWN scenes
                # (e.g. count_dialogue's exact per-event-story count).
                own = m.arc_id in scoped and (not unit or m.unit == unit)
                child = (
                    include_children and bool(m.parent_arc_id) and m.parent_arc_id in scoped
                )
                if own or child:
                    out.append(i)
            else:
                if unit and m.unit != unit:
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
        candidates = self._candidate_indices(unit, arc_id, arc_ids)
        name_group, topics = self._scoring_groups(question, aux_query)
        if not name_group and not topics:  # nothing nameable or topical
            return self._retrieve_additive(question, candidates, k=k, aux_query=aux_query)

        scored: list[tuple[float, int]] = []
        for i in candidates:
            score = self._concept_score(i, name_group, topics)
            if score > 0:
                # boost plot-heavy scenes, de-prioritize filler (never drop it)
                score *= weight_factor(self._weight_by_arc.get(self.nodes[i].metadata.arc_id))
                scored.append((score, i))
        if not scored:
            # Every scene missed every topic. Questions paraphrase ("promise",
            # "gender") and the tokenizer does not stem, so this is a phrasing gap,
            # not evidence of absence — fall back rather than answer nothing.
            return self._retrieve_additive(question, candidates, k=k, aux_query=aux_query)
        # deterministic tie-break: score desc, then stable source order
        scored.sort(key=lambda s: (-s[0], self._sort_key(self.nodes[s[1]])))
        return [(self.nodes[i], score) for score, i in scored[:k]]

    def _retrieve_additive(
        self, question: str, candidates: list[int], *, k: int, aux_query: str = ""
    ) -> list[tuple[StoryNode, float]]:
        """Plain TF-IDF over every query token — the fallback for questions with no
        name and no topic to group (nicknames, bare unit words, JP-only phrasings)."""
        q_tokens = [
            t for t in self._expand_tokens(self._query_tokens(question, aux_query)) if t in self._idf
        ]
        scored: list[tuple[float, int]] = []
        for i in candidates:
            tf = self._tf[i]
            score = sum(tf.get(t, 0) * self._idf[t] for t in q_tokens)
            if score > 0:
                score *= weight_factor(self._weight_by_arc.get(self.nodes[i].metadata.arc_id))
                scored.append((score, i))
        scored.sort(key=lambda s: (-s[0], self._sort_key(self.nodes[s[1]])))
        return [(self.nodes[i], score) for score, i in scored[:k]]

    def _scoring_groups(
        self, question: str, aux_query: str = ""
    ) -> tuple[list[str], list[list[str]]]:
        """``(name surfaces, topic concepts)`` — see :mod:`query.scoring`. Shared
        with the derived backend so the public deploy ranks identically."""
        return scoring_groups(
            question, self._characters, self._idf, aux_query=aux_query, floor=False
        )

    def _concept_score(
        self, index: int, name_group: list[str], topics: list[list[str]]
    ) -> float:
        """Concept-wise relevance for one scene — see :mod:`query.scoring`."""
        return concept_score(self._tf[index], self._idf, name_group, topics)

    # -- turn-level retrieval -------------------------------------------------
    def _attributed_speaker(self, question: str) -> list[tuple[str, str]]:
        """Characters the question asks about *as speakers* — named, and sitting in
        subject position before a speech verb. [] when this isn't an attribution
        question, which leaves scene retrieval in charge."""
        verb = _SPEECH_VERB_RE.search(question)
        if not verb:
            return []
        before = question[: verb.start()].lower()
        out = []
        for jp, en in self._named_chars(question):
            surfaces = [jp, *(t for t in en.lower().split() if len(t) >= 3)]
            if any(s.lower() in before for s in surfaces):
                out.append((jp, en))
        return out

    def _name_tokens(self, question: str) -> set[str]:
        """Every lexical surface of the characters the question names, as one
        concept rather than N independent terms."""
        return set(name_surfaces(question, self._characters, self._idf))

    def _topic_terms(self, question: str) -> list[str]:
        """Question terms that carry topic, sharpest first (no relevance floor)."""
        return topic_terms(question, self._characters, self._idf)

    def _content_concepts(self, question: str, aux_query: str = "") -> list[list[str]]:
        """What the question is *about*, as interchangeable surface forms per topic,
        pruned to the terms sharp enough to be its subject. Used by turn retrieval,
        which needs a tight topic set because it matches against single utterances."""
        return scoring_groups(
            question, self._characters, self._idf, aux_query=aux_query, floor=True
        )[1]

    def _turn_hits(
        self,
        question: str,
        *,
        unit: str | None,
        arc_id: str | None,
        arc_ids: tuple[str, ...],
        aux_query: str = "",
    ) -> list:
        """Turn windows where the character the question asks about is themselves
        involved in the topic. [] when this isn't an attribution question."""
        named = self._attributed_speaker(question)
        concepts = self._content_concepts(question, aux_query)
        if not named or not concepts:
            return []

        # Prefilter on the most discriminative topic so the turn scan stays cheap;
        # requiring *every* topic is too strict for multi-word questions.
        best = max(
            (c for c in concepts),
            key=lambda c: max((self._idf.get(t, 0.0) for t in c), default=0.0),
        )
        pool = [
            i
            for i in self._candidate_indices(unit, arc_id, arc_ids)
            if any(self._tf[i].get(t) for t in best)
        ]
        if not pool:
            return []

        targets = [(jp, {t for t in en.lower().split() if len(t) >= 2}) for jp, en in named]

        def is_speaker(speaker: str) -> bool:
            return any(_speaker_is(speaker, jp, en_toks) for jp, en_toks in targets)

        hits = find_turn_hits(
            self.nodes,
            pool,
            content_concepts=concepts,
            is_speaker=is_speaker,
            en_map=self._en,
        )
        # Mere co-presence (TIER_PRESENT) is overwhelmingly noise — on the full
        # corpus it outnumbers real attributions ~7:1, and it is exactly the
        # "someone else said it while they were in the room" failure this path
        # exists to remove. Keep only utterances we can actually attribute.
        return [h for h in hits if h.tier >= TIER_REPLY]

    def _budget_cover(
        self, idxs: list[int], budget_chars: int, bias: str | None = None
    ) -> list[int]:
        """Select scenes within a char budget. Default keeps the HEAD and TAIL so a
        scoped event's opening AND ending (climax) both survive; drop from the
        middle. ``bias='late'/'early'`` instead keeps scenes from that END (for
        climax/ending vs beginning questions). ``idxs`` must be in reading order;
        returns reading order."""
        total = sum(len(self.nodes[i].text) for i in idxs)
        if total <= budget_chars or len(idxs) <= 2:
            return idxs
        if bias in ("early", "late"):
            seq = idxs if bias == "early" else list(reversed(idxs))
            picked_biased: list[int] = []
            used_b = 0
            for i in seq:
                cost = len(self.nodes[i].text)
                if picked_biased and used_b + cost > budget_chars:
                    break
                picked_biased.append(i)
                used_b += cost
            return picked_biased if bias == "early" else picked_biased[::-1]
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
        # Positional intent (climax/ending vs beginning) biases WHICH scenes survive
        # the budget AND re-ranks the extractive quotes toward that end — no LLM.
        bias = _positional_intent(question)
        idxs = self._budget_cover(idxs, _SCOPED_CTX_CHARS, bias=bias)
        q_tokens = [t for t in self._expand_tokens(self._query_tokens(question, aux_query)) if t in self._idf]
        n = len(idxs)
        hits: list[tuple[StoryNode, float]] = []
        for rank, i in enumerate(idxs):
            tf = self._tf[i]
            score = sum(tf.get(t, 0) * self._idf[t] for t in q_tokens)
            score *= weight_factor(self._weight_by_arc.get(self.nodes[i].metadata.arc_id))
            if bias and n > 1:  # nudge quote ranking toward the asked-about end
                frac = rank / (n - 1)
                score *= 1 + _POS_BOOST * (frac if bias == "late" else 1 - frac)
            hits.append((self.nodes[i], score))
        return hits

    @staticmethod
    def _sort_key(node: StoryNode) -> tuple:
        m = node.metadata
        return (m.unit, m.arc_id, m.episode_number, m.scene_index)

    # -- human-readable labels ----------------------------------------------
    def _episode_title(self, node: StoryNode) -> str:
        """The episode's human title. Prefers the official English title (when the
        event row carries an ``episode_titles_en`` overlay, keyed by episode number),
        else the scene's own H1 (JP, e.g. '1. 感じていること')."""
        m = node.metadata
        en_titles = self._meta_by_arc.get(m.arc_id, {}).get("episode_titles_en") or {}
        # in-memory overlay uses int keys; a JSON-loaded one would use str -> try both
        en = en_titles.get(m.episode_number) or en_titles.get(str(m.episode_number))
        if en:
            return f"{m.episode_number}. {en}"
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
        turn_hits: list = []
        if arc_id and not arc_ids:
            hits = self._scoped_event_hits(question, unit, arc_id, aux_query=aux_query)
        else:
            # A question that names a character and asks about a topic ("when does
            # Honami mention her brother") is answered by an *utterance*, not by an
            # episode. Attribute at turn level first; fall back to scene retrieval
            # when the speaker is never near the topic.
            turn_hits = self._turn_hits(
                question, unit=unit, arc_id=arc_id, arc_ids=arc_ids, aux_query=aux_query
            )
            if turn_hits:
                best_by_node: dict[int, object] = {}
                for th in turn_hits:  # already strongest-first
                    best_by_node.setdefault(th.node_index, th)
                turn_hits = list(best_by_node.values())[:k]
                hits = [(self.nodes[th.node_index], float(th.tier)) for th in turn_hits]
            elif self._attributed_speaker(question) and self._content_concepts(
                question, aux_query
            ):
                # An attribution question we could not attribute. Falling through to
                # scene retrieval here is precisely how another character's line gets
                # quoted back as if they'd said it, so decline instead.
                who = ", ".join(en for _, en in self._attributed_speaker(question))
                msg = (
                    f"No line where {who} says that — nothing in the indexed corpus "
                    "attributes it to them."
                )
                return {
                    "answer": msg,
                    "answer_parts": [{"type": "text", "text": msg}],
                    "citations": [],
                    "scope": scope.as_dict(),
                    "backend": "local",
                }
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
        if turn_hits:
            # Turn-attributed: quote the named speaker's own line, and hand back the
            # surrounding exchange so a reply that refers back ("he", 彼) still reads
            # as evidence and the generator can resolve it.
            quotes = [(float(th.tier), th.quote, i) for i, th in enumerate(turn_hits)]
            citations = [
                self._citation(
                    node,
                    i + 1,
                    score=score,
                    quote=turn_hits[i].quote,
                    window=window_lines(node, turn_hits[i], self._en),
                )
                for i, (node, score) in enumerate(hits)
            ]
            label = citations[0]["label"]
            answer_parts: list[dict] = [{"type": "text", "text": f"From {label}:"}]
            for _, line, hit_idx in quotes:
                answer_parts.append({"type": "quote", "ref": hit_idx + 1, "text": line})
            return {
                "answer": f"From {label}:\n" + "\n".join(q[1] for q in quotes),
                "answer_parts": answer_parts,
                "citations": citations,
                "scope": scope.as_dict(),
                "backend": "local",
            }

        q_tokens = set(self._expand_tokens(tokenize(question)))
        # Extractive answer: gather query-overlapping lines from across the top
        # hits (not just #1), ranked by overlap × scene score, so supporting
        # evidence in a lower-ranked scene of the same arc still surfaces. Track
        # which hit each quote came from so the UI can link quote -> excerpt.
        #
        # Overlap is measured against the JP line *and* its official-EN rendering:
        # an English question matches the EN side, so scanning JP alone scores every
        # line at zero and leaves the answer quoteless. Weight by IDF rather than by
        # a raw term count, so a line whose only overlap is the speaker's name — the
        #「…………」 filler lines that a name-dense scene is full of — cannot outrank a
        # line that actually carries the topic.
        scored_lines: list[tuple[float, str, int]] = []
        seen_lines: set[str] = set()
        for hit_idx, (node, node_score) in enumerate(hits):
            for ln in node.text.splitlines():
                stripped = ln.strip()
                if not stripped or stripped.startswith("#") or stripped in seen_lines:
                    continue
                english = self._en.get(stripped, "")
                matched = q_tokens & set(tokenize(f"{stripped} {english}"))
                if matched:
                    overlap = sum(self._idf.get(t, 0.0) for t in matched)
                    scored_lines.append((overlap * node_score, english or stripped, hit_idx))
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

    def _citation(
        self,
        node: StoryNode,
        ref: int,
        *,
        score: float = 0.0,
        quote: str = "",
        window: list[str] | None = None,
    ) -> dict:
        loc = self.human_location(node)
        m = node.metadata
        return {
            "window": window or [],
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

        # No pre-computed summary: build an extractive "skim" — the opening line of
        # each cited scene — so /summarize still returns readable content when the
        # LLM can't refine it (keyless / quota). The webapp localizes these lines to
        # official-EN; when a key is available the caller generates a real summary
        # over the same scenes instead (this answer is then replaced).
        parts: list[dict] = [{"type": "text", "text": f"Summary of {label} (excerpts):"}]
        for r, i in enumerate(idxs):
            line = next(
                (ln.strip() for ln in self.nodes[i].text.splitlines()
                 if ln.strip() and not ln.startswith("#")),
                "",
            )
            if line:
                parts.append({"type": "quote", "ref": r + 1, "text": line})
                citations[r]["quote"] = line
        answer = "\n".join([parts[0]["text"], *(p["text"] for p in parts[1:])])
        return {
            "answer": answer,
            "answer_parts": parts,
            "citations": citations,
            "scope": scope.as_dict(),
            "backend": "local",
            "intent": "summarize",
        }

    def _named_chars(self, question: str) -> list[tuple[str, str]]:
        """All characters explicitly named in the question (JP fragment or EN token)."""
        return named_characters(question, self._characters)

    def names_absent_character(
        self, question: str, arc_ids: tuple[str, ...]
    ) -> bool | None:
        """True when the question names character(s) but NONE of them appear in the
        scoped event(s) — the signal that a *carried* conversation focus is stale
        (the user asked about someone who isn't in the remembered event, so the
        answer should go global). False when at least one named character is
        present (keep the scope). None when the turn names no character, so the
        caller falls back to other signals.

        "Present" = speaks in the event OR is named in its prose (so a character who
        is narrated/discussed but never gets a line still counts). The prose check
        uses the distinctive JP full name only — EN name tokens are too short to
        match reliably and would keep the scope alive on an incidental mention."""
        targets = self._named_chars(question)
        if not targets or not arc_ids:
            return None
        idxs = self._candidate_indices(None, None, tuple(arc_ids))
        for jp, en in targets:
            en_tokens = {t for t in en.lower().split() if len(t) >= 2}
            for i in idxs:
                node = self.nodes[i]
                if jp and jp in node.text:  # narrated / third-person mention
                    return False
                if any(_speaker_is(t.speaker, jp, en_tokens) for t in node.dialogue_turns):
                    return False  # a named character is in scope -> stay put
        return True  # named characters, none present -> focus has gone stale

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
        # Count the event's OWN scenes only — a count of lines "in event X" must not
        # silently absorb its nested card/area children (keeps the exact contract).
        idxs = self._candidate_indices(
            scope.unit, scope.arc_id, scope.arc_ids, include_children=False
        )
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


def _positional_intent(question: str) -> str | None:
    """'late' for climax/ending questions, 'early' for beginning questions — used to
    bias scoped scene selection + quote ranking toward that end of the event. None
    when the question isn't positional or matches both (ambiguous)."""
    late = bool(_LATE_INTENT_RE.search(question))
    early = bool(_EARLY_INTENT_RE.search(question))
    if late == early:  # neither, or both -> no bias
        return None
    return "late" if late else "early"


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
    # Official-EN sidecars (*.md.en). Best-effort: a corpus without them (the
    # sample fixture, or a fetch that predates EN support) just stays JP-only.
    try:
        from .official_en import load_official_en

        official_en = load_official_en(story_root)
    except Exception:
        official_en = {}
    return LocalQueryEngine(nodes, events_index, glossary, event_summaries, official_en)


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
