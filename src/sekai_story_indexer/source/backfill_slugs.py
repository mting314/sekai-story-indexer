"""Backfill utility to update story tree directories, episode filenames, events_index.json,
and story_order.yaml with Japanese-romanized slugs.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path

import yaml

from .transform import arc_slug, episode_filename

_H1_TITLE_RE = re.compile(r"^#\s*(\d+)\.\s*(.*)$")


def _remap_cache_keys(path: Path, slug_map: dict[str, str], log: Callable[[str], None]) -> int:
    """Remap the top-level arc_slug keys of a summary cache ({arc_slug: value}) via
    slug_map, so caches survive a slug rename. Returns the number of keys changed."""
    if not path.exists():
        return 0
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return 0
    if not isinstance(data, dict):
        return 0
    changed = 0
    remapped: dict = {}
    for key, value in data.items():
        new_key = slug_map.get(key, key)
        if new_key != key:
            changed += 1
        remapped[new_key] = value
    if changed:
        path.write_text(json.dumps(remapped, ensure_ascii=False, indent=2), encoding="utf-8")
        log(f"Remapped {changed} keys in {path}")
    return changed


def backfill_story_tree(
    story_root: Path = Path("story"),
    events_index_path: Path = Path("events_index.json"),
    story_order_path: Path = Path("story_order.yaml"),
    summary_cache_paths: tuple[Path, ...] = (
        Path("event_summaries.json"),
        Path("episode_summaries.json"),
    ),
    log: Callable[[str], None] = print,
) -> dict[str, int]:
    """Scans events_index.json and story_root, renames directories and episode files
    using the current slugify() implementation, and updates index/order/summary files.
    """
    story_root = Path(story_root)
    events_index_path = Path(events_index_path)
    story_order_path = Path(story_order_path)

    stats = {
        "events_updated": 0,
        "dirs_renamed": 0,
        "files_renamed": 0,
        "summaries_remapped": 0,
    }

    # 1. Update events_index.json
    slug_map: dict[str, str] = {}  # old_arc_slug -> new_arc_slug
    if events_index_path.exists():
        catalog = json.loads(events_index_path.read_text(encoding="utf-8"))
        for rec in catalog:
            old_slug = rec.get("arc_slug", "")
            event_id = rec.get("event_id")
            name = rec.get("name", "")
            if event_id is not None and name:
                new_slug = arc_slug(event_id, name)
                rec["arc_slug"] = new_slug
                if old_slug:
                    slug_map[old_slug] = new_slug
                if old_slug != new_slug:
                    stats["events_updated"] += 1

        events_index_path.write_text(
            json.dumps(catalog, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        log(f"Updated {events_index_path} ({stats['events_updated']} arc_slugs changed)")

    # 2. Rename directories under story_root
    if story_root.exists():
        for unit_dir in story_root.iterdir():
            if not unit_dir.is_dir():
                continue
            for ctype_dir in unit_dir.iterdir():
                if not ctype_dir.is_dir():
                    continue
                for arc_dir in list(ctype_dir.iterdir()):
                    if not arc_dir.is_dir():
                        continue
                    old_dir_name = arc_dir.name
                    target_dir_name = slug_map.get(old_dir_name, old_dir_name)

                    if target_dir_name == old_dir_name and old_dir_name.isdigit():
                        eid = int(old_dir_name)
                        for old_s, new_s in slug_map.items():
                            if old_s == f"{eid:04d}":
                                target_dir_name = new_s
                                break

                    final_arc_dir = arc_dir
                    if target_dir_name != old_dir_name:
                        new_dir = ctype_dir / target_dir_name
                        if not new_dir.exists():
                            arc_dir.rename(new_dir)
                            stats["dirs_renamed"] += 1
                            log(f"Renamed dir: {old_dir_name} -> {target_dir_name}")
                            final_arc_dir = new_dir
                        else:
                            final_arc_dir = new_dir

                    # Rename episode .md files inside final_arc_dir
                    for ep_file in list(final_arc_dir.glob("*.md")):
                        try:
                            lines = ep_file.read_text(encoding="utf-8").splitlines()
                            first_line = lines[0] if lines else ""
                        except Exception:
                            continue
                        m = _H1_TITLE_RE.match(first_line.strip())
                        if m:
                            ep_no = int(m.group(1))
                            ep_title = m.group(2).strip()
                            new_ep_fname = episode_filename(ep_no, ep_title)
                            if new_ep_fname != ep_file.name:
                                new_ep_path = final_arc_dir / new_ep_fname
                                if not new_ep_path.exists():
                                    ep_file.rename(new_ep_path)
                                    stats["files_renamed"] += 1
                                    log(f"Renamed ep file: {ep_file.name} -> {new_ep_fname}")

    # 3. Update story_order.yaml if present
    if story_order_path.exists() and slug_map:
        order_doc = yaml.safe_load(story_order_path.read_text(encoding="utf-8"))
        if isinstance(order_doc, dict):
            for group in order_doc.get("chronological_order", []):
                if isinstance(group, dict) and "arcs" in group:
                    group["arcs"] = [slug_map.get(str(a), str(a)) for a in group["arcs"]]
            for group in order_doc.get("summary_order", []):
                if isinstance(group, dict) and "arcs" in group:
                    group["arcs"] = [slug_map.get(str(a), str(a)) for a in group["arcs"]]

            story_order_path.write_text(
                yaml.safe_dump(order_doc, allow_unicode=True, sort_keys=False),
                encoding="utf-8",
            )
            log(f"Updated {story_order_path}")

    # 4. Remap arc_slug-keyed summary caches (event/episode). unit_summaries.json is
    #    keyed by unit, which doesn't change, so it's intentionally excluded.
    if slug_map:
        for cache_path in summary_cache_paths:
            stats["summaries_remapped"] += _remap_cache_keys(Path(cache_path), slug_map, log)

    return stats
