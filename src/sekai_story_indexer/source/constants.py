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
# Official English asset CDN — same layout as the JP CDN, but lags JP and is
# incomplete. Used only to source verbatim EN quotes for already-localized
# scenes; JP stays the source of truth (and the fallback) everywhere else.
EN_ASSET_CDN = "https://storage.sekai.best/sekai-en-assets"

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

# Canonical master-DB ``gameCharacters.json`` id -> Japanese name. Ids are fixed
# game constants (1-20 unit members in unit order, 21-26 Virtual Singers).
CHARACTER_ID_TO_JP: dict[int, str] = {
    1: "星乃一歌", 2: "天馬咲希", 3: "望月穂波", 4: "日野森志歩",
    5: "花里みのり", 6: "桐谷遥", 7: "桃井愛莉", 8: "日野森雫",
    9: "小豆沢こはね", 10: "白石杏", 11: "東雲彰人", 12: "青柳冬弥",
    13: "天馬司", 14: "鳳えむ", 15: "草薙寧々", 16: "神代類",
    17: "宵崎奏", 18: "朝比奈まふゆ", 19: "東雲絵名", 20: "暁山瑞希",
    21: "初音ミク", 22: "鏡音リン", 23: "鏡音レン", 24: "巡音ルカ",
    25: "MEIKO", 26: "KAITO",
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
