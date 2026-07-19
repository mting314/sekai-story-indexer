"""Thin network client for the Sekai master DB and asset CDN.

Adapted from the ``autosub`` project's ``fetch_event.py``. Kept deliberately
small and dependency-free (stdlib ``urllib``) and isolated from the pure
transforms so the rest of the pipeline stays testable without network access.

Note: these hosts are external (``sekai-world.github.io`` / ``storage.sekai.best``)
and may be blocked in restricted environments; run ingestion where egress to
them is permitted.
"""

from __future__ import annotations

import http.client
import json
import ssl
import time
import urllib.error
import urllib.request
from typing import Any

from .constants import ASSET_CDN, MASTER_DB

# Transient network faults worth retrying (incl. partial reads from the CDN).
_RETRYABLE = (urllib.error.URLError, TimeoutError, http.client.IncompleteRead, ConnectionError, OSError)

_UA = {"User-Agent": "sekai-story-indexer/0.1 (+fetch)"}


def _ssl_context() -> ssl.SSLContext:
    """Verified TLS context. Prefer certifi's CA bundle when available — many
    freshly-created venvs (e.g. uv on macOS) have no system CA bundle wired up,
    which otherwise fails with CERTIFICATE_VERIFY_FAILED. Falls back to the
    stdlib default (honouring SSL_CERT_FILE) when certifi isn't installed."""
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:  # pragma: no cover - certifi optional
        return ssl.create_default_context()


_SSL_CONTEXT = _ssl_context()


def fetch_json(url: str, *, retries: int = 3, backoff: float = 1.5) -> Any:
    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=_UA)
            with urllib.request.urlopen(req, timeout=30, context=_SSL_CONTEXT) as resp:
                return json.loads(resp.read())
        except _RETRYABLE as exc:  # pragma: no cover - network
            last_exc = exc
            if attempt < retries - 1:
                time.sleep(backoff ** attempt)
    raise RuntimeError(f"failed to fetch {url}: {last_exc}")


# --- master DB tables -------------------------------------------------------

def events() -> list[dict]:
    return fetch_json(f"{MASTER_DB}/events.json")


def event_stories() -> list[dict]:
    return fetch_json(f"{MASTER_DB}/eventStories.json")


def event_story_units() -> list[dict]:
    """eventStoryUnits.json: authoritative event -> unit + relation ("main"/"sub").
    Basis for both unit resolution and the native "key story" signal."""
    return fetch_json(f"{MASTER_DB}/eventStoryUnits.json")


def unit_stories() -> list[dict]:
    return fetch_json(f"{MASTER_DB}/unitStories.json")


def game_characters() -> list[dict]:
    return fetch_json(f"{MASTER_DB}/gameCharacters.json")


def event_musics() -> list[dict]:
    return fetch_json(f"{MASTER_DB}/eventMusics.json")


def musics() -> list[dict]:
    return fetch_json(f"{MASTER_DB}/musics.json")


def event_cards() -> list[dict]:
    """eventCards.json: eventId -> cardId, for focus-character resolution."""
    return fetch_json(f"{MASTER_DB}/eventCards.json")


def cards() -> list[dict]:
    """cards.json: id -> {characterId, cardRarityType, releaseAt, ...}."""
    return fetch_json(f"{MASTER_DB}/cards.json")


# --- asset CDN --------------------------------------------------------------

def event_scenario(asset_bundle: str, scenario_id: str) -> dict:
    url = f"{ASSET_CDN}/event_story/{asset_bundle}/scenario/{scenario_id}.asset"
    return fetch_json(url)


def unit_story_scenario(chapter_asset_bundle: str, scenario_id: str) -> dict:
    """Unit-story scenario asset. Path keyed by the CHAPTER's assetbundleName."""
    url = f"{ASSET_CDN}/scenario/unitstory/{chapter_asset_bundle}/{scenario_id}.asset"
    return fetch_json(url)


# --- grouped bundle ---------------------------------------------------------

def load_catalog_tables() -> dict:
    """Fetch + group every master table the enriched catalog needs, in the shape
    ``catalog.build_catalog`` expects. Shared by the fetcher and the web app so
    both derive the timeline identically. Optional tables degrade to empty."""
    events_list = events()
    stories = event_stories()
    stories_by_event = {s["eventId"]: s for s in stories}

    story_units_by_story_id: dict[int, list[dict]] = {}
    try:
        for row in event_story_units():
            story_units_by_story_id.setdefault(row["eventStoryId"], []).append(row)
    except Exception:  # pragma: no cover - optional/offline
        story_units_by_story_id = {}

    cards_by_id: dict[int, dict] = {}
    event_card_ids: dict[int, list[int]] = {}
    try:
        cards_by_id = {c["id"]: c for c in cards()}
        for row in event_cards():
            event_card_ids.setdefault(row["eventId"], []).append(row["cardId"])
    except Exception:  # pragma: no cover - optional/offline
        cards_by_id, event_card_ids = {}, {}

    music_by_event: dict[int, dict] = {}
    try:
        musics_by_id = {m["id"]: m for m in musics()}
        for row in event_musics():
            if row["eventId"] not in music_by_event and row["musicId"] in musics_by_id:
                music_by_event[row["eventId"]] = musics_by_id[row["musicId"]]
    except Exception:  # pragma: no cover - optional/offline
        music_by_event = {}

    return {
        "events": events_list,
        "stories_by_event": stories_by_event,
        "story_units_by_story_id": story_units_by_story_id,
        "event_card_ids": event_card_ids,
        "cards_by_id": cards_by_id,
        "music_by_event": music_by_event,
    }
