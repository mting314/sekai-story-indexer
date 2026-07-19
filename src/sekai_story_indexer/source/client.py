"""Thin network client for the Sekai master DB and asset CDN.

Adapted from the ``autosub`` project's ``fetch_event.py``. Kept deliberately
small and dependency-free (stdlib ``urllib``) and isolated from the pure
transforms so the rest of the pipeline stays testable without network access.

Note: these hosts are external (``sekai-world.github.io`` / ``storage.sekai.best``)
and may be blocked in restricted environments; run ingestion where egress to
them is permitted.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any

from .constants import ASSET_CDN, MASTER_DB

_UA = {"User-Agent": "sekai-story-indexer/0.1 (+fetch)"}


def fetch_json(url: str, *, retries: int = 3, backoff: float = 1.5) -> Any:
    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=_UA)
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read())
        except (urllib.error.URLError, TimeoutError) as exc:  # pragma: no cover - network
            last_exc = exc
            if attempt < retries - 1:
                time.sleep(backoff ** attempt)
    raise RuntimeError(f"failed to fetch {url}: {last_exc}")


# --- master DB tables -------------------------------------------------------

def events() -> list[dict]:
    return fetch_json(f"{MASTER_DB}/events.json")


def event_stories() -> list[dict]:
    return fetch_json(f"{MASTER_DB}/eventStories.json")


def unit_stories() -> list[dict]:
    return fetch_json(f"{MASTER_DB}/unitStories.json")


def game_characters() -> list[dict]:
    return fetch_json(f"{MASTER_DB}/gameCharacters.json")


def event_musics() -> list[dict]:
    return fetch_json(f"{MASTER_DB}/eventMusics.json")


def musics() -> list[dict]:
    return fetch_json(f"{MASTER_DB}/musics.json")


# --- asset CDN --------------------------------------------------------------

def event_scenario(asset_bundle: str, scenario_id: str) -> dict:
    url = f"{ASSET_CDN}/event_story/{asset_bundle}/scenario/{scenario_id}.asset"
    return fetch_json(url)
