#!/usr/bin/env python3
"""Standalone entry point for scheduled ingestion (cron / launchd / systemd / CI).

Thin wrapper around ``sekai_story_indexer.ingest`` so a scheduler doesn't need
the console script on PATH or a shell-quoted flag soup — everything is env-driven
and it always runs from the repo root. Exit code mirrors the run: 0 when every
*required* (Tier 1) step landed, 1 otherwise. Tier 2 (LLM) failures are logged
and never change the exit code — a missing key is a normal Tuesday.

    scripts/run_ingestion.py                # Tier 1 only
    SEKAI_INGEST_WITH_LLM=1 scripts/run_ingestion.py
    SEKAI_INGEST_BATCH_LIMIT=10 SEKAI_INGEST_WITH_LLM=1 scripts/run_ingestion.py

Env knobs (all optional):
    SEKAI_STORY_ROOT        story tree root                   (default: story)
    SEKAI_EVENTS_INDEX      events index path         (default: events_index.json)
    SEKAI_INGEST_WITH_LLM   "1" to run Tier 2                    (default: off)
    SEKAI_INGEST_BATCH_LIMIT  new items per Tier 2 pass            (default: 5)
    SEKAI_INGEST_CARDS/AREAS  "0" to skip those fetches            (default: on)
    SEKAI_RELOAD_URL        POST here after a good run to flush server caches
    SEKAI_ADMIN_TOKEN       sent as X-Admin-Token with that POST
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))


def _flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    return default if raw is None else raw.strip().lower() not in ("", "0", "false", "no")


def main() -> int:
    os.chdir(REPO)  # every default path in IngestConfig is repo-root relative
    from sekai_story_indexer.ingest import IngestConfig, run_ingest

    cfg = IngestConfig(
        story_root=Path(os.environ.get("SEKAI_STORY_ROOT", "story")),
        events_index=Path(os.environ.get("SEKAI_EVENTS_INDEX", "events_index.json")),
        cards=_flag("SEKAI_INGEST_CARDS", True),
        areas=_flag("SEKAI_INGEST_AREAS", True),
        unit_stories=_flag("SEKAI_INGEST_UNIT_STORIES", False),
        with_llm=_flag("SEKAI_INGEST_WITH_LLM", False),
        batch_limit=int(os.environ.get("SEKAI_INGEST_BATCH_LIMIT", "5")),
        model=os.environ.get("SEKAI_INGEST_MODEL", ""),
    )
    report = run_ingest(cfg)
    print(report.summary(), flush=True)

    reload_url = os.environ.get("SEKAI_RELOAD_URL", "")
    if report.ok and reload_url:
        from sekai_story_indexer.localcli import _notify_reload

        print(_notify_reload(reload_url), flush=True)
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
