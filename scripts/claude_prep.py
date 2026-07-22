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
files = sorted(f for f in glob.glob(f"story/*/event/{arc}/*.md") if not f.endswith(".md.en"))
if not files:
    print(f"no scenes on disk for {arc}")
    sys.exit(1)
for f in files:
    num = re.match(r"(\d+)", os.path.basename(f))
    print(f"\n===== Episode {int(num.group(1)) if num else '?'} ({os.path.basename(f)}) =====")
    print(open(f, encoding="utf-8").read().strip())

g = json.load(open("glossary.json", encoding="utf-8")) if os.path.exists("glossary.json") else {}
print("\n===== GLOSSARY — use these exact English names =====")
for section in ("characters", "units", "locations_and_terms"):
    for jp, en in (g.get(section) or {}).items():
        print(f"{jp} -> {en}")
