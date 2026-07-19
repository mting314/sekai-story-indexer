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
from .transform import (
    arc_slug,
    focus_character_id,
    is_key_event_story,
    resolve_unit_from_story_units,
    song_info,
)


def event_record(
    event: dict,
    story: dict | None,
    *,
    story_units: list[dict],
    event_card_ids: list[int],
    cards_by_id: dict[int, dict],
    music: dict | None,
) -> dict:
    """Enriched timeline record for one event (no nickname yet — that needs the
    full set for per-character numbering; added by :func:`build_catalog`)."""
    event_id = event["id"]
    name = event.get("name", str(event_id))
    asset_bundle = (story or {}).get("assetbundleName", "") or event.get("assetbundleName", "")
    unit = resolve_unit_from_story_units(story_units) if story_units else "mixed"
    focus_id = focus_character_id(event_card_ids, cards_by_id)
    song = song_info(music)
    episodes = story.get("eventStoryEpisodes", []) if story else []
    return {
        "event_id": event_id,
        "name": name,
        "unit": unit,
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
    event_card_ids: dict[int, list[int]],
    cards_by_id: dict[int, dict],
    music_by_event: dict[int, dict],
) -> list[dict]:
    """Full enriched, chronologically-sorted catalog with nicknames assigned."""
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
                event_card_ids=event_card_ids.get(event["id"], []),
                cards_by_id=cards_by_id,
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

    records.sort(key=lambda r: (r.get("started_at", 0), r["event_id"]))
    return records
