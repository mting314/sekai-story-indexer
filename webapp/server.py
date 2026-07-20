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
import re
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from sekai_story_indexer.source.constants import UNIT_NAMES, UNIT_SLUGS

HERE = Path(__file__).parent
STATIC = HERE / "static"

# Timeline freshness: the source ships a new event ~every 15 days, so the
# timeline is served LIVE from the master DB (with our enrichment) and cached,
# rather than frozen at last ingest. TTL default 6h; override via env.
EVENTS_TTL_SECONDS = int(os.environ.get("SEKAI_EVENTS_TTL", "21600"))
_cache: dict[str, object] = {"at": 0.0, "rows": None}


def _static_events() -> list[dict]:
    """Fallback: the last on-disk index written by `indexer fetch`."""
    env = os.environ.get("SEKAI_EVENTS_INDEX")
    candidates = [Path(env)] if env else [Path("events_index.json"), HERE.parent / "events_index.json"]
    for path in candidates:
        if path.exists():
            rows = json.loads(path.read_text(encoding="utf-8"))
            rows.sort(key=lambda r: (r.get("started_at", 0), r.get("event_id", 0)))
            return rows
    return []


def _indexed_event_ids() -> set[int]:
    """Which events have story text on disk (chat can answer about them). Derived
    from the last written index's `indexed` flag."""
    return {r["event_id"] for r in _static_events() if r.get("indexed")}


def load_events() -> list[dict]:
    """Live, cached timeline. Pulls the master tables + our enrichment; annotates
    each row `indexed` (has the ingest pipeline embedded it yet?). Falls back to
    the static index when the source is unreachable."""
    now = time.time()
    if _cache["rows"] is not None and now - float(str(_cache["at"])) < EVENTS_TTL_SECONDS:
        return _cache["rows"]  # type: ignore[return-value]

    try:
        from sekai_story_indexer.source import client
        from sekai_story_indexer.source.catalog import build_catalog

        tables = client.load_catalog_tables()
        rows = build_catalog(tables["events"], **{k: tables[k] for k in tables if k != "events"})
        indexed = _indexed_event_ids()
        for r in rows:
            r["indexed"] = r["event_id"] in indexed
    except Exception:
        rows = _static_events()  # offline / source down -> last snapshot

    _cache["rows"] = rows
    _cache["at"] = now
    return rows


app = FastAPI(title="Sekai Story Indexer")


@app.get("/api/units")
def units() -> list[dict]:
    return [{"slug": s, "name": UNIT_NAMES.get(s, s)} for s in UNIT_SLUGS]


@app.get("/api/events")
def events() -> list[dict]:
    return load_events()


# Per-region event windows (JP/EN/TW/KR). Release schedules are static once set,
# so cache for a day. Best-effort: any failure -> no region data, never fatal.
_REGION_TTL_SECONDS = int(os.environ.get("SEKAI_REGION_TTL", "86400"))
_region_cache: dict[str, Any] = {"at": 0.0, "times": None}


def _region_times() -> dict:
    now = time.time()
    if _region_cache["times"] is not None and now - float(str(_region_cache["at"])) < _REGION_TTL_SECONDS:
        return _region_cache["times"]  # type: ignore[return-value]
    try:
        from sekai_story_indexer.source import client

        times = client.region_event_times()
    except Exception:
        times = {}
    _region_cache["times"] = times
    _region_cache["at"] = now
    return times


def _regions_for(event: dict) -> dict:
    """JP window (from the record) plus any EN/TW/KR windows for this event."""
    regions = {}
    if event.get("started_at"):
        regions["jp"] = {"start": event.get("started_at", 0), "end": event.get("ended_at", 0)}
    for region, win in _region_times().get(event.get("event_id"), {}).items():
        if win.get("start"):
            regions[region] = win
    return regions


def _summaries_path() -> Path:
    env = os.environ.get("SEKAI_EVENT_SUMMARIES")
    candidates = [Path(env)] if env else [Path("event_summaries.json"), HERE.parent / "event_summaries.json"]
    return next((p for p in candidates if p.exists()), candidates[-1])


def _tier_path(name: str) -> Path:
    for p in (Path(name), HERE.parent / name):
        if p.exists():
            return p
    return Path(name)


def _entry(value) -> dict:
    """Normalize a summary cache value (bare string or {summary, characters})."""
    if isinstance(value, str):
        return {"summary": value, "characters": []}
    return {"summary": (value or {}).get("summary", ""), "characters": (value or {}).get("characters", [])}


@app.get("/api/summaries")
def summaries() -> list[dict]:
    """Browsable event summaries (from event_summaries.json), joined with the
    events index for names/nicknames/dates, chronologically sorted."""
    path = _summaries_path()
    if not path.exists():
        return []
    by_arc = json.loads(path.read_text(encoding="utf-8"))
    episodes_by_arc = json.loads(_tier_path("episode_summaries.json").read_text(encoding="utf-8")) \
        if _tier_path("episode_summaries.json").exists() else {}
    events_by_arc = {e.get("arc_slug"): e for e in load_events()}
    out = []
    for arc_id, value in by_arc.items():
        ent = _entry(value)  # handles bare-string (old) or {summary, characters}
        e = events_by_arc.get(arc_id, {})
        out.append({
            "arc_id": arc_id,
            "name": e.get("name", arc_id),
            "unit": e.get("unit", "unknown"),
            "nickname": e.get("nickname"),
            "started_at": e.get("started_at", 0),
            "ended_at": e.get("ended_at", 0),
            "regions": _regions_for(e),
            "focus_character_id": e.get("focus_character_id", 0),
            "song_title": e.get("song_title", ""),
            "jacket_url": e.get("jacket_url", ""),
            "is_key_story": e.get("is_key_story", False),
            "logo_url": e.get("logo_url", ""),
            "summary": ent["summary"],
            "characters": ent["characters"],
            "episode_count": len(episodes_by_arc.get(arc_id, {})),
        })
    out.sort(key=lambda r: (r.get("started_at", 0), r["arc_id"]))
    return out


@app.get("/api/episodes")
def episodes(arc: str) -> list[dict]:
    """Tier-1 episode summaries for one event, in reading order (lazy-loaded when
    a card is expanded)."""
    path = _tier_path("episode_summaries.json")
    if not path.exists():
        return []
    eps = json.loads(path.read_text(encoding="utf-8")).get(arc, {})

    def epnum(k: str) -> int:
        m = re.match(r"(\d+)", k)
        return int(m.group(1)) if m else 0

    out = []
    for key in sorted(eps, key=epnum):
        ent = _entry(eps[key])
        out.append({"episode": key, "summary": ent["summary"], "characters": ent["characters"]})
    return out


@app.get("/api/unit-summaries")
def unit_summaries() -> dict:
    """Tier-3 unit-level summaries, keyed by unit slug."""
    path = _tier_path("unit_summaries.json")
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {u: _entry(v) for u, v in raw.items()}


class QueryRequest(BaseModel):
    question: str
    unit: str | None = None
    event_id: int | None = None
    history: list[dict] = []  # prior turns: [{role, text}, ...] for follow-ups


# Backend selection: "local" (default) = dependency-light lexical engine that
# runs anywhere with no API key; "full" = Google embeddings + Gemini + Chroma.
QUERY_BACKEND = os.environ.get("SEKAI_QUERY_BACKEND", "local")


def _story_root() -> Path:
    env = os.environ.get("SEKAI_STORY_ROOT")
    if env:
        return Path(env)
    for candidate in (Path("story"), HERE.parent / "story", HERE.parent / "sample" / "story"):
        if candidate.exists():
            return candidate
    return Path("story")


_local_engine: dict[str, object] = {"engine": None}


def _get_local_engine():
    if _local_engine["engine"] is None:
        from sekai_story_indexer.query.local import build_local_engine

        _local_engine["engine"] = build_local_engine(_story_root(), _static_events())
    return _local_engine["engine"]


# NL generation over local retrieval ("RAG-lite"): on by default when a key is
# present; set SEKAI_GENERATE=0 to force pure extractive output.
GENERATE = os.environ.get("SEKAI_GENERATE", "1") != "0"


def _query_local(req: QueryRequest) -> dict:
    from sekai_story_indexer.query.condense import condense
    from sekai_story_indexer.query.intent import classify

    engine: Any = _get_local_engine()
    # Conversation memory: rewrite a follow-up into a standalone question using
    # recent history, then route/retrieve on that.
    q = condense(req.question, req.history) if req.history else req.question
    intent = classify(q)

    # Deterministic paths for common shapes (mirrors the original's routing).
    if intent == "count":
        result = engine.count_dialogue(q, unit=req.unit, event_id=req.event_id)
        result["error"] = None
        result["resolved_question"] = q
        return result  # exact count — never LLM-generated
    if intent == "summarize":
        result = engine.summarize(q, unit=req.unit, event_id=req.event_id)
    else:
        result = engine.query(q, unit=req.unit, event_id=req.event_id)
    result.setdefault("intent", intent)
    result["resolved_question"] = q
    result["error"] = None

    # Pre-computed summaries are returned as-is — no re-summarizing raw scenes.
    if result.get("pre_summarized"):
        result["generated"] = True
        return result

    if GENERATE and result.get("citations"):
        from sekai_story_indexer.query.generate import generate_answer

        nl = generate_answer(q, result["citations"])
        if nl:
            # Natural-language answer up top; keep quotes as supporting evidence.
            result["answer"] = nl
            result["answer_parts"] = (
                [{"type": "text", "text": nl}]
                + [p for p in result.get("answer_parts", []) if p.get("type") == "quote"]
            )
            result["generated"] = True
    return result


# The full engine writes inline "CITATION: <label>" markers (optionally wrapped in
# parens with trailing detail). Turn them into the same clickable [n] + citations
# shape the local backend returns, so the UI renders links + an excerpt sidebar.
_CIT_RE = re.compile(r"\(?\s*CITATION:\s*([^\s;)\]]+)(?:\s*;[^)]*)?\)?")
_ARC_RE = re.compile(r"\d{4}(?:-[a-z0-9-]+)?")
_TAG_STRIP = re.compile(r"\{char_id=\d+\}")


def _structure_full_answer(answer: str) -> dict:
    events_by_arc = {e.get("arc_slug"): e for e in load_events()}
    path = _summaries_path()
    sums = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    refs: dict[str, int] = {}
    order: list[str] = []

    def _repl(m: re.Match) -> str:
        label = m.group(1)
        if label not in refs:
            refs[label] = len(refs) + 1
            order.append(label)
        return f"[{refs[label]}]"

    text = _CIT_RE.sub(_repl, answer or "").strip()
    citations = []
    for label in order:
        am = _ARC_RE.search(label)
        arc = am.group(0) if am else label
        e = events_by_arc.get(arc, {})
        excerpt = _TAG_STRIP.sub("", _entry(sums.get(arc, "")).get("summary", ""))
        citations.append({
            "ref": refs[label],
            "arc_id": arc,
            "label": e.get("name") or arc,
            "nickname": e.get("nickname"),
            "excerpt": excerpt,
        })
    return {
        "answer": text,
        "answer_parts": [{"type": "text", "text": text}],
        "citations": citations,
        "error": None,
        "backend": "full",
    }


def _query_full(req: QueryRequest) -> dict:
    try:
        from sekai_story_indexer.database import initialize_query_settings
        from sekai_story_indexer.query.engine import RetrievalConfig, StoryQueryEngine
    except Exception as exc:  # pragma: no cover - optional heavy deps
        return {"answer": None, "error": f"full query engine unavailable: {exc}", "backend": "full"}
    try:
        initialize_query_settings()
        engine = StoryQueryEngine(retrieval_config=RetrievalConfig())
        return _structure_full_answer(engine.query(req.question))
    except Exception as exc:  # pragma: no cover - runtime/config
        return {
            "answer": None,
            "backend": "full",
            "error": (
                f"{exc}. Build the index (`indexer fetch` + `indexer ingest`) and set "
                "GOOGLE_API_KEY, or use SEKAI_QUERY_BACKEND=local."
            ),
        }


@app.post("/api/query")
def query(req: QueryRequest) -> dict:
    """Answer a question. Uses the local lexical engine by default (runs anywhere);
    set SEKAI_QUERY_BACKEND=full for the Google/Chroma RAG stack."""
    if QUERY_BACKEND == "full":
        return _query_full(req)
    try:
        return _query_local(req)
    except Exception as exc:  # pragma: no cover - defensive
        return {"answer": None, "error": f"local query failed: {exc}", "backend": "local"}


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "backend": QUERY_BACKEND, "events": len(load_events())}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


if STATIC.exists():
    app.mount("/static", StaticFiles(directory=STATIC), name="static")
