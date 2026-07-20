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

    _overlay_en_titles(rows)
    _cache["rows"] = rows
    _cache["at"] = now
    return rows


# Official English titles (event names + song titles) from the EN master DB, so
# the UI/citations show English instead of Japanese where localized. Cached a day.
_EN_TTL_SECONDS = int(os.environ.get("SEKAI_EN_TTL", "86400"))
_en_cache: dict[str, object] = {"at": 0.0, "names": None, "songs": None}


def _en_maps() -> tuple[dict, dict]:
    now = time.time()
    if _en_cache["names"] is not None and now - float(str(_en_cache["at"])) < _EN_TTL_SECONDS:
        return _en_cache["names"], _en_cache["songs"]  # type: ignore[return-value]
    try:
        from sekai_story_indexer.source import client

        names, songs = client.en_event_names(), client.en_music_titles()
    except Exception:
        names, songs = {}, {}
    _en_cache.update(at=now, names=names, songs=songs)
    return names, songs


def _overlay_en_titles(rows: list[dict]) -> None:
    """Replace event name + song title with official English, keeping JP as
    *_jp fallbacks. Events/songs not yet localized keep their Japanese."""
    names, songs = _en_maps()
    if not names and not songs:
        return
    for r in rows:
        en = names.get(r.get("event_id"))
        if en and en != r.get("name"):
            r["name_jp"] = r.get("name")
            r["name"] = en
        es = songs.get(r.get("song_id"))
        if es and es != r.get("song_title"):
            r["song_title_jp"] = r.get("song_title")
            r["song_title"] = es


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


# The full engine writes inline citations in two forms: "CITATION: <label>"
# (optionally paren-wrapped) and bracketed "[<arc-slug> · Episode …]". Turn both
# into clickable [n] refs (collapsed per event) + a citations array, so the UI
# renders links + an excerpt sidebar like the local backend.
_ARC_RE = re.compile(r"\d{4}(?:-[a-z0-9-]+)?")
# both citation forms in ONE pass (so [n] numbers in reading order): the
# "CITATION: <label>" form (group 1) and the bracketed "[<arc-slug> · …]" form
# (group 2, a bracket whose contents include an arc-slug).
_CIT_RE = re.compile(
    r"\(?\s*CITATION:\s*([^\s;)\]]+)(?:\s*;[^)]*)?\)?"
    r"|\[([^\]\n]*\d{4}(?:-[a-z0-9-]+)?[^\]\n]*)\]"
    # bare form: "<arc-slug> · Side Story … · Part … · Scene 1" (no brackets).
    # Requires at least one " · " segment so a plain 4-digit number won't match.
    r"|(\d{4}(?:-[a-z0-9-]+)?(?:\s*·\s*[^·\n.]+)+)"
)
_TAG_STRIP = re.compile(r"\{char_id=\d+\}")


def _structure_full_answer(answer: str) -> dict:
    events_by_arc = {e.get("arc_slug"): e for e in load_events()}
    path = _summaries_path()
    sums = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    refs: dict[str, int] = {}  # arc_slug -> ref number
    order: list[str] = []

    def _repl(m: re.Match) -> str:
        label = m.group(1) or m.group(2) or m.group(3) or ""
        am = _ARC_RE.search(label)
        arc = am.group(0) if am else label.strip()
        if arc not in refs:
            refs[arc] = len(refs) + 1
            order.append(arc)
        return f"[{refs[arc]}]"

    text = _CIT_RE.sub(_repl, answer or "").strip()
    # The engine wraps bits of prose/citations in backticks; strip them so the UI
    # doesn't render them as blue code spans (these answers never contain code).
    text = text.replace("`", "")

    citations = []
    for arc in order:
        e = events_by_arc.get(arc, {})
        excerpt = _TAG_STRIP.sub("", _entry(sums.get(arc, "")).get("summary", ""))
        citations.append({
            "ref": refs[arc],
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


def _characters_meta() -> dict:
    p = STATIC / "meta.json"
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8")).get("characters", {})
        except Exception:
            return {}
    return {}


def _metadata_intercept(question: str) -> dict | None:
    """Answer pure-metadata questions (focus events) deterministically, ahead of
    either RAG backend. Returns None for everything else."""
    try:
        from sekai_story_indexer.query.metadata import metadata_answer

        path = _summaries_path()
        sums = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        return metadata_answer(question, load_events(), _characters_meta(), sums)
    except Exception:
        return None


def _resolve_focus_scope(req: QueryRequest) -> dict | None:
    """If the question refers to an event by focus name/nickname, append the event's
    real name to the question (so retrieval finds it) and scope to its event_id.
    Returns the resolved event (for logging) or None."""
    try:
        from sekai_story_indexer.query.metadata import resolve_focus_reference

        ev = resolve_focus_reference(req.question, load_events(), _characters_meta())
        if not ev:
            return None
        name, nick = ev.get("name"), ev.get("nickname")
        tag = f'"{name}"' + (f" [{nick}]" if nick else "")
        req.question = f"{req.question}\n\n(Note: this question refers to the event {tag}.)"
        if not req.event_id and ev.get("event_id"):
            req.event_id = ev["event_id"]
        return ev
    except Exception:
        return None


# Chat log: one JSON line per turn (raw + condensed question, route taken, backend,
# citation count) so we can see what was asked and which path answered it.
_CHATLOG = Path(os.environ.get("SEKAI_CHAT_LOG", "chat_log.jsonl"))


def _log_turn(rec: dict) -> None:
    try:
        import datetime

        rec = {"ts": datetime.datetime.now(datetime.UTC).isoformat(), **rec}
        with _CHATLOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


_SUMMARIZE_RE = re.compile(r"\b(summar(?:y|ize|ise)|recap|overview|tl;?dr|synops)", re.IGNORECASE)


def _summarize_intercept(question: str) -> dict | None:
    """'Summarize <event>' where the event resolves (nickname/focus) and has a
    pre-computed summary -> return that summary directly, instead of letting the
    RAG re-summarize raw scenes (which tends to dump the whole cast)."""
    if not _SUMMARIZE_RE.search(question):
        return None
    try:
        from sekai_story_indexer.query.metadata import resolve_focus_reference

        ev = resolve_focus_reference(question, load_events(), _characters_meta())
        if not ev:
            return None
        path = _summaries_path()
        sums = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        ent = _entry(sums.get(ev.get("arc_slug"), ""))
        if not ent["summary"]:
            return None
        return {
            "answer": ent["summary"],
            "answer_parts": [{"type": "text", "text": ent["summary"]}],
            "characters": ent["characters"],
            "citations": [{
                "ref": 1, "arc_id": ev.get("arc_slug"), "label": ev.get("name"),
                "nickname": ev.get("nickname"), "excerpt": ent["summary"],
            }],
            "intent": "summarize", "backend": "summary", "error": None,
        }
    except Exception:
        return None


@app.post("/api/query")
def query(req: QueryRequest) -> dict:
    """Answer a question. Uses the local lexical engine by default (runs anywhere);
    set SEKAI_QUERY_BACKEND=full for the Google/Chroma RAG stack."""
    raw = req.question
    log: dict = {"question": raw, "history_len": len(req.history or [])}

    # Resolve follow-ups into a standalone question up front, so BOTH backends and
    # the intercepts see the full context (the full engine has no history of its own).
    if req.history:
        try:
            from sekai_story_indexer.query.condense import condense

            req.question = condense(req.question, req.history)
        except Exception:
            pass
        if req.question != raw:
            log["condensed"] = req.question
        req.history = []  # consumed -> no double-condense in _query_local

    # Pure identity/count/list focus-event questions -> deterministic answer.
    md = _metadata_intercept(req.question)
    if md is not None:
        _log_turn({**log, "route": "metadata", "backend": md.get("backend"), "citations": len(md.get("citations") or [])})
        return md
    # "Summarize <event>" -> serve the pre-computed summary, not a raw-scene RAG dump.
    sm = _summarize_intercept(req.question)
    if sm is not None:
        _log_turn({**log, "route": "summarize", "citations": len(sm.get("citations") or [])})
        return sm
    # Other content questions that REFER to an event by focus name/nickname -> resolve
    # to the event and point the RAG at it, then answer normally.
    ev = _resolve_focus_scope(req)
    if ev:
        log["resolved_event"] = ev.get("arc_slug")
    if QUERY_BACKEND == "full":
        result = _query_full(req)
    else:
        try:
            result = _query_local(req)
        except Exception as exc:  # pragma: no cover - defensive
            result = {"answer": None, "error": f"local query failed: {exc}", "backend": "local"}
    _log_turn({
        **log, "route": "rag", "backend": result.get("backend"),
        "citations": len(result.get("citations") or []), "error": result.get("error"),
    })
    return result


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "backend": QUERY_BACKEND, "events": len(load_events())}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


if STATIC.exists():
    app.mount("/static", StaticFiles(directory=STATIC), name="static")
