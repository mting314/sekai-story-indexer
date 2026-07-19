"""Tier-1 unit overviews — the summary tier above events.

The production design (Phase 2 proper) builds these by LLM "Refine" over event
summaries. Offline / no-API, we synthesize a deterministic unit overview from the
per-event ``outline`` synopses already in the events index (chronological, plot
events first). This gives broad "what is Leo/need's overall arc?" queries a
single Tier-1 node to hit instead of fanning out over dozens of scenes.

These are synopsis-level (not full story text), so they're built from all events
regardless of the indexed-only queryable contract that governs scene retrieval.
"""

from __future__ import annotations

from datetime import UTC, datetime

from ..models.story import StoryMetadata, StoryNode
from ..source.constants import UNIT_NAMES

_PLOT_WEIGHTS_FOR_OVERVIEW = {"high", "medium"}


def _date(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, tz=UTC).strftime("%Y-%m-%d") if ts_ms else ""


def build_unit_overviews(events_index: list[dict]) -> list[StoryNode]:
    """One Tier-1 overview StoryNode per unit, from its events' outlines."""
    by_unit: dict[str, list[dict]] = {}
    for row in events_index or []:
        if row.get("outline"):
            by_unit.setdefault(row.get("unit", "unknown"), []).append(row)

    nodes: list[StoryNode] = []
    for unit, rows in by_unit.items():
        rows.sort(key=lambda r: (r.get("started_at", 0), r.get("event_id", 0)))
        lines = [f"Overview of {UNIT_NAMES.get(unit, unit)} — story arc:"]
        for r in rows:
            if r.get("plot_weight") in _PLOT_WEIGHTS_FOR_OVERVIEW:
                nick = f"[{r['nickname']}] " if r.get("nickname") else ""
                lines.append(f"- {_date(r.get('started_at', 0))} {nick}{r.get('name', '')}: {r['outline']}")
        text = "\n".join(lines)
        arc_id = f"__unit__{unit}"
        meta = StoryMetadata(
            unit=unit,
            content_type="unit_overview",
            arc_id=arc_id,
            story_type="Other",
            episode_name="Overview",
            part_name="Overview",
            file_path=f"<generated:{arc_id}>",
            parent_year_id=arc_id,
            parent_episode_id=arc_id,
            parent_part_id=arc_id,
        )
        nodes.append(StoryNode(text=text, metadata=meta, summary_level=1))
    return nodes
