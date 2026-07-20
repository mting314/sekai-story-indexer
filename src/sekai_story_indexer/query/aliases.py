"""Multilingual event-title matching.

Players refer to an event by any of: the Japanese title, the official English
title, a romaji transliteration, or the community nickname. Embeddings match
these only fuzzily, so this module resolves them *deterministically* from an
event's alias set — used by the scope resolver (which arc to retrieve) and the
clarify gate (is the reference ambiguous?).

Alias sources per event:
  * ``name`` / ``name_jp`` — JP and (after the EN overlay) official-EN titles
  * the arc slug's romaji tail — ``0188-aogu-yozora-ni-...`` -> ``aogu yozora ...``
  * (nickname is handled separately by the scope resolver)

Matching is guarded against false positives: a CJK title must appear as a
substring; an EN/romaji title needs >=2 distinctive content tokens present (or one
long, distinctive token), so a title made of common words won't match everything.
"""

from __future__ import annotations

import re

_WORD_RE = re.compile(r"[a-z0-9]+")
_CJK_RE = re.compile(r"[぀-ヿ㐀-鿿]")

# generic English words + romaji Japanese particles that carry no title-identity
_STOP = {
    "the", "a", "an", "of", "to", "and", "in", "on", "for", "with", "as", "at",
    "is", "are", "be", "my", "our", "your", "story", "event", "side", "part",
    # romaji particles / grammatical fragments
    "ni", "ha", "wa", "wo", "no", "ga", "mo", "de", "e", "he", "ya", "ka", "na",
    "te", "ta", "da", "to",
}


def _tokens(text: str) -> list[str]:
    return _WORD_RE.findall(text.lower())


def _distinctive(text: str) -> set[str]:
    return {t for t in _tokens(text) if t not in _STOP and len(t) >= 2}


def _romaji_from_slug(arc_slug: str) -> str:
    """'0188-aogu-yozora-ni-hoshi-ha-magire-te' -> 'aogu yozora ni hoshi ha magire te'."""
    return re.sub(r"^\d+-", "", arc_slug or "").replace("-", " ")


def event_alias_texts(event: dict) -> list[str]:
    """Every title-ish alias string for an event (JP, EN, romaji)."""
    out: list[str] = []
    for key in ("name", "name_jp"):
        v = event.get(key)
        if v:
            out.append(v)
    slug = event.get("arc_slug")
    if slug:
        out.append(_romaji_from_slug(slug))
    return out


def _matches(question: str, qtokens: set[str], event: dict) -> bool:
    for alias in event_alias_texts(event):
        if _CJK_RE.search(alias):
            # JP/CJK title (no word spacing): a full-title substring is distinctive
            if len(alias) >= 3 and alias in question:
                return True
            continue
        dist = _distinctive(alias)
        if not dist:
            continue
        overlap = qtokens & dist
        # >=2 distinctive title tokens present, OR the title is essentially a single
        # distinctive word and that word appears. Requiring 2 tokens for multi-word
        # titles avoids false scope from one common word (e.g. "friendship" alone
        # must not match "Re-tie Friendship").
        if len(overlap) >= 2 or (len(dist) == 1 and overlap):
            return True
    return False


def event_title_matches(question: str, events: list[dict]) -> list[dict]:
    """Events whose title (any language/form) is referenced in the question,
    de-duplicated by arc, in index order."""
    qtokens = set(_tokens(question))
    out: list[dict] = []
    seen: set[str] = set()
    for e in events:
        arc = e.get("arc_slug")
        if not arc or arc in seen:
            continue
        if _matches(question, qtokens, e):
            seen.add(arc)
            out.append(e)
    return out
