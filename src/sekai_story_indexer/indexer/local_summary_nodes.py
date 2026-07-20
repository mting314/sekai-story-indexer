"""Turn the locally-generated summary caches into embeddable StoryNodes.

Lets the ``full`` (Google/Chroma) ingest embed the FREE local summaries produced
by ``scripts/summarize_ollama.py`` instead of re-summarizing via a paid API
(``indexer ingest --summaries local``). We map our tiers onto the inherited
summary-level machinery:

    Unit summary    -> level 1 (top / "year" tier)   id: summary:year:<unit>
    Event summary   -> level 2 ("episode" tier)       id: summary:episode:<arc>
    Episode summary -> level 3 ("part" tier)          id: summary:part:<arc>:<ep>

Inline ``{char_id=N}`` tags are stripped from the embedded text (they help the UI
color names but only add noise to a vector).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ..models.story import StoryMetadata, StoryNode

_TAG_RE = re.compile(r"\{char_id=\d+\}")


def _load(name: str, story_root: Path) -> dict:
    for p in (Path(name), story_root.parent / name):
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                return {}
    return {}


def _text_of(value) -> str:
    s = value if isinstance(value, str) else (value or {}).get("summary", "")
    return _TAG_RE.sub("", s).strip()


def _arc_units(story_root: Path) -> dict[str, str]:
    """arc_slug -> unit, derived from the story tree (story/<unit>/event/<arc>)."""
    out: dict[str, str] = {}
    for d in story_root.glob("*/event/*"):
        if d.is_dir():
            out[d.name] = d.parts[-3]
    return out


def _meta(**kw) -> StoryMetadata:
    base = dict(
        arc_id="",
        episode_name="",
        part_name="",
        file_path="",
        story_type="Event",  # local summaries are all derived from event stories
        is_prose=True,
        scene_start=0,
        scene_end=0,
    )
    base.update(kw)
    return StoryMetadata(**base)


def load_local_summary_nodes(story_root: Path) -> list[StoryNode]:
    episodes = _load("episode_summaries.json", story_root)
    events = _load("event_summaries.json", story_root)
    units = _load("unit_summaries.json", story_root)
    raw_index = _load("events_index.json", story_root)
    index: dict[str, Any] = (
        {r.get("arc_slug"): r for r in raw_index if isinstance(r, dict)}
        if isinstance(raw_index, list)
        else {}
    )
    arc_unit = _arc_units(story_root)

    # chronological rank per arc (for story_order) when the index is available
    order_rank = {
        a: i for i, a in enumerate(
            sorted(index, key=lambda a: (index[a].get("started_at", 0), a)), start=1
        )
    }

    def unit_of(arc: str) -> str:
        entry = index.get(arc)
        if isinstance(entry, dict):
            unit_val = entry.get("unit")
            if unit_val:
                return str(unit_val)
        return str(arc_unit.get(arc, "mixed"))

    nodes: list[StoryNode] = []

    # Tier 3 (Part): per-episode
    for arc, eps in episodes.items():
        unit, rec = unit_of(arc), index.get(arc, {})
        for ep_key, val in eps.items():
            text = _text_of(val)
            if not text:
                continue
            nodes.append(StoryNode(
                text=text, summary_level=3,
                metadata=_meta(
                    unit=unit, arc_id=arc, event_id=rec.get("event_id", 0),
                    started_at=rec.get("started_at", 0),
                    episode_name=str(ep_key), part_name=str(ep_key),
                    file_path=f"<summary:episode:{arc}:{ep_key}>",
                    parent_part_id=f"{arc}:{ep_key}", parent_episode_id=arc, parent_year_id=unit,
                    story_order=order_rank.get(arc, 0), canonical_story_order=order_rank.get(arc, 0),
                ),
            ))

    # Tier 2 (Episode): per-event
    for arc, val in events.items():
        text = _text_of(val)
        if not text:
            continue
        unit, rec = unit_of(arc), index.get(arc, {})
        nodes.append(StoryNode(
            text=text, summary_level=2,
            metadata=_meta(
                unit=unit, arc_id=arc, event_id=rec.get("event_id", 0),
                started_at=rec.get("started_at", 0),
                episode_name="(event summary)", part_name="(event summary)",
                file_path=f"<summary:event:{arc}>",
                parent_episode_id=arc, parent_year_id=unit,
                story_order=order_rank.get(arc, 0), canonical_story_order=order_rank.get(arc, 0),
            ),
        ))

    # Tier 1 (Year): per-unit
    for unit, val in units.items():
        text = _text_of(val)
        if not text:
            continue
        nodes.append(StoryNode(
            text=text, summary_level=1,
            metadata=_meta(
                unit=unit, arc_id=f"unit:{unit}",
                episode_name="(unit summary)", part_name="(unit summary)",
                file_path=f"<summary:unit:{unit}>",
                parent_year_id=unit,
            ),
        ))

    return nodes
