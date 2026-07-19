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
from .catalog import build_catalog
from .constants import CHARACTER_ID_TO_JP, DB_UNIT_TO_SLUG
from .transform import (
    arc_slug,
    episode_filename,
    focus_character_id,
    is_key_event_story,
    render_episode_markdown,
    resolve_unit,
    resolve_unit_from_story_units,
    scenario_to_lines,
    slugify,
    song_info,
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
    asset_bundle: str = ""
    focus_character_id: int = 0
    focus_character: str = ""
    song: dict = field(default_factory=dict)
    episodes: list[EpisodePlan] = field(default_factory=list)


def plan_event(
    event: dict,
    story: dict,
    *,
    content_type: str = "event",
    story_units: list[dict] | None = None,
    event_characters: dict[int, list[int]] | None = None,
    event_card_ids: list[int] | None = None,
    cards_by_id: dict[int, dict] | None = None,
    music: dict | None = None,
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

    focus_id = focus_character_id(event_card_ids or [], cards_by_id or {})
    song = song_info(music)

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
        asset_bundle=asset_bundle,
        focus_character_id=focus_id,
        focus_character=CHARACTER_ID_TO_JP.get(focus_id, ""),
        song=song,
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


def fetch_unit_stories(
    story_root: Path,
    *,
    scenario_fetch: Callable[[str, str], dict] | None = None,
    log: Callable[[str], None] = print,
) -> int:
    """Fetch the units' main (non-event) stories into
    ``story/<unit>/unit/<NN-chapter>/<NN-episode>.md``. Returns episodes written.

    Unit stories are the formation arcs (Phase 5). They're not events, so they
    carry ``content_type='unit'`` and aren't in the events index; the query
    layer treats non-event content as always-queryable once on disk.
    """
    story_root = Path(story_root)
    fetch_scenario = scenario_fetch or client.unit_story_scenario
    written = 0
    for us in client.unit_stories():
        unit = DB_UNIT_TO_SLUG.get((us.get("unit") or "").lower(), "mixed")
        for chapter in us.get("chapters", []):
            ch_no = chapter.get("chapterNo", 0)
            ch_slug = f"{ch_no:02d}-{slugify(chapter.get('title', '')) or 'chapter'}"
            ch_bundle = chapter["assetbundleName"]
            for ep in sorted(chapter.get("episodes", []), key=lambda e: e.get("episodeNo", 0)):
                try:
                    scenario = fetch_scenario(ch_bundle, ep["scenarioId"])
                except Exception as exc:
                    log(f"  skip {unit}/{ch_slug} {ep['scenarioId']}: {exc}")
                    continue
                lines = scenario_to_lines(scenario)
                title = f"{ep.get('episodeNo', 0)}. {ep.get('title', '')}".strip()
                markdown = render_episode_markdown(title, [lines])
                fname = episode_filename(ep.get("episodeNo", 0), ep.get("title", ""))
                out = story_root / tree_relpath(unit, "unit", ch_slug, fname)
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(markdown, encoding="utf-8")
                written += 1
            log(f"wrote {unit}/unit/{ch_slug} ({len(chapter.get('episodes', []))} episodes)")
    log(f"unit stories: {written} episodes")
    return written


def fetch_and_write(
    story_root: Path,
    *,
    limit: int | None = None,
    event_ids: list[int] | None = None,
    scenario_fetch: Callable[[str, str], dict] | None = None,
    skip_existing: bool = False,
    log: Callable[[str], None] = print,
) -> list[EventPlan]:
    """Fetch events + scenarios and write the story tree + story_order.yaml.

    Returns the list of realized :class:`EventPlan`. ``scenario_fetch`` is
    injectable for testing; defaults to the live CDN client.
    """
    story_root = Path(story_root)
    fetch_scenario = scenario_fetch or client.event_scenario

    tables = client.load_catalog_tables()
    events = tables["events"]
    stories_by_event = tables["stories_by_event"]
    story_units_by_story_id = tables["story_units_by_story_id"]
    event_card_ids = tables["event_card_ids"]
    cards_by_id = tables["cards_by_id"]
    music_by_event = tables["music_by_event"]

    selected = [e for e in events if not event_ids or e["id"] in event_ids]
    selected.sort(key=lambda e: (e.get("startAt", 0), e["id"]))
    if limit:
        selected = selected[:limit]

    plans: list[EventPlan] = []
    written_ids: set[int] = set()
    for event in selected:
        story = stories_by_event.get(event["id"])
        if not story:
            log(f"skip event {event['id']} ({event.get('name')}): no eventStories record")
            continue
        plan = plan_event(
            event,
            story,
            story_units=story_units_by_story_id.get(story["id"], []),
            event_card_ids=event_card_ids.get(event["id"], []),
            cards_by_id=cards_by_id,
            music=music_by_event.get(event["id"]),
        )
        ok = 0
        for ep in plan.episodes:
            out_path = story_root / ep.relpath
            if skip_existing and out_path.exists() and out_path.stat().st_size > 0:
                ok += 1  # already fetched — resumable
                continue
            try:
                scenario = fetch_scenario(ep.asset_bundle, ep.scenario_id)
            except Exception as exc:  # one bad episode must not abort the run
                log(f"  skip {plan.arc_slug} ep {ep.scenario_id}: {exc}")
                continue
            lines = scenario_to_lines(scenario)
            markdown = render_episode_markdown(ep.title, [lines])
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(markdown, encoding="utf-8")
            ok += 1
        log(f"wrote {plan.unit}/{plan.arc_slug} ({ok}/{len(plan.episodes)} episodes)")
        plans.append(plan)
        if ok:
            written_ids.add(plan.event_id)

    # emit ordering (covers written trees) + a COMPLETE events index (all events,
    # so the timeline stays complete and nicknames are globally correct even with
    # --limit). Events without written story text carry has_story=False; the
    # `indexed` flag marks which trees this run wrote.
    order_path = story_root.parent / "story_order.yaml"
    order_path.write_text(
        yaml.safe_dump(story_order_doc(plans), allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

    catalog = build_catalog(events, **{k: tables[k] for k in tables if k != "events"})
    for row in catalog:
        row["indexed"] = row["event_id"] in written_ids

    index_path = story_root.parent / "events_index.json"
    index_path.write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    log(f"wrote {order_path} and {index_path} ({len(plans)} trees / {len(catalog)} events)")
    return plans
