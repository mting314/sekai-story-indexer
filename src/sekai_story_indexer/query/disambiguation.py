"""Clarify-instead-of-guess gate for ambiguous entity references.

Retrieval always returns *something*, so an ambiguous reference like
"summarize that rise as one story" silently resolves to whatever ranked top —
the whole-arc guess this module exists to prevent (see DESIGN.md / the
conversational-RAG notes). Rather than answer confidently from one arbitrary
interpretation, we enumerate the distinct candidate interpretations and, when
there are two or more, return a *clarify* response asking the user to pick.

The trigger is intent–evidence ambiguity, NOT a similarity threshold: a phrase
that matches an event by its **title** AND a character whose **arc spans
multiple focus events** is two distinct interpretations, so we ask. This is the
deterministic, no-LLM check the local engine needed — it composes with the
existing nickname/unit/World-Link resolution (an explicit nickname short-circuits
the gate, since the user already disambiguated).

Pure and side-effect free so it unit-tests with synthetic colliding fixtures.
"""

from __future__ import annotations

import re

from .aliases import event_title_matches
from .intent import classify
from .metadata import _NICK_RE, _ORDINALS, _char_id_map


def _named_char_ids(question_lower: str, characters: dict) -> list[int]:
    """Character ids explicitly named in the question, longest name first (so a
    full name wins over a shared given name), de-duplicated in first-seen order."""
    ids: list[int] = []
    for name, cid in _char_id_map(characters):
        if re.search(rf"\b{re.escape(name)}\b", question_lower) and cid not in ids:
            ids.append(cid)
    return ids


def _has_explicit_disambiguator(question: str, events: list[dict]) -> bool:
    """The user already pinned the reference: a nickname token that resolves to a
    known event (e.g. 'saki3'), or an ordinal ('first'/'2nd') on a focus event."""
    by_nick = {(e.get("nickname") or "").lower() for e in events if e.get("nickname")}
    for m in _NICK_RE.finditer(question):
        if m.group(1).lower() in by_nick:
            return True
    ql = question.lower()
    return any(re.search(rf"\b{k}\b", ql) for k in _ORDINALS)


def _event_candidate(e: dict) -> dict:
    return {
        "kind": "event",
        "arc_slug": e.get("arc_slug"),
        "event_id": e.get("event_id"),
        "label": e.get("name") or e.get("arc_slug"),
        "nickname": e.get("nickname"),
    }


def _character_arc_candidate(cid: int, events: list[dict], characters: dict) -> dict | None:
    focus = sorted(
        (e for e in events if e.get("focus_character_id") == cid),
        key=lambda e: e.get("arc_slug") or "",
    )
    if len(focus) < 2:
        return None
    name = characters.get(str(cid), {}).get("en", f"character {cid}")
    return {
        "kind": "character_arc",
        "character_id": cid,
        "label": f"{name}'s overall story arc",
        "count": len(focus),
        "events": [e.get("arc_slug") for e in focus],
    }


def find_candidates(
    question: str,
    events: list[dict],
    characters: dict,
    *,
    focus_character_id: int | None = None,
) -> list[dict]:
    """Enumerate the distinct interpretations of an entity reference.

    Two ambiguities are detectable from the question text alone:
      (A) a named character whose arc spans >=2 focus events, referenced without
          an ordinal or nickname — "summarize Honami's story" (which event?);
      (B) a title phrase that matches >=2 events — e.g. a rerun/collab reusing a
          name (which one?).
    Returns a list of candidate dicts (kind='event' | 'character_arc'), deduped by
    arc. Fewer than two means no ambiguity — the caller proceeds normally. Returns
    ``[]`` when the user already disambiguated explicitly (nickname/ordinal).

    (C) the conversational case: this turn names ONE event by title while the
    remembered ``focus_character_id`` has a multi-event arc — "summarize that rise
    as one story" while we were discussing Honami. That's event-vs-arc ambiguity
    only visible with session focus state, hence the parameter.
    """
    if _has_explicit_disambiguator(question, events):
        return []

    candidates: list[dict] = []
    seen_arcs: set[str] = set()

    def add_arc_events(arc_slugs: list[str | None]) -> None:
        for arc in arc_slugs:
            if not arc or arc in seen_arcs:
                continue
            e = next((x for x in events if x.get("arc_slug") == arc), None)
            if e:
                seen_arcs.add(arc)
                candidates.append(_event_candidate(e))

    # (B) event-title collisions (JP / EN / romaji, via the shared matcher)
    for e in event_title_matches(question, events):
        arc = e.get("arc_slug")
        if arc and arc not in seen_arcs:
            seen_arcs.add(arc)
            candidates.append(_event_candidate(e))

    # (A) a named character with a multi-event arc -> offer the overall arc plus
    # each specific focus event. Only for summarize/"which story" requests: a
    # thematic question ("how does Kohane feel about singing") is answerable across
    # her scenes and is NOT ambiguous, so it must not trigger a clarify.
    if classify(question) == "summarize":
        for cid in _named_char_ids(question.lower(), characters):
            cand = _character_arc_candidate(cid, events, characters)
            if cand:
                candidates.append(cand)
                add_arc_events(cand["events"])

    # (C) conversational: a title match this turn + a remembered multi-event focus
    # character -> "the event, or that character's arc?". Only when a specific
    # event is already a candidate (so a bare pronoun follow-up doesn't clarify).
    if (
        focus_character_id is not None
        and candidates
        and not any(c.get("character_id") == focus_character_id for c in candidates)
    ):
        cand = _character_arc_candidate(focus_character_id, events, characters)
        if cand:
            candidates.append(cand)

    return candidates


def clarify_response(candidates: list[dict]) -> dict:
    """Build a clarify turn (same {answer, answer_parts, citations} shape the query
    backends return, so the existing UI renders it as a normal assistant message).
    ``options`` carries the structured choices for a future click-to-pick UI."""
    lines = ["That could mean a few things — which did you mean?"]
    options: list[dict] = []
    for c in candidates[:5]:
        if c["kind"] == "event":
            nick = f" [{c['nickname']}]" if c.get("nickname") else ""
            lines.append(f"- the event **{c['label']}**{nick}")
            options.append({
                "type": "event", "label": c["label"],
                "arc_slug": c.get("arc_slug"), "event_id": c.get("event_id"),
            })
        else:
            lines.append(f"- {c['label']} ({c['count']} focus events)")
            options.append({
                "type": "character_arc", "label": c["label"],
                "character_id": c.get("character_id"),
            })
    text = "\n".join(lines)
    return {
        "answer": text,
        "answer_parts": [{"type": "text", "text": text}],
        "citations": [],
        "options": options,
        "backend": "clarify",
        "intent": "clarify",
        "error": None,
    }


def maybe_clarify(
    question: str,
    events: list[dict],
    characters: dict,
    *,
    focus_character_id: int | None = None,
) -> dict | None:
    """Return a clarify response if the reference is ambiguous (>=2 distinct
    interpretations), else None so the caller proceeds to normal retrieval."""
    candidates = find_candidates(
        question, events, characters, focus_character_id=focus_character_id
    )
    if len(candidates) >= 2:
        return clarify_response(candidates)
    return None
