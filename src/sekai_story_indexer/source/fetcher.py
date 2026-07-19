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
from .constants import CHARACTER_ID_TO_UNIT
from .transform import (
    arc_slug,
    episode_filename,
    render_episode_markdown,
    resolve_unit,
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
    episodes: list[EpisodePlan] = field(default_factory=list)


def plan_event(
    event: dict,
    story: dict,
    *,
    content_type: str = "event",
    event_characters: dict[int, list[int]] | None = None,
) -> EventPlan:
    """Pure: turn one event + its eventStories record into a write plan.

    ``event_characters`` maps event_id -> featured character ids, used to resolve
    the unit when the event carries no explicit unit field.
    """
    event_id = event["id"]
    name = event.get("name", str(event_id))
    char_ids = (event_characters or {}).get(event_id)
    unit = resolve_unit(db_unit=event.get("unit"), character_ids=char_ids)
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


def _event_characters(event_stories_deck: list[dict]) -> dict[int, list[int]]:
    """Map event_id -> featured character ids from eventDeckBonuses / eventCards.
    Best-effort; unknown ids simply don't contribute to unit resolution."""
    out: dict[int, list[int]] = {}
    for row in event_stories_deck:
        ev = row.get("eventId")
        cid = row.get("gameCharacterId") or row.get("characterId")
        if ev and cid in CHARACTER_ID_TO_UNIT:
            out.setdefault(ev, []).append(cid)
    return out


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
    stories_by_event = {s["eventId"]: s for s in client.event_stories()}
    # Featured-character -> unit resolution is best-effort; when the eventCards
    # table is unavailable, resolve_unit() falls back to the db unit or "mixed".
    try:
        deck = client.fetch_json(f"{client.MASTER_DB}/eventCards.json")
        char_map = _event_characters(deck)
    except Exception:  # pragma: no cover - optional table / offline
        char_map = {}

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
        plan = plan_event(event, story, event_characters=char_map)
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
