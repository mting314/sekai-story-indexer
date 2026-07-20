"""Shared hierarchical summary prompt — one source of truth for every backend.

Mirrors the original linkura summarizer (``ahuei123456/linkura-story-indexer``,
``indexer/summarizer.py``): tier-specific prompts (Episode -> Event -> Unit),
each synthesizing the tier below, with a rolling PREVIOUS CONTEXT block for
continuity, an OFFICIAL GLOSSARY of mandatory JP->EN translations, and an
English-pinned system message.

Our additions over linkura:
  * ground-truth group ROSTERS (with ids), so the model never guesses membership;
  * inline character tags ``Name{char_id=ID}`` on every mention, so the reader
    colors names by exact span + id instead of guessing.

NOTE: ``scripts/summarize_ollama.py`` keeps a self-contained copy of these
strings (it must run without this package installed). This module is canonical —
keep the two in sync.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

_META_CANDIDATES = (
    Path("webapp/static/meta.json"),
    Path(__file__).resolve().parents[3] / "webapp" / "static" / "meta.json",
)


def _load_meta() -> dict:
    for p in _META_CANDIDATES:
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                return {}
    return {}


_meta = _load_meta()
ID_TO_EN: dict[int, str] = {int(cid): c["en"] for cid, c in _meta.get("characters", {}).items()}
NAME_TO_ID: dict[str, int] = {c["en"]: int(cid) for cid, c in _meta.get("characters", {}).items()}
UNIT_NAMES: dict[str, str] = {slug: u["name"] for slug, u in _meta.get("units", {}).items()}
VALID_IDS: set[int] = set(ID_TO_EN)

_UNIT_ORDER = [
    "leo_need", "more_more_jump", "vivid_bad_squad",
    "wonderlands_showtime", "nightcord", "virtual_singer",
]
_roster: dict[str, list[tuple[int, str]]] = {}
for _cid, _c in _meta.get("characters", {}).items():
    _roster.setdefault(_c.get("unit", "other"), []).append((int(_cid), _c["en"]))
# Roster doubles as the id table AND a worked example of the tag format.
ROSTER_TEXT = "\n".join(
    f"- {UNIT_NAMES.get(slug, slug)}: "
    + ", ".join(f"{en}{{char_id={cid}}}" for cid, en in _roster[slug])
    for slug in _UNIT_ORDER
    if slug in _roster
) or "(rosters unavailable)"


def _load_glossary() -> dict:
    for p in (Path("glossary.json"), Path(__file__).resolve().parents[3] / "glossary.json"):
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                return {}
    return {}


def _glossary_text(g: dict) -> str:
    lines: list[str] = []
    for cat, terms in g.items():
        if isinstance(terms, dict) and terms:
            lines.append(f"{cat.replace('_', ' ').upper()}:")
            lines += [f" - {jp} -> {en}" for jp, en in terms.items()]
    return "\n".join(lines)


GLOSSARY_TEXT = _glossary_text(_load_glossary())

SYSTEM = (
    "You are an expert archivist and translator indexing Japanese Project Sekai "
    "(Hatsune Miku: Colorful Stage!) event stories. You write all summaries in "
    "clear, concise ENGLISH — never Japanese.\n\n"
    "MAIN CAST: the roster lists the 26 main characters with their ids and groups. "
    "For a listed character, use their exact English name, never move them to a "
    "different group, and tag each mention with their id, e.g. "
    "`Kohane Azusawa{char_id=13}` (and `Kohane{char_id=13}` later). Never tag a "
    "character with another character's id.\n"
    "SIDE CHARACTERS: minor, guest, and one-off characters who are NOT on the "
    "roster also appear — refer to them faithfully by the name the story uses "
    "(romanized), do NOT add a {char_id} tag to them (they have no id), and NEVER "
    "substitute a main character for a side character. If the story's focus is a "
    "side character (e.g. someone who is ill, a mentor, a rival), name that side "
    "character — do not misattribute their role to a roster member.\n"
    "Do not fabricate events or people that are not in the text."
)
if GLOSSARY_TEXT:
    SYSTEM += (
        "\n\n--- OFFICIAL GLOSSARY (MANDATORY TRANSLATIONS) ---\n"
        "When translating or referencing names and terms, you MUST use these "
        "English equivalents:\n" + GLOSSARY_TEXT
    )

_GLOBAL_RULES = (
    "Global formatting rules:\n"
    "- Write in clear, concise English.\n"
    "- Use exactly the required `## ` section headings for the tier, and emit every one.\n"
    "- If a bullet-list section has no entries, write exactly `- None`.\n"
    "- Tag each mention of a ROSTER character as Name{char_id=ID}; name side "
    "characters plainly (no tag).\n"
    "- Do not add extra sections, tables, or bold text."
)

# Tier-specific required formats (markdown). Kept intentionally lighter than
# linkura's (we want concise), but same structured spirit + our inline tags.
_FORMATS = {
    "episode": (
        "Required Episode summary format:\n\n"
        "## Overview\n"
        "[2-4 sentences of concrete scene progression for THIS episode, inline-tagged.]\n\n"
        "## Key Events\n"
        "- [chronological beat, inline-tagged]\n\n"
        "## Character Developments\n"
        "- Name{char_id=ID}: [concrete emotional/relational/goal change]"
    ),
    "event": (
        "Required Event summary format:\n\n"
        "## Overview\n"
        "[1-2 short paragraphs synthesizing the whole event arc, inline-tagged. "
        "Do not copy the episode sections verbatim.]\n\n"
        "## Key Events\n"
        "- [major beat across the event, inline-tagged]\n\n"
        "## Character Developments\n"
        "- Name{char_id=ID}: [change across the event]"
    ),
    "unit": (
        "Required Unit summary format:\n\n"
        "## Overview\n"
        "[1-2 short paragraphs on this group's overall story so far, inline-tagged.]\n\n"
        "## Arc Progression\n"
        "- [event name]: [one line of what it advances, inline-tagged]\n\n"
        "## Character Arcs\n"
        "- Name{char_id=ID}: [their through-line across events]"
    ),
}

# "event_raw" = summarize a whole event directly from raw text in one pass (the
# Google single-shot path); it reuses the event output format.
_FORMATS["event_raw"] = _FORMATS["event"]
_INPUT_LABEL = {
    "episode": "CURRENT EPISODE TEXT (RAW JAPANESE STORY)",
    "event": "CURRENT EVENT INPUT (THIS EVENT'S EPISODE SUMMARIES)",
    "event_raw": "CURRENT EVENT TEXT (RAW JAPANESE STORY)",
    "unit": "CURRENT UNIT INPUT (THIS UNIT'S EVENT SUMMARIES)",
}
_INPUT_INSTR = {
    "episode": "Summarize only this episode's source text; use previous context only to resolve references.",
    "event": "Synthesize across the child episode summaries into one event-level summary. Do not concatenate or copy child sections verbatim.",
    "event_raw": "Summarize the entire event story in one pass.",
    "unit": "Synthesize across the event summaries into a unit-level through-line. Do not concatenate or copy child sections verbatim.",
}


def build_context(unit: str, focus_id: int | None = None, song: str | None = None) -> str:
    bits = [f"This is a {UNIT_NAMES.get(unit, unit)} event story."]
    if focus_id and int(focus_id) in ID_TO_EN:
        bits.append(f"Its focus character is {ID_TO_EN[int(focus_id)]}{{char_id={int(focus_id)}}}.")
    if song:
        bits.append(f'The featured song is "{song}".')
    return " ".join(bits)


def build_prompt(
    tier: str,
    unit: str,
    current_text: str,
    *,
    prev_summary: str | None = None,
    focus_id: int | None = None,
    song: str | None = None,
) -> str:
    """Assemble a tier prompt. ``current_text`` is raw story text (episode tier) or
    concatenated child summaries (event/unit tiers)."""
    parts = [
        "MAIN CAST (use these EXACT names + ids; never reassign anyone to another "
        "group). Side/guest characters not listed here may also appear — name them "
        "faithfully from the text, without an id tag:",
        ROSTER_TEXT,
        build_context(unit, focus_id, song),
    ]
    if prev_summary:
        parts += [
            "--- PREVIOUS CONTEXT (for continuity only) ---",
            prev_summary.strip(),
            "Use previous context only to resolve references, pronouns, chronology, and "
            "ongoing situations. Do not re-summarize prior events or copy prior sections.",
        ]
    parts += [
        f"--- {_INPUT_LABEL.get(tier, tier.upper())} ---",
        current_text,
        _INPUT_INSTR.get(tier, ""),
        _GLOBAL_RULES,
        _FORMATS[tier],
        f"Write the {tier} summary now using exactly the required format, in English, with inline character tags.",
    ]
    return "\n\n".join(p for p in parts if p)


_TAG_RE = re.compile(r"\{char_id=(\d+)\}")


def extract_char_ids(markdown: str) -> list[int]:
    """The inline tags ARE the character roster — derive validated ids from them."""
    ids = {int(m) for m in _TAG_RE.findall(markdown or "")}
    return sorted(i for i in ids if i in VALID_IDS)


def clean_markdown(markdown: str) -> str:
    """Drop any invalid {char_id=N} tags (unknown id) but keep the name text."""
    return _TAG_RE.sub(lambda m: "" if int(m.group(1)) not in VALID_IDS else m.group(0), markdown or "")


def parse_summary(out: str) -> dict:
    """Extract {summary(markdown), characters:[ids]} from a model reply.

    Accepts a JSON envelope ``{"summary": "<markdown>"}`` or bare markdown."""
    md = None
    m = re.search(r"\{.*\}", out or "", re.S)
    if m:
        try:
            obj = json.loads(m.group(0))
            if isinstance(obj, dict) and obj.get("summary"):
                md = obj["summary"]
        except Exception:
            md = None
    if md is None:
        md = (out or "").strip()
    md = clean_markdown(md).strip()
    return {"summary": md, "characters": extract_char_ids(md)}
