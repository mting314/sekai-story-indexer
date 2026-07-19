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
from .assets import event_banner_url, event_logo_url, music_jacket_url
from .constants import CHARACTER_ID_TO_JP
from .nicknames import assign_focus_nicknames
from .transform import (
    arc_slug,
    episode_filename,
    focus_character_id,
    is_key_event_story,
    render_episode_markdown,
    resolve_unit,
    resolve_unit_from_story_units,
    scenario_to_lines,
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

    # Focus character (eventCards -> cards) and commissioned song (eventMusics ->
    # musics). All optional; enrichment degrades gracefully if a table is absent.
    cards_by_id: dict[int, dict] = {}
    event_card_ids: dict[int, list[int]] = {}
    music_by_event: dict[int, dict] = {}
    try:
        cards_by_id = {c["id"]: c for c in client.cards()}
        for row in client.event_cards():
            event_card_ids.setdefault(row["eventId"], []).append(row["cardId"])
    except Exception:  # pragma: no cover - optional table / offline
        cards_by_id, event_card_ids = {}, {}
    try:
        musics_by_id = {m["id"]: m for m in client.musics()}
        for row in client.event_musics():
            if row["eventId"] not in music_by_event and row["musicId"] in musics_by_id:
                music_by_event[row["eventId"]] = musics_by_id[row["musicId"]]
    except Exception:  # pragma: no cover - optional table / offline
        music_by_event = {}

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
            event,
            story,
            story_units=story_units_by_story_id.get(story["id"], []),
            event_card_ids=event_card_ids.get(event["id"], []),
            cards_by_id=cards_by_id,
            music=music_by_event.get(event["id"]),
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
    # Assign community nicknames (kasa5, mizu3, …) from focus characters,
    # numbering each character's focus events in release order.
    nicknames = assign_focus_nicknames(
        [
            {
                "event_id": p.event_id,
                "focus_character_id": p.focus_character_id,
                "started_at": p.started_at,
            }
            for p in plans
        ]
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
                    "focus_character_id": p.focus_character_id,
                    "focus_character": p.focus_character,
                    "nickname": (nicknames.get(p.event_id) or {}).get("nickname"),
                    "focus_index": (nicknames.get(p.event_id) or {}).get("focus_index"),
                    **p.song,
                    "logo_url": event_logo_url(p.asset_bundle) if p.asset_bundle else "",
                    "banner_url": event_banner_url(p.asset_bundle) if p.asset_bundle else "",
                    "jacket_url": (
                        music_jacket_url(p.song["song_assetbundle"])
                        if p.song.get("song_assetbundle")
                        else ""
                    ),
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
