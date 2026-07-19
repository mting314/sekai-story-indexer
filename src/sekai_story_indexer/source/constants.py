"""Project Sekai data-source constants and unit taxonomy.

The story text and structure come from the Sekai-World / sekai.best ecosystem —
the same source the ``autosub`` project's ``fetch_event.py`` startup script uses:

* Master DB (metadata): https://sekai-world.github.io/sekai-master-db-diff
* Asset CDN (text):      https://storage.sekai.best/sekai-jp-assets

JP is treated as the source of truth (most complete / current). EN/other-locale
CDNs exist (``sekai-en-assets`` etc.) but lag JP and are incomplete; translation
is handled downstream by the RAG layer with the glossary + State Ledger.
"""

from __future__ import annotations

MASTER_DB = "https://sekai-world.github.io/sekai-master-db-diff"
ASSET_CDN = "https://storage.sekai.best/sekai-jp-assets"

# Canonical unit slugs. These are the Tier-1 grouping for the index and the
# value of ``--unit`` query scoping.
UNIT_SLUGS: tuple[str, ...] = (
    "leo_need",
    "more_more_jump",
    "vivid_bad_squad",
    "wonderlands_showtime",
    "nightcord",
    "virtual_singer",
    "mixed",  # crossover / multi-unit / World Link events
)

# Human-readable unit names keyed by slug.
UNIT_NAMES: dict[str, str] = {
    "leo_need": "Leo/need",
    "more_more_jump": "MORE MORE JUMP!",
    "vivid_bad_squad": "Vivid BAD SQUAD",
    "wonderlands_showtime": "Wonderlands x Showtime",
    "nightcord": "Nightcord at 25:00",
    "virtual_singer": "Virtual Singer",
    "mixed": "Mixed / Crossover",
}

# Master-DB ``gameCharacters.json`` id -> unit slug. The six Virtual Singers
# (21-26) appear across every unit's Sekai but are their own group here.
CHARACTER_ID_TO_UNIT: dict[int, str] = {
    1: "leo_need", 2: "leo_need", 3: "leo_need", 4: "leo_need",
    5: "more_more_jump", 6: "more_more_jump", 7: "more_more_jump", 8: "more_more_jump",
    9: "vivid_bad_squad", 10: "vivid_bad_squad", 11: "vivid_bad_squad", 12: "vivid_bad_squad",
    13: "wonderlands_showtime", 14: "wonderlands_showtime", 15: "wonderlands_showtime", 16: "wonderlands_showtime",
    17: "nightcord", 18: "nightcord", 19: "nightcord", 20: "nightcord",
    21: "virtual_singer", 22: "virtual_singer", 23: "virtual_singer",
    24: "virtual_singer", 25: "virtual_singer", 26: "virtual_singer",
}

# The master-DB ``unit`` string values (as used on gameCharacters / events) that
# map onto our slugs, for sources that carry an explicit unit field.
DB_UNIT_TO_SLUG: dict[str, str] = {
    "light_sound": "leo_need",
    "idol": "more_more_jump",
    "street": "vivid_bad_squad",
    "theme_park": "wonderlands_showtime",
    "school_refusal": "nightcord",
    "piapro": "virtual_singer",
    "none": "mixed",
}

CONTENT_TYPES: tuple[str, ...] = ("main", "event", "unit", "card", "area")

PLOT_WEIGHTS: tuple[str, ...] = ("high", "medium", "filler", "unrated")
