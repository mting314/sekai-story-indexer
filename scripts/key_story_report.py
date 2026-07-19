#!/usr/bin/env python3
"""Report which event stories are 'key' (native isKeyEventStory), grouped by unit.

Two modes:

  # after `indexer fetch` has written events_index.json (offline, instant):
  python scripts/key_story_report.py --index story/../events_index.json

  # or pull straight from the live master DB (needs egress to sekai-world):
  python scripts/key_story_report.py --live

'Key' means the event has a main-relation unit in eventStoryUnits.json — the same
overinclusive rule sekai.best uses. This is the native prior, NOT our own
plot_weight verdict.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from sekai_story_indexer.source.constants import UNIT_NAMES, UNIT_SLUGS
from sekai_story_indexer.source.transform import is_key_event_story, resolve_unit_from_story_units


def _date(ts_ms: int) -> str:
    if not ts_ms:
        return "????-??-??"
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def rows_from_index(index_path: Path) -> list[dict]:
    data = json.loads(Path(index_path).read_text(encoding="utf-8"))
    return [r for r in data if r.get("is_key_story")]


def rows_from_live() -> list[dict]:
    from sekai_story_indexer.source import client

    events = {e["id"]: e for e in client.events()}
    stories = client.event_stories()
    units_by_story: dict[int, list[dict]] = {}
    for row in client.event_story_units():
        units_by_story.setdefault(row["eventStoryId"], []).append(row)

    out: list[dict] = []
    for story in stories:
        su = units_by_story.get(story["id"], [])
        if not is_key_event_story(su):
            continue
        ev = events.get(story["eventId"], {})
        out.append(
            {
                "event_id": story["eventId"],
                "name": ev.get("name", str(story["eventId"])),
                "unit": resolve_unit_from_story_units(su),
                "started_at": ev.get("startAt", 0),
            }
        )
    return out


def render(rows: list[dict]) -> str:
    by_unit: dict[str, list[dict]] = {u: [] for u in UNIT_SLUGS}
    for r in rows:
        by_unit.setdefault(r.get("unit", "mixed"), []).append(r)

    lines: list[str] = []
    for unit in UNIT_SLUGS:
        items = sorted(by_unit.get(unit, []), key=lambda r: (r.get("started_at", 0), r["event_id"]))
        if not items:
            continue
        lines.append(f"\n## {UNIT_NAMES.get(unit, unit)}  ({len(items)} key stories)")
        lines.append(f"{'date':<12} {'id':>4}  name")
        for r in items:
            lines.append(f"{_date(r.get('started_at', 0)):<12} {r['event_id']:>4}  {r['name']}")
    return "\n".join(lines) if lines else "(no key stories found)"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--index", type=Path, help="path to events_index.json from `indexer fetch`")
    g.add_argument("--live", action="store_true", help="pull from the live master DB")
    args = ap.parse_args()

    rows = rows_from_live() if args.live else rows_from_index(args.index)
    print(render(rows))


if __name__ == "__main__":
    main()
