"""Pure transforms that turn Sekai master-DB / CDN payloads into the on-disk
story tree the indexer's :class:`StoryProcessor` expects.

Everything here is network-free so it can be unit tested against small fixtures.

Canonical story tree layout (what :func:`tree_relpath` produces and what
``StoryProcessor.extract_hierarchy`` reads back):

    story/<unit>/<content_type>/<arc_slug>/<NN_episode-slug>.md

Scenes inside each episode file are separated by the ``---`` delimiter, and
script lines are written ``speaker: text`` — identical to the upstream linkura
format, so the parser / chunker / summarizer machinery is reused unchanged.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

from .constants import CHARACTER_ID_TO_UNIT, DB_UNIT_TO_SLUG


def en_sidecar_path(jp_path: Path) -> Path:
    """Co-located official-EN sidecar for a JP episode: ``foo.md`` → ``foo.md.en``.
    The trailing ``.en`` (not ``.en.md``) keeps it off every ``*.md`` glob, so the
    EN text is never indexed as JP story content."""
    return jp_path.with_name(jp_path.name + ".en")

_KAKASI = None
_SLUG_STRIP_RE = re.compile(r"[^a-z0-9]+")


def _get_kakasi():
    global _KAKASI
    if _KAKASI is None:
        import pykakasi

        _KAKASI = pykakasi.kakasi()
    return _KAKASI


def slugify(value: str, *, max_len: int = 60) -> str:
    """ASCII slug for filesystem paths. Japanese (Kana/Kanji) is romanized into
    Hepburn Romaji; remaining non-ASCII characters are stripped."""
    if any(ord(c) > 127 for c in value):
        try:
            k = _get_kakasi()
            converted = k.convert(value)
            value = " ".join(item.get("hepburn", "") for item in converted)
        except Exception:
            pass
    normalized = unicodedata.normalize("NFKD", value)
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii").lower()
    slug = _SLUG_STRIP_RE.sub("-", ascii_only).strip("-")
    return slug[:max_len].strip("-")


def arc_slug(event_id: int, name: str) -> str:
    """Zero-padded, name-tagged slug for an event 'volume'. Zero padding keeps
    chronological filesystem ordering aligned with release order."""
    tag = slugify(name)
    return f"{event_id:04d}-{tag}" if tag else f"{event_id:04d}"


def episode_filename(episode_no: int, title: str) -> str:
    """``05_the-title.md`` — number-prefixed so files sort in reading order."""
    tag = slugify(title)
    return f"{episode_no:02d}_{tag}.md" if tag else f"{episode_no:02d}.md"


# Canonical map from a Sekai content bucket to the tier machinery's story-type
# axis. Sekai has no "side story" (that was a linkura Main/Side concept), so each
# bucket reads as itself and unclassified paths fall back to a neutral "Other".
# Shared by the fetcher (writes story_order.yaml) and the processor (reads the
# tree) so the two never drift.
_CONTENT_STORY_TYPE = {"event": "Event", "unit": "Unit", "card": "Card", "area": "Area"}


def story_type_for(content_type: str) -> str:
    if content_type == "main" or content_type.startswith("第"):
        return "Main"
    return _CONTENT_STORY_TYPE.get(content_type, "Other")


def tree_relpath(unit: str, content_type: str, arc_slug_: str, episode_file: str) -> str:
    """Path under ``story/`` for one episode file."""
    return f"{unit}/{content_type}/{arc_slug_}/{episode_file}"


def resolve_unit(
    *,
    db_unit: str | None = None,
    character_ids: list[int] | None = None,
) -> str:
    """Best-effort unit slug for an event.

    Order of precedence:
      1. an explicit master-DB unit string, mapped via ``DB_UNIT_TO_SLUG``
      2. the majority unit among featured character ids (Virtual Singers ignored
         as tie-breakers so a VS-supported unit event still resolves to the unit)
      3. ``"mixed"`` when nothing resolves or featured chars span >1 real unit
    """
    if db_unit:
        mapped = DB_UNIT_TO_SLUG.get(db_unit.strip().lower())
        if mapped:
            return mapped

    if character_ids:
        counts: dict[str, int] = {}
        for cid in character_ids:
            unit = CHARACTER_ID_TO_UNIT.get(cid)
            if unit and unit != "virtual_singer":
                counts[unit] = counts.get(unit, 0) + 1
        if len(counts) == 1:
            return next(iter(counts))
        if len(counts) > 1:
            return "mixed"
        # only virtual singers featured
        if any(CHARACTER_ID_TO_UNIT.get(cid) == "virtual_singer" for cid in character_ids):
            return "virtual_singer"

    return "mixed"


def resolve_unit_from_story_units(story_units: list[dict]) -> str:
    """Authoritative unit slug from ``eventStoryUnits`` rows for one event.

    Prefers the ``main``-relation unit(s); a single main unit wins, multiple
    main units (crossover) -> ``mixed``. Falls back to any present unit, else
    ``mixed``. Each row: ``{unit, eventStoryUnitRelation}``.
    """
    main = {
        u
        for r in story_units
        if r.get("eventStoryUnitRelation") == "main"
        if (u := DB_UNIT_TO_SLUG.get((r.get("unit") or "").lower())) is not None
    }
    if len(main) == 1:
        val = next(iter(main))
        if val is not None:
            return val
    if len(main) > 1:
        return "mixed"
    other = {
        u
        for r in story_units
        if (u := DB_UNIT_TO_SLUG.get((r.get("unit") or "").lower())) is not None
    }
    if len(other) == 1:
        val = next(iter(other))
        if val is not None:
            return val
    return "mixed"


def is_key_event_story(story_units: list[dict]) -> bool:
    """Native "key story" signal: any story-unit row with relation ``main``.

    This is sekai.best's ``isKeyEventStory`` rule. It is deliberately
    overinclusive (most unit events qualify), so it is stored as an input
    prior only — our own ``plot_weight`` classifier remains the final say.
    """
    return any(r.get("eventStoryUnitRelation") == "main" for r in story_units)


def focus_character_id(event_card_ids: list[int], cards_by_id: dict[int, dict]) -> int:
    """The event's focus character = character of its featured limited 4★ card.

    Among the event's cards, prefer ``rarity_4`` (and birthday) cards and take the
    earliest-released one as the debut/limited focus. Returns 0 if unresolvable.
    This is what the community nickname system counts.
    """
    cards = [cards_by_id[cid] for cid in event_card_ids if cid in cards_by_id]
    if not cards:
        return 0
    featured = [c for c in cards if c.get("cardRarityType") in ("rarity_4", "rarity_birthday")]
    pool = featured or cards
    pool.sort(key=lambda c: c.get("releaseAt", 0))
    return pool[0].get("characterId", 0)


def build_card_parent_map(
    event_cards: list[dict],
    cards_by_id: dict[int, dict],
) -> dict[int, dict]:
    """Resolve each card's hierarchy parent, for nesting card side-stories under
    their event.

    The authoritative link is ``eventCards.json`` (``cardId`` -> ``eventId``). A
    card reused across events (re-run / anniversary) is assigned its PRIMARY event:
    rows flagged ``isDisplayCardStory`` win, then the earliest ``eventId``. Cards
    with no ``eventCards`` row have no event parent and fall back to their
    character, tagged ``"birthday"`` (``rarity_birthday``) or ``"other"``
    (permanent / initial / gacha).

    Returns ``{card_id: {"kind", "event_id", "character_id", "is_display_card_story"}}``
    for every card in ``cards_by_id`` (callers filter to cards that actually have
    side-stories). ``kind`` is ``"event" | "birthday" | "other"``; ``event_id`` is
    ``None`` for the non-event kinds.
    """
    rows_by_card: dict[int, list[dict]] = {}
    for row in event_cards:
        cid = row.get("cardId")
        if cid is not None:
            rows_by_card.setdefault(cid, []).append(row)

    out: dict[int, dict] = {}
    for cid, rows in rows_by_card.items():
        displayed = [r for r in rows if r.get("isDisplayCardStory")]
        # Primary event: prefer a story-displaying row, then the earliest event id.
        primary = min(displayed or rows, key=lambda r: r.get("eventId", 1 << 30))
        out[cid] = {
            "kind": "event",
            "event_id": primary.get("eventId"),
            "character_id": (cards_by_id.get(cid) or {}).get("characterId", 0),
            "is_display_card_story": bool(displayed),
        }

    for cid, card in cards_by_id.items():
        if cid in out:
            continue
        rarity = card.get("cardRarityType", "")
        out[cid] = {
            "kind": "birthday" if rarity == "rarity_birthday" else "other",
            "event_id": None,
            "character_id": card.get("characterId", 0),
            "is_display_card_story": False,
        }
    return out


def song_info(music: dict | None) -> dict:
    """Flatten a ``musics.json`` record into commissioned-song fields."""
    if not music:
        return {}
    return {
        "song_id": music.get("id", 0),
        "song_title": music.get("title", ""),
        "song_composer": music.get("composer", ""),
        "song_lyricist": music.get("lyricist", ""),
        "song_arranger": music.get("arranger", ""),
        "song_assetbundle": music.get("assetbundleName", ""),
    }


def scenario_to_lines(scenario: dict) -> list[tuple[str, str]]:
    """Extract ordered ``(speaker, text)`` tuples from a scenario ``.asset``.

    ``TalkData`` carries the spoken dialogue. ``WindowDisplayName`` is the
    speaker label (may be blank for narration). Newlines inside ``Body`` are
    collapsed to spaces so one turn is one line in the emitted markdown.
    """
    lines: list[tuple[str, str]] = []
    for talk in scenario.get("TalkData", []):
        body = (talk.get("Body") or "").replace("\n", " ").strip()
        if not body:
            continue
        speaker = (talk.get("WindowDisplayName") or "").strip()
        lines.append((speaker, body))
    return lines


def align_en_to_jp(
    jp_lines: list[tuple[str, str]], en_scenario: dict
) -> list[tuple[str, str]] | None:
    """Align official-EN scene lines to the JP lines by ``TalkData`` index.

    Both regions share the same scenario structure, so an equal line count means a
    safe 1:1 index alignment. Returns the EN ``(speaker, text)`` list when it lines
    up with the JP, else ``None`` (EN missing / partial / structurally different →
    caller keeps the JP source of truth). We compare counts, not speakers, since EN
    speaker labels are localized ("Honami" vs "穂波").
    """
    if not en_scenario or not jp_lines:
        return None
    en_lines = scenario_to_lines(en_scenario)
    if not en_lines or len(en_lines) != len(jp_lines):
        return None
    return en_lines


def render_episode_markdown(
    title: str,
    scene_lines: list[list[tuple[str, str]]],
) -> str:
    """Render one episode file. ``scene_lines`` is a list of scenes, each a list
    of ``(speaker, text)`` turns; scenes are joined with the ``---`` delimiter."""
    blocks: list[str] = []
    for scene in scene_lines:
        rendered = []
        for speaker, text in scene:
            rendered.append(f"{speaker}: {text}" if speaker else text)
        if rendered:
            blocks.append("\n".join(rendered))
    header = f"# {title}\n\n" if title else ""
    return header + "\n\n---\n\n".join(blocks) + "\n"
