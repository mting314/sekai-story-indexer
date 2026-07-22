"""Dump one uncached event's scenes + glossary for a Claude-session summary.

Usage: uv run python scripts/claude_prep.py <arc-slug>
       uv run python scripts/claude_prep.py --list   # list missing event arcs
"""

import glob
import json
import os
import re
import sys

CACHE = "summaries_cache.json"


def missing_arcs():
    cache = json.load(open(CACHE, encoding="utf-8")) if os.path.exists(CACHE) else {}
    cached = {k[6:] for k in cache if k.startswith("EVENT|")}
    arcs = sorted(
        os.path.basename(d)
        for d in glob.glob("story/*/event/*")
        if os.path.isdir(d) and re.match(r"\d{4}-", os.path.basename(d))
    )
    return [a for a in arcs if a not in cached]


if sys.argv[1:] == ["--list"]:
    m = missing_arcs()
    print(f"{len(m)} missing:")
    print("\n".join(m))
    sys.exit(0)

arc = sys.argv[1]
# glob "*.md" matches only the JP scene files (".md.en" sidecars end in ".en").
files = sorted(glob.glob(f"story/*/event/{arc}/*.md"))
if not files:
    print(f"no scenes on disk for {arc}")
    sys.exit(1)
# Prefer the official-EN localization sidecar when present (1:1-aligned with JP),
# so summaries use canonical English names instead of hand-romanized readings.
# Falls back to JP per-episode for events the EN server hasn't reached yet.
en_episodes = 0
for f in files:
    num = re.match(r"(\d+)", os.path.basename(f))
    en = f + ".en"
    src_file, lang = (en, "EN") if os.path.exists(en) else (f, "JP")
    if lang == "EN":
        en_episodes += 1
    print(f"\n===== Episode {int(num.group(1)) if num else '?'} ({os.path.basename(src_file)}) [{lang}] =====")
    print(open(src_file, encoding="utf-8").read().strip())
print(f"\n# transcript source: {en_episodes}/{len(files)} episodes in official EN")

g = json.load(open("glossary.json", encoding="utf-8")) if os.path.exists("glossary.json") else {}
print("\n===== GLOSSARY — use these exact English names =====")
for section in ("characters", "side_characters", "units", "locations_and_terms"):
    for jp, en in (g.get(section) or {}).items():
        print(f"{jp} -> {en}")
