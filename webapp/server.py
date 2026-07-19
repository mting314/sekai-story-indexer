"""Lightweight web app for the Sekai story indexer.

A small FastAPI backend that serves:
  * GET  /                -> the single-page chat + timeline UI
  * GET  /api/units       -> unit slugs + display names
  * GET  /api/events      -> timeline data from events_index.json (image URLs,
                             unit, nickname, focus character, commissioned song)
  * POST /api/query       -> ask the RAG engine {question, unit?}

The timeline works from events_index.json alone (no model needed). The chat
endpoint lazily loads the query engine and degrades to a clear message if the
index/keys aren't set up yet, so the timeline stays usable regardless.

Run:  uv run uvicorn webapp.server:app --reload   (see webapp/README.md)
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from sekai_story_indexer.source.constants import UNIT_NAMES, UNIT_SLUGS

HERE = Path(__file__).parent
STATIC = HERE / "static"


def _events_index_path() -> Path:
    env = os.environ.get("SEKAI_EVENTS_INDEX")
    if env:
        return Path(env)
    for candidate in (Path("events_index.json"), HERE.parent / "events_index.json"):
        if candidate.exists():
            return candidate
    return Path("events_index.json")


def load_events() -> list[dict]:
    path = _events_index_path()
    if not path.exists():
        return []
    rows = json.loads(path.read_text(encoding="utf-8"))
    rows.sort(key=lambda r: (r.get("started_at", 0), r.get("event_id", 0)))
    return rows


app = FastAPI(title="Sekai Story Indexer")


@app.get("/api/units")
def units() -> list[dict]:
    return [{"slug": s, "name": UNIT_NAMES.get(s, s)} for s in UNIT_SLUGS]


@app.get("/api/events")
def events() -> list[dict]:
    return load_events()


class QueryRequest(BaseModel):
    question: str
    unit: str | None = None
    event_id: int | None = None


@app.post("/api/query")
def query(req: QueryRequest) -> dict:
    """Answer via the RAG engine. Degrades gracefully if it isn't set up."""
    try:
        from sekai_story_indexer.database import initialize_query_settings
        from sekai_story_indexer.query.engine import RetrievalConfig, StoryQueryEngine
    except Exception as exc:  # pragma: no cover - optional heavy deps
        return {"answer": None, "error": f"query engine unavailable: {exc}"}

    try:
        initialize_query_settings()
        engine = StoryQueryEngine(retrieval_config=RetrievalConfig())
        # NOTE: unit/event scoping (req.unit / req.event_id) becomes a metadata
        # filter once Phase 4 wiring lands; today the engine answers globally.
        answer = engine.query(req.question)
        return {"answer": answer, "error": None}
    except Exception as exc:  # pragma: no cover - runtime/config
        return {
            "answer": None,
            "error": (
                f"{exc}. The index may not be built yet — run `indexer fetch` then "
                "`indexer ingest`, and set GOOGLE_API_KEY."
            ),
        }


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


if STATIC.exists():
    app.mount("/static", StaticFiles(directory=STATIC), name="static")
