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

from .constants import CHARACTER_ID_TO_UNIT, DB_UNIT_TO_SLUG

_SLUG_STRIP_RE = re.compile(r"[^a-z0-9]+")


def slugify(value: str, *, max_len: int = 60) -> str:
    """ASCII slug for filesystem paths. Non-ASCII (e.g. Japanese) is dropped;
    if nothing survives, returns an empty string (callers fall back to an id)."""
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
