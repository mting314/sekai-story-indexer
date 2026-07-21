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

from .constants import ASSET_CDN, EN_ASSET_CDN, MASTER_DB

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


# --- official English asset CDN (best-effort; EN lags JP) --------------------
# These return {} instead of raising when a scene isn't localized yet, so callers
# can quietly fall back to the JP source of truth.

def en_event_scenario(asset_bundle: str, scenario_id: str) -> dict:
    """Official-EN event scenario asset (same layout as JP). Returns {} when the
    scene isn't localized on the EN CDN yet."""
    url = f"{EN_ASSET_CDN}/event_story/{asset_bundle}/scenario/{scenario_id}.asset"
    try:
        return fetch_json(url)
    except Exception:
        return {}


def en_unit_story_scenario(chapter_asset_bundle: str, scenario_id: str) -> dict:
    """Official-EN unit-story scenario asset. Returns {} when not yet localized."""
    url = f"{EN_ASSET_CDN}/scenario/unitstory/{chapter_asset_bundle}/{scenario_id}.asset"
    try:
        return fetch_json(url)
    except Exception:
        return {}


def game_character_units() -> list[dict]:
    """gameCharacterUnits.json: id -> gameCharacterId (a character can belong to
    several units, so this maps a character-unit id back to the character)."""
    return fetch_json(f"{MASTER_DB}/gameCharacterUnits.json")


# Other-region master DBs. Same event IDs as JP, but each region releases the
# event on its own schedule — this is what sekai.best's per-server "Event Period"
# block shows. JP is the primary MASTER_DB.
REGION_DBS = {
    "en": "https://sekai-world.github.io/sekai-master-db-en-diff",
    "tw": "https://sekai-world.github.io/sekai-master-db-tc-diff",
    "kr": "https://sekai-world.github.io/sekai-master-db-kr-diff",
}


def en_event_names() -> dict[int, str]:
    """Official English event titles from the EN master DB, keyed by event id.
    Only events localized so far are present (EN lags JP)."""
    try:
        rows = fetch_json(f"{REGION_DBS['en']}/events.json")
    except Exception:
        return {}
    return {e["id"]: e.get("name", "") for e in rows if e.get("id") and e.get("name")}


def en_music_titles() -> dict[int, str]:
    """Official English song titles from the EN master DB, keyed by music id."""
    try:
        rows = fetch_json(f"{REGION_DBS['en']}/musics.json")
    except Exception:
        return {}
    return {m["id"]: m.get("title", "") for m in rows if m.get("id") and m.get("title")}


def region_event_times() -> dict[int, dict[str, dict[str, int]]]:
    """Per-region event windows keyed by event id:
    ``{event_id: {"en": {"start": ms, "end": ms}, "tw": {...}, "kr": {...}}}``.

    JP is intentionally omitted (it's already on each catalog record). A region
    that can't be fetched is skipped, never fatal."""
    out: dict[int, dict[str, dict[str, int]]] = {}
    for region, base in REGION_DBS.items():
        try:
            rows = fetch_json(f"{base}/events.json")
        except Exception:  # region down / not yet published -> skip that server
            continue
        for e in rows:
            eid = e.get("id")
            if eid is None:
                continue
            out.setdefault(eid, {})[region] = {
                "start": e.get("startAt", 0),
                "end": e.get("aggregateAt", 0),
            }
    return out


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

    # Authoritative focus character = the event's banner character
    # (eventStories.bannerGameCharacterUnitId -> gameCharacterUnits -> characterId).
    # Absent (~5 events) means no single focus (crossover/anniversary).
    banner_char_by_event: dict[int, int] = {}
    try:
        gcu = {u["id"]: u["gameCharacterId"] for u in game_character_units()}
        for s in stories:
            b = s.get("bannerGameCharacterUnitId")
            if b and b in gcu:
                banner_char_by_event[s["eventId"]] = gcu[b]
    except Exception:  # pragma: no cover - optional/offline
        banner_char_by_event = {}

    return {
        "events": events_list,
        "stories_by_event": stories_by_event,
        "story_units_by_story_id": story_units_by_story_id,
        "banner_char_by_event": banner_char_by_event,
        "event_card_ids": event_card_ids,
        "cards_by_id": cards_by_id,
        "music_by_event": music_by_event,
    }
