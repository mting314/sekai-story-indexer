"""Deterministic answers for metadata questions the RAG can't reliably handle.

Questions like "Saki's first focus event" or "how many focus events does Kohane
have" are pure metadata lookups over events_index.json (focus_character_id,
nickname, focus_index) — the embedded story text doesn't contain the *concept* of
a focus event, so the LLM guesses. This intercept answers them exactly, with the
same {answer, answer_parts, citations} shape the query backends return, so the UI
renders clickable [n] citations. Returns None when the question isn't a metadata
lookup (caller then falls through to the RAG backend).

A "focus event" here = an event whose banner/focus character is that character
(the marathon single-unit-4★ rule already applied when the catalog was built);
its community nickname is <abbrev><index>, e.g. saki1.
"""

from __future__ import annotations

import datetime
import re

_ORDINALS = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
    "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10,
    "1st": 1, "2nd": 2, "3rd": 3, "4th": 4, "5th": 5, "6th": 6,
    "7th": 7, "8th": 8, "9th": 9, "10th": 10,
}
_LAST = {"last", "latest", "newest", "most recent"}
_FOCUS_RE = re.compile(r"focus\s+event", re.IGNORECASE)
# Pure-identity/count/list phrasings -> answer deterministically. If a CONTENT verb
# is present, the user is asking ABOUT the event (by its focus name) -> resolve the
# reference and let the RAG answer instead.
_LOOKUP_RE = re.compile(r"\b(which|what(?:'s| is| are| was)?|how many|number of|list|name)\b", re.IGNORECASE)
_CONTENT_RE = re.compile(
    r"\b(happen\w*|summar\w*|recap\w*|describ\w*|detail\w*|explain\w*|tell\s+me|"
    r"how\s+(?:do|does|did)|why|who|plot|storyline|arc|theme\w*|analy\w*|compar\w*|"
    r"what\s+about)\b",
    re.IGNORECASE,
)
_NICK_RE = re.compile(r"\b([a-z]{2,7}\d+(?:-\d+)?)\b", re.IGNORECASE)


def _fmt_date(ms: int) -> str:
    if not ms:
        return "?"
    return datetime.datetime.fromtimestamp(ms / 1000, datetime.UTC).strftime("%Y-%m-%d")


def _char_id_map(characters: dict) -> list[tuple[str, int]]:
    """(name_lower, id) pairs, longest name first. Given names <3 chars (only
    'An') are excluded to avoid matching the English article."""
    pairs: list[tuple[str, int]] = []
    for cid, c in characters.items():
        en = c.get("en", "")
        pairs.append((en.lower(), int(cid)))
        given = en.split(" ")[0]
        if given and given.lower() != en.lower() and len(given) >= 3:
            pairs.append((given.lower(), int(cid)))
    pairs.sort(key=lambda p: -len(p[0]))
    return pairs


def _resolve_char(q: str, characters: dict) -> int | None:
    for name, cid in _char_id_map(characters):
        if re.search(rf"\b{re.escape(name)}\b", q):
            return cid
    return None


def _citation(ref: int, e: dict, summaries: dict) -> dict:
    arc = e.get("arc_slug", "")
    val = summaries.get(arc, "")
    excerpt = val if isinstance(val, str) else (val or {}).get("summary", "")
    excerpt = re.sub(r"\{char_id=\d+\}", "", excerpt)
    return {
        "ref": ref, "arc_id": arc, "label": e.get("name") or arc,
        "nickname": e.get("nickname"), "excerpt": excerpt,
    }


def metadata_answer(question: str, events_index: list[dict], characters: dict, summaries: dict | None = None) -> dict | None:
    """Answer a focus-event metadata question, or None to fall through to RAG."""
    summaries = summaries or {}
    # Only answer PURE identity/count/list questions here. Content questions that
    # merely *refer* to an event by its focus name (e.g. "what happens in Saki's
    # first focus event") must fall through so the RAG answers — the caller uses
    # resolve_focus_reference() to point the RAG at the right event.
    if not _FOCUS_RE.search(question) or _CONTENT_RE.search(question):
        return None
    if not _LOOKUP_RE.search(question):
        return None
    q = question.lower()
    cid = _resolve_char(q, characters)
    if cid is None:
        return None

    events = sorted(
        (e for e in events_index if e.get("focus_character_id") == cid),
        key=lambda e: (e.get("focus_index") or 0, e.get("started_at") or 0),
    )
    name = characters.get(str(cid), {}).get("en", f"character {cid}")
    if not events:
        return {
            "answer": f"{name} has no focus events on record.",
            "answer_parts": [{"type": "text", "text": f"{name} has no focus events on record."}],
            "citations": [], "error": None, "backend": "metadata", "intent": "focus_event",
        }

    # which one(s)?
    ordinal = next((v for k, v in _ORDINALS.items() if re.search(rf"\b{k}\b", q)), None)
    want_last = any(w in q for w in _LAST)
    is_count = bool(re.search(r"how many|number of|count", q))
    is_list = bool(re.search(r"\ball\b|\blist\b|\bevents\b.*\?|which .*events", q)) or "focus events" in q

    def line(i: int, e: dict) -> str:
        return f"[{i}] **{e.get('name')}** ({e.get('nickname')}) — {_fmt_date(e.get('started_at'))}"

    if is_count and not ordinal and not want_last:
        picks = events
        head = f"{name} has {len(events)} focus event{'s' if len(events) != 1 else ''}:"
    elif ordinal:
        if ordinal > len(events):
            return {
                "answer": f"{name} has only {len(events)} focus events, so there is no #{ordinal}.",
                "answer_parts": [{"type": "text", "text": f"{name} has only {len(events)} focus events, so there is no #{ordinal}."}],
                "citations": [], "error": None, "backend": "metadata", "intent": "focus_event",
            }
        picks = [events[ordinal - 1]]
        e = picks[0]
        head = f"{name}'s #{ordinal} focus event is [1] **{e.get('name')}** ({e.get('nickname')}), released {_fmt_date(e.get('started_at'))}."
        return _pack(head, picks, summaries, single=True)
    elif want_last:
        picks = [events[-1]]
        e = picks[0]
        head = f"{name}'s most recent focus event is [1] **{e.get('name')}** ({e.get('nickname')}), released {_fmt_date(e.get('started_at'))}."
        return _pack(head, picks, summaries, single=True)
    else:  # list all
        picks = events
        head = f"{name} has {len(events)} focus events:"

    body = "\n".join(line(i, e) for i, e in enumerate(picks, 1))
    text = f"{head}\n{body}"
    return _pack(text, picks, summaries)


def resolve_focus_reference(question: str, events_index: list[dict], characters: dict) -> dict | None:
    """Resolve a focus-event REFERENCE in a (content) question to a single event, so
    the RAG can be pointed at it. Handles a bare nickname ('saki1', 'kasa5',
    'wl2-4') or 'X's <ordinal|last> focus event'. Returns the event dict or None."""
    by_nick = {(e.get("nickname") or "").lower(): e for e in events_index if e.get("nickname")}
    for m in _NICK_RE.finditer(question):
        e = by_nick.get(m.group(1).lower())
        if e:
            return e
    if not _FOCUS_RE.search(question):
        return None
    q = question.lower()
    cid = _resolve_char(q, characters)
    if cid is None:
        return None
    events = sorted(
        (e for e in events_index if e.get("focus_character_id") == cid),
        key=lambda e: (e.get("focus_index") or 0, e.get("started_at") or 0),
    )
    if not events:
        return None
    ordinal = next((v for k, v in _ORDINALS.items() if re.search(rf"\b{k}\b", q)), None)
    if ordinal:
        return events[ordinal - 1] if ordinal <= len(events) else None
    if any(w in q for w in _LAST):
        return events[-1]
    # singular "X's focus event" with no ordinal -> default to the first
    if re.search(r"focus event\b", q) and not re.search(r"focus events\b", q):
        return events[0]
    return None


def _pack(text: str, picks: list[dict], summaries: dict, single: bool = False) -> dict:
    citations = [_citation(i, e, summaries) for i, e in enumerate(picks, 1)]
    return {
        "answer": text,
        "answer_parts": [{"type": "text", "text": text}],
        "citations": citations,
        "error": None,
        "backend": "metadata",
        "intent": "focus_event",
    }
