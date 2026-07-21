"""Official-EN quote lookup: JP source line → verbatim official English line.

Built from the ``foo.md`` / ``foo.md.en`` sidecar pairs the fetcher writes
(aligned 1:1 by line). Lets the query layer surface authentic English quotes
instead of the LLM's paraphrase, falling back to the JP source of truth for
scenes not yet localized on the EN CDN.
"""

from __future__ import annotations

from pathlib import Path


def _content_lines(text: str) -> list[str]:
    """Dialogue/narration lines only — drop the H1 title, scene ``---``, blanks."""
    return [
        s
        for s in (ln.strip() for ln in text.splitlines())
        if s and s != "---" and not s.startswith("#")
    ]


def load_official_en(story_root: str | Path) -> dict[str, str]:
    """Map each JP transcript line → its official EN line, from ``*.md.en`` sidecars.

    A pair whose line counts have drifted (e.g. JP re-fetched after EN) is skipped
    so we never mis-align a quote — JP stays the fallback there.
    """
    root = Path(story_root)
    mapping: dict[str, str] = {}
    for en_path in root.rglob("*.md.en"):
        jp_path = en_path.with_name(en_path.name[:-3])  # "foo.md.en" -> "foo.md"
        if not jp_path.exists():
            continue
        jp_lines = _content_lines(jp_path.read_text(encoding="utf-8"))
        en_lines = _content_lines(en_path.read_text(encoding="utf-8"))
        if len(jp_lines) != len(en_lines):
            continue
        for jp, en in zip(jp_lines, en_lines):
            mapping.setdefault(jp, en)
    return mapping
