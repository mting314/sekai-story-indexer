"""Materialize the Project Sekai story tree from the master DB + asset CDN.

Split into a pure planning layer (``plan_event`` / ``story_order_doc``) that is
unit-tested against fixtures, and an I/O layer (``fetch_and_write``) that does
the network + disk work.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from . import client
from .transform import (
    arc_slug,
    episode_filename,
    is_key_event_story,
    render_episode_markdown,
    resolve_unit,
    resolve_unit_from_story_units,
    scenario_to_lines,
    tree_relpath,
)


def story_type_for(content_type: str) -> str:
    """Map a Sekai content bucket onto the tier machinery's {Main, Side, Other}."""
    return "Main" if content_type == "main" else "Side"


@dataclass
class EpisodePlan:
    relpath: str          # path under story_root
    title: str            # human title written as the file's H1
    scenario_id: str      # asset scenario id to fetch
    asset_bundle: str     # event_story asset bundle name


@dataclass
class EventPlan:
    event_id: int
    name: str
    unit: str
    content_type: str
    arc_slug: str
    started_at: int
    outline: str
    is_key_story: bool = False
    episodes: list[EpisodePlan] = field(default_factory=list)


def plan_event(
    event: dict,
    story: dict,
    *,
    content_type: str = "event",
    story_units: list[dict] | None = None,
    event_characters: dict[int, list[int]] | None = None,
) -> EventPlan:
    """Pure: turn one event + its eventStories record into a write plan.

    Unit resolution precedence: authoritative ``eventStoryUnits`` (main
    relation) -> explicit event ``unit`` field / featured characters -> mixed.
    ``story_units`` are the eventStoryUnits rows for this event's story; they
    also determine the native ``is_key_story`` signal.
    """
    event_id = event["id"]
    name = event.get("name", str(event_id))
    story_units = story_units or []
    if story_units:
        unit = resolve_unit_from_story_units(story_units)
    else:
        char_ids = (event_characters or {}).get(event_id)
        unit = resolve_unit(db_unit=event.get("unit"), character_ids=char_ids)
    is_key = is_key_event_story(story_units)
    slug = arc_slug(event_id, name)
    asset_bundle = story["assetbundleName"]

    episodes: list[EpisodePlan] = []
    for ep in sorted(story.get("eventStoryEpisodes", []), key=lambda e: e["episodeNo"]):
        fname = episode_filename(ep["episodeNo"], ep.get("title", ""))
        episodes.append(
            EpisodePlan(
                relpath=tree_relpath(unit, content_type, slug, fname),
                title=f"{ep['episodeNo']}. {ep.get('title', '')}".strip(),
                scenario_id=ep["scenarioId"],
                asset_bundle=asset_bundle,
            )
        )

    return EventPlan(
        event_id=event_id,
        name=name,
        unit=unit,
        content_type=content_type,
        arc_slug=slug,
        started_at=event.get("startAt", 0),
        outline=story.get("outline", ""),
        is_key_story=is_key,
        episodes=episodes,
    )


def story_order_doc(plans: list[EventPlan]) -> dict:
    """Build a ``story_order.yaml`` document from event plans.

    Arcs are grouped by story_type and listed in chronological (release) order,
    so ``StoryOrder`` positions align with in-universe timeline.
    """
    ordered = sorted(plans, key=lambda p: (p.started_at, p.event_id))
    groups: dict[str, list[str]] = {}
    for plan in ordered:
        st = story_type_for(plan.content_type)
        groups.setdefault(st, []).append(plan.arc_slug)

    order_list = [{"story_type": st, "arcs": arcs} for st, arcs in groups.items()]
    return {
        "chronological_order": order_list,
        "summary_order": order_list,
        "part_order_overrides": [],
    }


def fetch_and_write(
    story_root: Path,
    *,
    limit: int | None = None,
    event_ids: list[int] | None = None,
    scenario_fetch: Callable[[str, str], dict] | None = None,
    log: Callable[[str], None] = print,
) -> list[EventPlan]:
    """Fetch events + scenarios and write the story tree + story_order.yaml.

    Returns the list of realized :class:`EventPlan`. ``scenario_fetch`` is
    injectable for testing; defaults to the live CDN client.
    """
    story_root = Path(story_root)
    fetch_scenario = scenario_fetch or client.event_scenario

    events = client.events()
    stories = client.event_stories()
    stories_by_event = {s["eventId"]: s for s in stories}
    # eventStoryUnits gives authoritative unit + the native key-story signal,
    # grouped by eventStoryId. Optional/offline -> fall back per event.
    story_units_by_story_id: dict[int, list[dict]] = {}
    try:
        for row in client.event_story_units():
            story_units_by_story_id.setdefault(row["eventStoryId"], []).append(row)
    except Exception:  # pragma: no cover - optional table / offline
        story_units_by_story_id = {}

    selected = [e for e in events if not event_ids or e["id"] in event_ids]
    selected.sort(key=lambda e: (e.get("startAt", 0), e["id"]))
    if limit:
        selected = selected[:limit]

    plans: list[EventPlan] = []
    for event in selected:
        story = stories_by_event.get(event["id"])
        if not story:
            log(f"skip event {event['id']} ({event.get('name')}): no eventStories record")
            continue
        plan = plan_event(
            event, story, story_units=story_units_by_story_id.get(story["id"], [])
        )
        for ep in plan.episodes:
            scenario = fetch_scenario(ep.asset_bundle, ep.scenario_id)
            lines = scenario_to_lines(scenario)
            markdown = render_episode_markdown(ep.title, [lines])
            out_path = story_root / ep.relpath
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(markdown, encoding="utf-8")
        log(f"wrote {plan.unit}/{plan.arc_slug} ({len(plan.episodes)} episodes)")
        plans.append(plan)

    # emit ordering + an events index manifest alongside the story root
    order_path = story_root.parent / "story_order.yaml"
    order_path.write_text(
        yaml.safe_dump(story_order_doc(plans), allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    index_path = story_root.parent / "events_index.json"
    index_path.write_text(
        json.dumps(
            [
                {
                    "event_id": p.event_id,
                    "name": p.name,
                    "unit": p.unit,
                    "content_type": p.content_type,
                    "arc_slug": p.arc_slug,
                    "started_at": p.started_at,
                    "outline": p.outline,
                    "is_key_story": p.is_key_story,
                    "plot_weight": "unrated",
                    "episodes": len(p.episodes),
                }
                for p in plans
            ],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    log(f"wrote {order_path} and {index_path} ({len(plans)} events)")
    return plans
