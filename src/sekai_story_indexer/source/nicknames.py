"""Community event-nickname system, e.g. ``kasa5`` / ``mizu3``.

Convention: a short (~2-mora) abbreviation of the event's **focus character** +
the running count of that character's focus events, in release order. So
``kasa5`` = Tsukasa's 5th focus event; ``mizu3`` = Mizuki's 3rd.

The **numbering is data-driven** (count focus events per character
chronologically from the master DB). Only the **abbreviations** are a fixed
fandom convention: the map below is a best-effort seed — ``kasa`` and ``mizu``
are user-confirmed, the rest are first-mora defaults pending community
verification. Override any of them via a root ``nicknames.json`` mapping
``{"<abbrev>": <characterId>}`` (see :func:`load_overrides`).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .constants import CHARACTER_ID_TO_JP

# characterId -> abbreviation. CONFIRMED: 11 (aki), 13 (kasa), 20 (mizu). Others: seed.
CHARACTER_ID_TO_ABBREV: dict[int, str] = {
    1: "ichi", 2: "saki", 3: "hona", 4: "shiho",
    5: "mino", 6: "haru", 7: "airi", 8: "shizu",
    9: "koha", 10: "an", 11: "aki", 12: "toya",
    13: "kasa", 14: "emu", 15: "nene", 16: "rui",
    17: "kana", 18: "mafu", 19: "ena", 20: "mizu",
    21: "miku", 22: "rin", 23: "len", 24: "luka",
    25: "meiko", 26: "kaito",
}

_NICK_RE = re.compile(r"^([a-z]+)\s*[-_ ]?\s*(\d+)$", re.IGNORECASE)


def abbrev_to_character_id() -> dict[str, int]:
    return {abbrev.lower(): cid for cid, abbrev in CHARACTER_ID_TO_ABBREV.items()}


def load_overrides(path: str | Path = Path("nicknames.json")) -> None:
    """Merge ``{"<abbrev>": <characterId>}`` overrides from a JSON file, if present."""
    p = Path(path)
    if not p.exists():
        return
    data = json.loads(p.read_text(encoding="utf-8"))
    for abbrev, cid in data.items():
        CHARACTER_ID_TO_ABBREV[int(cid)] = abbrev.lower()


def parse_nickname(nick: str) -> tuple[str, int] | None:
    """``"kasa5"`` -> ``("kasa", 5)``; tolerates ``kasa-5`` / ``kasa 5``."""
    m = _NICK_RE.match(nick.strip())
    if not m:
        return None
    return m.group(1).lower(), int(m.group(2))


def resolve_nickname(nick: str) -> tuple[int, int] | None:
    """``"kasa5"`` -> ``(characterId=13, focus_index=5)``, or None if unknown."""
    parsed = parse_nickname(nick)
    if not parsed:
        return None
    abbrev, index = parsed
    cid = abbrev_to_character_id().get(abbrev)
    if cid is None:
        return None
    return cid, index


def nickname_for(character_id: int, focus_index: int) -> str | None:
    abbrev = CHARACTER_ID_TO_ABBREV.get(character_id)
    return f"{abbrev}{focus_index}" if abbrev else None


def assign_focus_nicknames(
    events: list[dict],
) -> dict[int, dict]:
    """Assign each event its focus nickname from its focus character.

    ``events`` items need ``event_id``, ``focus_character_id`` (0/None if
    unknown), and ``started_at``. Returns ``{event_id: {focus_character_id,
    focus_character, focus_index, nickname}}`` for events with a known single
    focus character. Focus index counts that character's focus events in release
    order (1-based).
    """
    ordered = sorted(events, key=lambda e: (e.get("started_at", 0), e.get("event_id", 0)))
    counts: dict[int, int] = {}
    out: dict[int, dict] = {}
    for ev in ordered:
        cid = ev.get("focus_character_id") or 0
        if not cid:
            continue
        counts[cid] = counts.get(cid, 0) + 1
        out[ev["event_id"]] = {
            "focus_character_id": cid,
            "focus_character": CHARACTER_ID_TO_JP.get(cid, str(cid)),
            "focus_index": counts[cid],
            "nickname": nickname_for(cid, counts[cid]),
        }
    return out
