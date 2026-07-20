"""Shared event-summary prompt — one source of truth for every backend.

Both the Google path (``indexer/event_summarizer.py``) and the standalone local
scripts summarize with the SAME instructions: pin English output, give the model
ground-truth group rosters so it never guesses membership, and inject per-event
context (unit + focus character + song). Rosters are derived from the same
``webapp/static/meta.json`` the web app uses, so names stay consistent.

NOTE: ``scripts/summarize_ollama.py`` keeps a self-contained copy of these
strings (it must run on a machine without this package installed). Keep the two
in sync — the wording here is canonical.
"""

from __future__ import annotations

import json
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


def _load_glossary() -> dict:
    for p in (Path("glossary.json"), Path(__file__).resolve().parents[3] / "glossary.json"):
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                return {}
    return {}


def _glossary_text(g: dict) -> str:
    """linkura-style 'JP -> EN' mandatory-translation block, by category."""
    lines: list[str] = []
    for cat, terms in g.items():
        if isinstance(terms, dict) and terms:
            lines.append(f"{cat.replace('_', ' ').upper()}:")
            lines += [f" - {jp} -> {en}" for jp, en in terms.items()]
    return "\n".join(lines)


GLOSSARY_TEXT = _glossary_text(_load_glossary())

_UNIT_ORDER = [
    "leo_need", "more_more_jump", "vivid_bad_squad",
    "wonderlands_showtime", "nightcord", "virtual_singer",
]
_roster: dict[str, list[str]] = {}
for _cid, _c in _meta.get("characters", {}).items():
    _roster.setdefault(_c.get("unit", "other"), []).append(_c["en"])
ROSTER_TEXT = "\n".join(
    f"- {UNIT_NAMES.get(slug, slug)}: {', '.join(_roster[slug])}"
    for slug in _UNIT_ORDER
    if slug in _roster
) or "(rosters unavailable)"

SYSTEM = (
    "You are an expert archivist and translator indexing Japanese Project Sekai "
    "(Hatsune Miku: Colorful Stage!) event stories. You write all summaries in "
    "clear, concise ENGLISH — never Japanese. You use only the character names and "
    "group memberships given to you; you never invent characters or move a "
    "character to a different group."
)
if GLOSSARY_TEXT:
    SYSTEM += (
        "\n\n--- OFFICIAL GLOSSARY (MANDATORY TRANSLATIONS) ---\n"
        "When translating or referencing names and terms, you MUST use these "
        "English equivalents:\n" + GLOSSARY_TEXT
    )

PROMPT = """Character groups (ground truth — use these EXACT English names and do NOT reassign anyone to a different group):
{roster}

{context}

Summarize the Japanese event story below in 1-2 short paragraphs of clear ENGLISH prose. Focus on plot progression and character development. Do not invent anything that is not in the text. Write the summary in English only.

Write clean prose only: no headings, numbered steps, bullet points, outlines, draft notes, or labels like "Paragraph 1" or "Refine". Do not describe your process.

After the summary, list which of the characters above actually appear in this story (exact English names).

Return ONLY a JSON object and nothing else:
{{"summary": "<the English summary>", "characters": ["<name>", "<name>"]}}

Story (Japanese):
{body}"""


def build_context(unit: str, focus_id: int | None = None, song: str | None = None) -> str:
    """One-line ground-truth context: unit, focus character, featured song."""
    bits = [f"This is a {UNIT_NAMES.get(unit, unit)} event story."]
    if focus_id and int(focus_id) in ID_TO_EN:
        bits.append(f"Its focus character is {ID_TO_EN[int(focus_id)]}.")
    if song:
        bits.append(f'The featured song is "{song}".')
    return " ".join(bits)


def build_prompt(unit: str, body: str, *, focus_id: int | None = None, song: str | None = None) -> str:
    return PROMPT.format(roster=ROSTER_TEXT, context=build_context(unit, focus_id, song), body=body)


def parse_summary(out: str) -> dict:
    """Extract {summary, characters:[ids]} from a model's JSON reply; fall back to
    treating the whole text as the summary."""
    import re

    m = re.search(r"\{.*\}", out or "", re.S)
    if m:
        try:
            obj = json.loads(m.group(0))
            summ = (obj.get("summary") or "").strip()
            chars = sorted({NAME_TO_ID[n] for n in obj.get("characters", []) if n in NAME_TO_ID})
            if summ:
                return {"summary": summ, "characters": chars}
        except Exception:
            pass
    return {"summary": (out or "").strip(), "characters": []}
