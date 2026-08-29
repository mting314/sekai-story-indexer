"""Turn-level retrieval: find the *utterance* that answers a speaker-scoped
question, and keep its conversation around it as evidence.

Why this exists — a "scene" in the Sekai corpus is a whole episode. The fetcher
never writes the ``---`` delimiter, so ``split_into_scenes`` always returns one
chunk and a scene node averages ~48 dialogue turns. Scene-level TF-IDF therefore
only ever knows:

    does this episode mention X *somewhere* and the topic *somewhere*?

Those can be forty turns apart in unrelated conversations, which is how "Rin
talking about KAITO" outranks the line you actually asked for. Scoring the
**turn** fixes attribution.

But a bare turn is too narrow in the other direction: when another character
raises the topic and the person you asked about answers with a pronoun —

    Emu:    ...and you have a little brother, right, Honami?
    Honami: I'd like it if we went out more together, but he's at an age where...

— the answering line contains no topic word at all. So the unit here is a turn
**plus its conversational window**: score and attribute on the turn, return the
window so a reader (or the generator) can resolve "he".

Hits are tiered by how directly the named speaker is involved, strongest first:

    DIRECT  (3) the named speaker utters the topic themselves
    REPLY   (2) they answer within 2 turns, referring back ("he", 彼, その人)
    PRESENT (1) they merely have a line somewhere in the window

Tier 2's anaphora test is deliberately a *ranking* signal, not a filter. It is a
hand-written approximation of coreference and it will miss zero-pronoun Japanese;
demoting a hit degrades the ordering, whereas filtering on it would silently drop
true answers.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass

from ..models.story import StoryNode

# Referring expressions that mark a reply as being *about* the topic just raised.
# EN pronouns need word boundaries; the JP forms are substring-safe.
_ANAPHORA_RE = re.compile(
    r"\b(he|him|his|she|her|hers|they|them|their)\b|彼|彼女|その人|あの人|あの子",
    re.IGNORECASE,
)

TIER_DIRECT = 3
TIER_REPLY = 2
TIER_PRESENT = 1

#: How many turns either side of the topic turn form the conversational window.
DEFAULT_WINDOW = 4
#: A reply this many turns after the topic still counts as answering it.
_REPLY_SPAN = 2


@dataclass(frozen=True)
class TurnHit:
    """One topic mention, attributed to a named speaker, with its window."""

    node_index: int
    center: int  # turn index carrying the topic
    anchor: int  # turn index credited to the named speaker
    start: int  # window bounds, inclusive
    end: int
    tier: int
    coverage: int  # distinct content concepts matched inside the window
    quote: str  # the anchor turn, official-EN when available

    @property
    def sort_key(self) -> tuple:
        """Deterministic: strongest tier, then coverage, then tightest reply."""
        return (
            -self.tier,
            -self.coverage,
            abs(self.anchor - self.center),
            self.node_index,
            self.center,
        )


def turn_texts(
    node: StoryNode, en_map: dict[str, str] | None = None
) -> list[tuple[str, str]]:
    """``(searchable_text, display_text)`` per dialogue turn of a node.

    Searchable text folds in the official-EN line so an English question matches a
    Japanese transcript without a translation round-trip. Display prefers the EN
    rendering and falls back to ``speaker: text`` from the source.
    """
    lines = node.text.splitlines()
    out: list[tuple[str, str]] = []
    for turn in node.dialogue_turns:
        raw = ""
        if 0 <= turn.line_start < len(lines):
            raw = lines[turn.line_start].strip()
        english = (en_map or {}).get(raw, "")
        searchable = f"{turn.text}\n{english}".lower()
        out.append((searchable, english or f"{turn.speaker}: {turn.text}"))
    return out


def _covers(text: str, concept: Iterable[str]) -> bool:
    """True when any surface form of a concept occurs in the (lowercased) text."""
    return any(term in text for term in concept)


def find_turn_hits(
    nodes: Sequence[StoryNode],
    candidates: Iterable[int],
    *,
    content_concepts: Sequence[Sequence[str]],
    is_speaker: Callable[[str], bool],
    en_map: dict[str, str] | None = None,
    window: int = DEFAULT_WINDOW,
) -> list[TurnHit]:
    """Rank turn windows where ``is_speaker`` is involved in a content mention.

    ``content_concepts`` is one entry per topic the question asks about, each a
    list of interchangeable surface forms (e.g. ``["brother", "弟", "兄"]``).
    Returns hits sorted strongest-first; empty when the speaker is never near the
    topic, which lets the caller fall back to scene retrieval.
    """
    if not content_concepts:
        return []

    hits: list[TurnHit] = []
    for node_index in candidates:
        node = nodes[node_index]
        turns = node.dialogue_turns
        if not turns:
            continue
        texts = turn_texts(node, en_map)
        speaks = [is_speaker(t.speaker) for t in turns]
        if not any(speaks):
            continue

        for center, (searchable, _) in enumerate(texts):
            if not any(_covers(searchable, c) for c in content_concepts):
                continue
            start, end = max(0, center - window), min(len(turns) - 1, center + window)
            in_window = [i for i in range(start, end + 1) if speaks[i]]
            if not in_window:
                continue

            coverage = sum(
                1
                for concept in content_concepts
                if any(_covers(texts[i][0], concept) for i in range(start, end + 1))
            )
            if speaks[center]:
                tier, anchor = TIER_DIRECT, center
            else:
                replies = [
                    i
                    for i in in_window
                    if center < i <= center + _REPLY_SPAN
                    and _ANAPHORA_RE.search(texts[i][0])
                ]
                if replies:
                    tier, anchor = TIER_REPLY, replies[0]
                else:
                    tier = TIER_PRESENT
                    anchor = min(in_window, key=lambda i: (abs(i - center), i))

            hits.append(
                TurnHit(
                    node_index=node_index,
                    center=center,
                    anchor=anchor,
                    start=start,
                    end=end,
                    tier=tier,
                    coverage=coverage,
                    quote=texts[anchor][1],
                )
            )

    hits.sort(key=lambda h: h.sort_key)
    return hits


def window_lines(
    node: StoryNode, hit: TurnHit, en_map: dict[str, str] | None = None
) -> list[str]:
    """The hit's conversation, in reading order — evidence for resolving pronouns."""
    texts = turn_texts(node, en_map)
    return [texts[i][1] for i in range(hit.start, min(hit.end + 1, len(texts)))]
