"""Fixed summary section labels + a pure section extractor.

Kept dependency-free (no model/database imports) so the summary reader and the
web app can parse summaries without pulling the generation stack (chromadb,
google-genai, …). ``summarizer`` re-exports these for back-compat.
"""

from __future__ import annotations

PART_SUMMARY_SECTIONS = (
    "Overview",
    "Key Events",
    "Character Developments",
    "Continuity Facts",
    "Important Terms",
)
EPISODE_SUMMARY_SECTIONS = (
    "Overview",
    "Part Index",
    "Episode Arc",
    "Character Developments",
    "Relationship / Unit Developments",
    "Continuity Facts",
    "Important Terms",
)
EVENT_SUMMARY_SECTIONS = (
    "Overview",
    "Episode Index",
    "Character Trajectories",
    "Unit / Club State",
    "Continuity Facts",
    "Important Terms",
)
SUMMARY_SECTIONS_BY_LEVEL = {
    "Part": PART_SUMMARY_SECTIONS,
    "Episode": EPISODE_SUMMARY_SECTIONS,
    "Event": EVENT_SUMMARY_SECTIONS,
}
KNOWN_SUMMARY_SECTIONS = frozenset(
    PART_SUMMARY_SECTIONS + EPISODE_SUMMARY_SECTIONS + EVENT_SUMMARY_SECTIONS
)


def extract_summary_sections(summary: str) -> dict[str, str]:
    """Extract known fixed-label summary sections from generated Markdown."""
    sections: dict[str, list[str]] = {}
    current_label: str | None = None

    for line in summary.splitlines():
        stripped = line.strip()
        if stripped.endswith(":") and stripped[:-1] in KNOWN_SUMMARY_SECTIONS:
            current_label = stripped[:-1]
            sections.setdefault(current_label, [])
            continue

        if current_label is not None:
            sections[current_label].append(line)

    return {label: "\n".join(lines).strip() for label, lines in sections.items()}
