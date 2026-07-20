"""Deterministic contextual-retrieval prefix (Anthropic-style).

Retrieval can't match what the text never says. A scene from ここからRE:START！
never contains the string "airi1" or "Airi Momoi's 1st focus event", so those
queries can't find it by meaning. Anthropic's Contextual Retrieval fixes this by
prepending a short situating context to each chunk *before* indexing.

This builds that context deterministically from event metadata (no LLM): the
event name, community nickname, "<character>'s Nth focus event", unit, and
commissioned song — keyed by ``arc_id`` (== ``arc_slug``). It is used two ways:

* **Local backend (free, immediate):** folded into the in-memory TF-IDF source
  at load time, so nickname/focus queries work with no re-embedding.
* **Full backend (teed up):** prepended to the Chroma embedding text + lexical
  index at ingest — this needs a re-embed to take effect (see the ingest path).

Kept short (structured facts, not prose) so it situates without diluting the
scene's own semantics.
"""

from __future__ import annotations

from ..source.constants import UNIT_NAMES

_ORDINALS = {1: "1st", 2: "2nd", 3: "3rd"}


def _ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        return f"{n}th"
    return _ORDINALS.get(n, f"{n}th") if n < 4 else f"{n}th"


def arc_context_line(meta: dict | None, *, focus_name_en: str | None = None) -> str:
    """One situating line for an event's scenes, or "" when metadata is missing.

    ``meta`` is an events-index row (name, nickname, focus_character [JP],
    focus_character_id, focus_index, unit, song_title). ``focus_name_en`` is the
    English focus-character name when the caller can resolve it (so both JP and EN
    name queries match).
    """
    if not meta:
        return ""
    bits: list[str] = []

    name = meta.get("name") or meta.get("arc_slug")
    if name:
        nickname = meta.get("nickname")
        bits.append(f"Event: {name}" + (f" (nickname {nickname})" if nickname else ""))

    focus_jp = meta.get("focus_character")
    focus_index = meta.get("focus_index")
    if meta.get("focus_character_id") and focus_index:
        # name both JP and EN so a query in either language matches
        who = " / ".join(n for n in (focus_name_en, focus_jp) if n) or "the focus character"
        bits.append(f"{who}'s {_ordinal(int(focus_index))} focus event")

    unit = UNIT_NAMES.get(meta.get("unit"), meta.get("unit"))
    if unit:
        bits.append(str(unit))

    song = meta.get("song_title")
    if song:
        bits.append(f"commissioned song {song}")

    return (". ".join(bits) + ".") if bits else ""
