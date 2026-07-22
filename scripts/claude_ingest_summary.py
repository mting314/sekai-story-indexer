"""Ingest a Claude-session-written event summary into summaries_cache.json.

Validates the required sections, then stores it in the same structure the app reads,
marked as claude-session-generated (so a future Gemini run can keep it via
`sekai summarize --skip-existing` or upgrade it).

Usage: uv run python scripts/claude_ingest_summary.py <arc-slug> <summary-file>
"""

import json
import sys
from pathlib import Path

from sekai_story_indexer.indexer.summary_sections import extract_summary_sections

CACHE = "summaries_cache.json"
REQUIRED = ("Overview", "Episode Index", "Character Trajectories")

arc, path = sys.argv[1], sys.argv[2]
summary = Path(path).read_text(encoding="utf-8").strip()

secs = extract_summary_sections(summary)
missing = [s for s in REQUIRED if s not in secs]
if missing:
    print(f"REFUSED: {arc} summary missing required sections: {missing}")
    print(f"parsed sections: {list(secs)}")
    sys.exit(1)

cache = json.loads(Path(CACHE).read_text(encoding="utf-8")) if Path(CACHE).exists() else {}
cache[f"EVENT|{arc}"] = {
    "schema_version": "1",
    "fingerprint": "claude-session",  # honest marker; kept by --skip-existing
    "summary": summary,
    "inputs": {
        "level": "event",
        "chat_model": "claude-opus-4-8",
        "generation_provider": "claude-session",
    },
}
Path(CACHE).write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"wrote EVENT|{arc} ({len(summary)} chars) — sections: {list(secs)}")
