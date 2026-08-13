"""Song resonance data loader and validator.

Loads curated lyric-to-quote mappings from ``song_resonance.json``, enabling
both RAG context enrichment and REST/UI displays with transcript deep links.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

DEFAULT_RESONANCE_PATH = "song_resonance.json"


def load_song_resonance(path: str | Path = DEFAULT_RESONANCE_PATH) -> dict[str, dict[str, Any]]:
    """Load song resonance mappings keyed by arc_slug."""
    path_obj = Path(path)
    if not path_obj.exists():
        candidate = Path(__file__).resolve().parents[3] / path
        if candidate.exists():
            path_obj = candidate
        else:
            return {}

    try:
        content = path_obj.read_text(encoding="utf-8")
        data = json.loads(content)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


def get_resonance_for_event(arc_slug: str, path: str | Path = DEFAULT_RESONANCE_PATH) -> dict[str, Any] | None:
    """Retrieve resonance data for a given arc_slug."""
    all_data = load_song_resonance(path)
    return all_data.get(arc_slug)


def validate_resonance_entry(entry: dict[str, Any]) -> bool:
    """Check whether a single song resonance entry satisfies schema requirements."""
    required_keys = {"event_id", "song_title", "unit", "resonance_mappings"}
    if not required_keys.issubset(entry.keys()):
        return False

    mappings = entry.get("resonance_mappings")
    if not isinstance(mappings, list):
        return False

    for item in mappings:
        if not isinstance(item, dict):
            return False
        mapping_keys = {
            "lyric_jp",
            "lyric_en",
            "story_episode",
            "line_range",
            "speaker",
            "story_quote_jp",
            "story_quote_en",
            "resonance_commentary",
        }
        if not mapping_keys.issubset(item.keys()):
            return False

    return True
