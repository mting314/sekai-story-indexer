#!/usr/bin/env bash
# Self-resuming full ingest.
#
# `indexer ingest` writes summaries_cache.json incrementally, but the inherited
# summarizer does NOT retry transient network errors (httpx ReadError, etc.), so
# a single blip over a multi-hour run aborts it. This loop just re-runs ingest —
# each restart resumes from the cache (cached summaries are skipped by content
# fingerprint), so it grinds through blips until the run completes.
#
# Requires GOOGLE_API_KEY in .env and a funded (paid-tier) project.
# Usage:  scripts/ingest_resume.sh [story-dir]
#
# TODO (proper fix): wrap the generation calls in indexer/summarizer.py with a
# bounded retry/backoff on transient errors so a plain `indexer ingest` is
# self-healing without this loop.
set -u
cd "$(dirname "$0")/.."
STORY_DIR="${1:-story}"
MODEL="${SEKAI_INGEST_MODEL:-gemini-flash-latest}"

ingest_done=0
for i in $(seq 1 400); do
  echo "===== ingest attempt $i $(date -u +%FT%TZ) ====="
  SEKAI_INGEST_MODEL="$MODEL" SEKAI_CHAT_MODEL="$MODEL" \
    .venv/bin/python -u -m sekai_story_indexer.cli ingest --story-dir "$STORY_DIR" \
    && { echo "INGEST COMPLETE"; ingest_done=1; break; }
  echo "attempt $i ended non-zero; resuming from cache in 8s..."
  sleep 8
done
[ "$ingest_done" = 1 ] || { echo "hit attempt cap without completing"; exit 1; }

# Build the State Ledger (world_state.json) so the full backend + RAG-lite
# generation are ledger-grounded. Same self-resuming pattern.
for i in $(seq 1 400); do
  echo "===== extract-state attempt $i $(date -u +%FT%TZ) ====="
  SEKAI_INGEST_MODEL="$MODEL" SEKAI_CHAT_MODEL="$MODEL" \
    .venv/bin/python -u -m sekai_story_indexer.cli extract-state \
    && { echo "STATE LEDGER COMPLETE"; exit 0; }
  echo "attempt $i ended non-zero; resuming in 8s..."
  sleep 8
done
echo "state extraction hit attempt cap"; exit 1
