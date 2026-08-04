"""Sekaipedia lyric fetch + parse — the keyless read side of lyric TEXT.

Lyric text is copyrighted: it is fetched **live** by stable ``pageid`` (from
``lyric_page_map.json``) at analysis time and **never persisted** — only the derived
resonance notes are cached. No API key needed (plain HTTP + parsing), but egress to
Sekaipedia is required. The pure parser (`parse_lyrics_wikitext`) is separated from
the network fetch so it's unit-testable offline.

Sekaipedia lyrics live in a ``== Lyrics ==`` section as a ``<tabber>`` of versions
(e.g. "Game Version" / "Full Version"), each a run of::

    {{Lyrics line
    | japanese = {{Lyric|Singer|<text, may span lines>}}{{Lyric|Other|…}}
    | romaji   = {{Lyric|Singer|…}}{{Lyric|Other|…}}
    | english  = {{Lyric|Singer|…}}{{Lyric|Other|…}}
    }}

and an optional ``{{Lyrics tail|<attribution>}}``. The three columns carry the same
sequence of ``{{Lyric}}`` singers, so we zip them into per-line segments.
"""

from __future__ import annotations

import json
import re
import urllib.parse
from pathlib import Path

from .client import fetch_json
from .constants import SEKAIPEDIA_API

# One {{Lyric|Singer|text}} call. Non-greedy to the first }} (lyric text has no }}).
_LYRIC_TMPL = re.compile(r"\{\{Lyric\|([^|}]*)\|(.*?)\}\}", re.DOTALL)
# Column separators inside a {{Lyrics line}} block (line-anchored | name =).
_COLUMN_SPLIT = re.compile(r"\n\s*\|\s*(japanese|romaji|english)\s*=")
# Translation/source attribution.
_TAIL_TMPL = re.compile(r"\{\{Lyrics tail\|(.*?)\}\}", re.DOTALL)
# The == Lyrics == section body, up to the next level-2 heading.
_LYRICS_SECTION = re.compile(r"==\s*Lyrics\s*==(.*?)(?:\n==[^=]|\Z)", re.DOTALL)

_COLUMNS = ("japanese", "romaji", "english")


def _iter_named_templates(text: str, name: str):
    """Yield each full ``{{name …}}`` block (brace-matched, nesting-aware)."""
    marker = "{{" + name
    n, i = len(text), 0
    while True:
        start = text.find(marker, i)
        if start == -1:
            return
        depth, j = 0, start
        while j < n:
            two = text[j:j + 2]
            if two == "{{":
                depth += 1
                j += 2
            elif two == "}}":
                depth -= 1
                j += 2
                if depth == 0:
                    break
            else:
                j += 1
        yield text[start:j]
        i = j


def _lyric_calls(column_body: str) -> list[tuple[str, str]]:
    """``[(singer, text)]`` from the {{Lyric}} calls in one column, in order."""
    return [(m.group(1).strip(), m.group(2).strip()) for m in _LYRIC_TMPL.finditer(column_body)]


def _parse_lyrics_line(block: str) -> list[dict]:
    """Zip a single {{Lyrics line}} block's columns into per-singer segments."""
    inner = block[len("{{Lyrics line"):]
    if inner.endswith("}}"):
        inner = inner[:-2]
    parts = _COLUMN_SPLIT.split(inner)  # [pre, name, body, name, body, ...]
    columns: dict[str, list[tuple[str, str]]] = {}
    for idx in range(1, len(parts) - 1, 2):
        columns[parts[idx]] = _lyric_calls(parts[idx + 1])
    width = max((len(v) for v in columns.values()), default=0)
    segments = []
    for k in range(width):
        singer = next(
            (columns[c][k][0] for c in _COLUMNS if c in columns and k < len(columns[c])), ""
        )
        seg = {"singer": singer}
        for c in _COLUMNS:
            seg[c] = columns[c][k][1] if c in columns and k < len(columns[c]) else ""
        segments.append(seg)
    return segments


def _split_versions(section: str) -> dict[str, str]:
    """``{version_name: raw_content}`` from a ``<tabber>`` (or one 'default' when
    there's no tabber)."""
    tabber = re.search(r"<tabber>(.*?)</tabber>", section, re.DOTALL)
    if not tabber:
        return {"default": section}
    versions: dict[str, str] = {}
    for chunk in tabber.group(1).split("|-|"):
        name, sep, content = chunk.partition("=")
        if sep and name.strip():
            versions[name.strip()] = content
    return versions or {"default": section}


def parse_lyrics_wikitext(wikitext: str) -> dict:
    """Parse a Sekaipedia song page into ``{"versions": {name: [segment,…]},
    "attribution": str}``. A *segment* is ``{singer, japanese, romaji, english}``.
    Empty ``versions`` means the page has no parseable lyrics section."""
    sec = _LYRICS_SECTION.search(wikitext)
    if not sec:
        return {"versions": {}, "attribution": ""}
    body = sec.group(1)
    tail = _TAIL_TMPL.search(body)
    versions = {}
    for name, content in _split_versions(body).items():
        lines: list[dict] = []
        for block in _iter_named_templates(content, "Lyrics line"):
            lines.extend(_parse_lyrics_line(block))
        if lines:
            versions[name] = lines
    return {"versions": versions, "attribution": (tail.group(1).strip() if tail else "")}


def choose_version(versions: dict[str, list]) -> str | None:
    """Prefer the most complete lyrics: Full > Game > first available."""
    if not versions:
        return None
    for want in ("Full Version", "Game Version"):
        if want in versions:
            return want
    return next(iter(versions))


def lyrics_to_text(lines: list[dict], columns: tuple[str, ...] = ("japanese", "english")) -> str:
    """Flatten segments to a plain block for an LLM prompt (default JP + EN)."""
    out = []
    for seg in lines:
        cols = " / ".join(seg[c] for c in columns if seg.get(c))
        out.append(f"[{seg['singer']}] {cols}" if seg.get("singer") else cols)
    return "\n".join(out)


def load_page_map(path: str | Path = "lyric_page_map.json") -> dict:
    """The ``{str(music_id): {pageid, title}}`` map written by ``build-lyric-map``."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return data.get("map", data) if isinstance(data, dict) else {}


def _fetch_page_wikitext(pageid: int) -> str:
    params = urllib.parse.urlencode(
        {"action": "parse", "pageid": pageid, "prop": "wikitext", "format": "json"}
    )
    data = fetch_json(f"{SEKAIPEDIA_API}?{params}")
    return (((data or {}).get("parse") or {}).get("wikitext") or {}).get("*", "")


def fetch_lyrics(music_id: int, page_map: dict) -> dict | None:
    """Fetch + parse lyrics for a song by its master-DB id, via the page map.

    Returns ``{music_id, pageid, title, version, attribution, lines}`` or ``None``
    when the song isn't mapped or the page has no parseable lyrics. Fetches live;
    the raw lyric text is returned to the caller but NEVER persisted here."""
    entry = page_map.get(str(music_id))
    if not entry:
        return None
    wikitext = _fetch_page_wikitext(entry["pageid"])
    parsed = parse_lyrics_wikitext(wikitext)
    version = choose_version(parsed["versions"])
    if version is None:
        return None
    return {
        "music_id": music_id,
        "pageid": entry["pageid"],
        "title": entry.get("title", ""),
        "version": version,
        "attribution": parsed["attribution"],
        "lines": parsed["versions"][version],
    }
