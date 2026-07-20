"""Run the hierarchical summarizer on ONE event, persist into summaries_cache.json,
and print the output. Run: uv run python scratch_one_event.py <arc_slug>
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

for line in Path(".env").read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

from sekai_story_indexer.cli import _assign_canonical_story_order  # noqa: E402
from sekai_story_indexer.indexer.manifest import (  # noqa: E402
    SUMMARY_CACHE_SCHEMA_VERSION,
    SummaryCacheContext,
    hash_files,
)
from sekai_story_indexer.indexer.parser import PARSER_VERSION  # noqa: E402
from sekai_story_indexer.indexer.processor import StoryProcessor  # noqa: E402
from sekai_story_indexer.indexer.summarizer import (  # noqa: E402
    SUMMARIZATION_PROMPT_VERSION,
    HierarchicalSummarizer,
)
from sekai_story_indexer.story_order import load_story_order  # noqa: E402

slug = sys.argv[1] if len(sys.argv) > 1 else "0097-light-up-the-fire"
CACHE = "summaries_cache.json"  # persist so the in-app view can read it

event_dir = next(Path("story").glob(f"*/event/{slug}"))
md_files = sorted(event_dir.glob("*.md"))
raw_nodes = [n for p in md_files for n in StoryProcessor.process_file(p)]
story_order = load_story_order(story_root=Path("story"))
_assign_canonical_story_order(raw_nodes, story_order=story_order)

glossary = json.loads(Path("glossary.json").read_text()) if Path("glossary.json").exists() else None
ctx = SummaryCacheContext(
    source_file_hashes=hash_files(md_files),
    parser_version=PARSER_VERSION,
    summarization_prompt_version=SUMMARIZATION_PROMPT_VERSION,
    glossary_hash="",
    chat_model="sample",
    generation_provider="google",
    generation_model="gemini-flash-latest",
    embedding_model="sample",
    summary_cache_schema_version=SUMMARY_CACHE_SCHEMA_VERSION,
)

print(f"Summarizing {slug} ({len(md_files)} episodes) ...\n")
summarizer = HierarchicalSummarizer(glossary=glossary, story_order=story_order, cache_context=ctx)
nodes = summarizer.summarize_hierarchy(raw_nodes, cache_file=CACHE)

for level, label in [(1, "EVENT (Tier-1)"), (2, "EPISODE (Tier-2)"), (3, "PART (Tier-3)")]:
    for n in nodes:
        if n.summary_level != level:
            continue
        m = n.metadata
        print(f"\n{'='*74}\n### [{label}] {m.arc_id}  ep={m.episode_name!r}\n{'='*74}")
        print(n.text.strip())
