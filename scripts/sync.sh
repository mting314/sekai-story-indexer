#!/usr/bin/env bash
# Incremental refresh of the RAG index. Run on a schedule (cron / CI) — the game
# ships a new event ~every 15 days, so a daily run is plenty and no-ops when
# nothing new landed.
#
#   fetch  — idempotent; re-writes the story tree + events_index.json from the
#            source. Existing episodes overwrite identically; new events appear.
#   ingest — manifest-gated (content-hashed), so ONLY new/changed episodes are
#            re-summarized and re-embedded. Unchanged story stays as-is.
#
# The timeline endpoint stays fresh on its own (live+cached from the source);
# this job is what makes new events chat-answerable.
#
# Usage:  scripts/sync.sh            # full incremental sync
#         SEKAI_FETCH_ARGS="--limit 5" scripts/sync.sh   # smoke test
set -euo pipefail
cd "$(dirname "$0")/.."

echo "[sync] $(date -u +%FT%TZ) fetching story tree from source..."
uv run indexer fetch ${SEKAI_FETCH_ARGS:-}

echo "[sync] ingesting (incremental; manifest skips unchanged)..."
uv run indexer ingest

echo "[sync] done."
