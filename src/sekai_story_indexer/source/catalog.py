"""Enriched event catalog — the timeline data model, built from master-DB tables.

One place that turns raw master-DB tables into enriched timeline records (unit,
key-story, focus character, commissioned song, CDN image URLs, community
nickname). Used by BOTH:

  * the fetcher, to write ``events_index.json`` at ingest time, and
  * the web app's ``/api/events``, to serve a live, cached view straight from the
    source API — so the timeline stays current between ingests.

Because both paths call ``build_catalog``, the on-disk index and the live API are
identical by construction.
"""

from __future__ import annotations

from .assets import event_banner_url, event_logo_url, music_jacket_url
from .constants import CHARACTER_ID_TO_JP
from .nicknames import assign_focus_nicknames
from .relevance import classify_event
from .transform import (
    arc_slug,
    is_key_event_story,
    resolve_unit_from_story_units,
    song_info,
)


def event_record(
    event: dict,
    story: dict | None,
    *,
    story_units: list[dict],
    focus_id: int,
    music: dict | None,
) -> dict:
    """Enriched timeline record for one event (no nickname yet — that needs the
    full set for per-character numbering; added by :func:`build_catalog`).

    ``focus_id`` is the event's banner character (0 = no single focus, e.g.
    crossover/anniversary events) — the authoritative signal for focus/nickname.
    """
    event_id = event["id"]
    name = event.get("name", str(event_id))
    asset_bundle = (story or {}).get("assetbundleName", "") or event.get("assetbundleName", "")
    unit = resolve_unit_from_story_units(story_units) if story_units else "mixed"
    song = song_info(music)
    episodes = story.get("eventStoryEpisodes", []) if story else []
    return {
        "event_id": event_id,
        "name": name,
        "unit": unit,
        "event_type": event.get("eventType", ""),
        "content_type": "event",
        "arc_slug": arc_slug(event_id, name),
        "started_at": event.get("startAt", 0),
        "outline": (story or {}).get("outline", ""),
        "is_key_story": is_key_event_story(story_units),
        "plot_weight": "unrated",
        "focus_character_id": focus_id,
        "focus_character": CHARACTER_ID_TO_JP.get(focus_id, ""),
        "has_story": bool(story),
        "episodes": len(episodes),
        **song,
        "logo_url": event_logo_url(asset_bundle) if asset_bundle else "",
        "banner_url": event_banner_url(asset_bundle) if asset_bundle else "",
        "jacket_url": (
            music_jacket_url(song["song_assetbundle"]) if song.get("song_assetbundle") else ""
        ),
    }


def build_catalog(
    events: list[dict],
    *,
    stories_by_event: dict[int, dict],
    story_units_by_story_id: dict[int, list[dict]],
    music_by_event: dict[int, dict],
    banner_char_by_event: dict[int, int] | None = None,
    **_ignored: object,  # tolerate extra table keys (event_card_ids, cards_by_id)
) -> list[dict]:
    """Full enriched, chronologically-sorted catalog with nicknames assigned.

    Focus character comes from the event's banner (``banner_char_by_event``);
    events without a banner get no focus/nickname (crossover/anniversary).
    """
    banner_char_by_event = banner_char_by_event or {}
    records: list[dict] = []
    for event in events:
        story = stories_by_event.get(event["id"])
        story_units = (
            story_units_by_story_id.get(story["id"], []) if story else []
        )
        records.append(
            event_record(
                event,
                story,
                story_units=story_units,
                focus_id=banner_char_by_event.get(event["id"], 0),
                music=music_by_event.get(event["id"]),
            )
        )

    nicknames = assign_focus_nicknames(
        [
            {
                "event_id": r["event_id"],
                "focus_character_id": r["focus_character_id"],
                "started_at": r["started_at"],
            }
            for r in records
        ]
    )
    for r in records:
        nn = nicknames.get(r["event_id"]) or {}
        r["nickname"] = nn.get("nickname")
        r["focus_index"] = nn.get("focus_index")
        r["plot_weight"] = classify_event(r)  # our relevance verdict (heuristic)

    _assign_world_links(records)
    records.sort(key=lambda r: (r.get("started_at", 0), r["event_id"]))
    return records


# A World Link (world_bloom) campaign spans several separate events ("parts")
# released over ~a season; a new campaign starts after a long gap. Number them
# "World Link N Part M" and expose a `wlN-M` alias for scoping.
_WORLD_LINK_GAP_MS = 120 * 24 * 3600 * 1000  # 120 days


def _assign_world_links(records: list[dict]) -> None:
    wl = sorted(
        (r for r in records if r.get("event_type") == "world_bloom"),
        key=lambda r: (r.get("started_at", 0), r["event_id"]),
    )
    series = part = 0
    prev_at: int | None = None
    for r in wl:
        at = r.get("started_at", 0)
        if prev_at is None or at - prev_at > _WORLD_LINK_GAP_MS:
            series += 1
            part = 1
        else:
            part += 1
        prev_at = at
        r["world_link_series"] = series
        r["world_link_part"] = part
        r["world_link_label"] = f"World Link {series} Part {part}"
        r["wl_alias"] = f"wl{series}-{part}"
        # banner-less World Link parts get the wl alias as their nickname
        if not r.get("nickname"):
            r["nickname"] = r["wl_alias"]
