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

from fastapi import FastAPI, Response
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


_EMPTY_HIERARCHY = {
    "roots": [],
    "nodes": {},
    "summaries": {},
    "counts": {"events": 0, "episodes": 0, "parts": 0},
}


def _hierarchical_cache_path() -> Path:
    env = os.environ.get("SEKAI_SUMMARIES_CACHE")
    candidates = (
        [Path(env)] if env else [Path("summaries_cache.json"), HERE.parent / "summaries_cache.json"]
    )
    return next((p for p in candidates if p.exists()), candidates[-1])


@app.get("/api/hierarchical-summaries")
def hierarchical_summaries() -> dict:
    """Tiered event -> episode -> part summaries from the hierarchical cache
    (``summaries_cache.json``), for in-app quality review. Returns an empty tree
    when the cache is absent/unreadable so the tab degrades gracefully.

    Dependency-light: ``summary_export`` no longer pulls the generation stack."""
    path = _hierarchical_cache_path()
    if not path.exists():
        return _EMPTY_HIERARCHY
    try:
        cache = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _EMPTY_HIERARCHY

    from sekai_story_indexer.story_order import StoryOrderConfigError, load_story_order
    from sekai_story_indexer.summary_export import build_summary_reader_data

    try:
        # config only (no story_root) so cache arcs missing from the yaml don't
        # raise — the reader's sort keys fall back to end-of-list for unknown arcs.
        story_order = load_story_order()
    except (FileNotFoundError, StoryOrderConfigError):
        return _EMPTY_HIERARCHY

    try:
        data = build_summary_reader_data(cache, story_order=story_order)
    except (ValueError, KeyError):
        return _EMPTY_HIERARCHY

    # Label event-tier nodes with the real event name/nickname instead of "Arc <id>".
    events_by_arc = {e.get("arc_slug"): e for e in load_events()}
    for node in data.get("nodes", {}).values():
        if node.get("kind") != "event":
            continue
        arc = str(node.get("id", "")).removeprefix("event:")
        ev = events_by_arc.get(arc)
        if ev:
            name = ev.get("name") or node.get("label")
            nickname = ev.get("nickname")
            node["title"] = name
            node["label"] = f"{name} · {nickname}" if nickname else name
    return data


class QueryRequest(BaseModel):
    question: str
    unit: str | None = None
    event_id: int | None = None
    history: list[dict] = []  # prior turns: [{role, text}, ...] for follow-ups
    session_id: str | None = None  # stable per-chat id for server-side focus state


# Server-side conversation focus: remembers the current event/character per chat
# session, so pronoun follow-ups stay on topic (even with no condense key) and a
# topic switch resets it. See webapp/sessions.py.
from webapp.sessions import Focus, SessionStore, is_followup, resolve_turn  # noqa: E402

_SESSIONS = SessionStore()


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


def _local_retrieval(req: QueryRequest, scope_arc_ids: tuple[str, ...] = ()) -> dict:
    """Local retrieval only (count/summarize/query) — no NL generation. Shared by
    the JSON and streaming paths so streaming can emit citations, then stream the
    answer over them. ``scope_arc_ids`` carries explicit refs or conversation
    focus so a follow-up stays on the remembered event."""
    from sekai_story_indexer.query.condense import condense
    from sekai_story_indexer.query.intent import classify

    engine: Any = _get_local_engine()
    q = condense(req.question, req.history) if req.history else req.question
    intent = classify(q)

    if intent == "count":
        result = engine.count_dialogue(q, unit=req.unit, event_id=req.event_id)
    elif intent == "summarize":
        result = engine.summarize(q, unit=req.unit, event_id=req.event_id, arc_ids=scope_arc_ids)
    else:
        result = engine.query(q, unit=req.unit, event_id=req.event_id, arc_ids=scope_arc_ids)
    result.setdefault("intent", intent)
    result["resolved_question"] = q
    result["error"] = None
    return result


def _can_generate_over(result: dict) -> bool:
    """NL generation applies only to non-count, non-pre-summarized results that
    actually retrieved evidence."""
    return bool(
        GENERATE
        and result.get("intent") != "count"
        and not result.get("pre_summarized")
        and result.get("citations")
    )


def _best_supporting_line(excerpt: str, answer: str) -> str:
    """The line in a cited scene most relevant to the answer — pins WHERE a claim
    came from (the UI highlights it in the excerpt), instead of the whole episode."""
    from sekai_story_indexer.query.local import tokenize

    atoks = set(tokenize(answer))
    best, best_overlap = "", 0
    for ln in (excerpt or "").splitlines():
        s = ln.strip()
        if not s or s.startswith("#"):
            continue
        overlap = len(atoks & set(tokenize(s)))
        if overlap > best_overlap:
            best, best_overlap = s, overlap
    return best


def _finalize_citations(nl: str, citations: list[dict]) -> tuple[str, list[dict]]:
    """Keep only the citations the answer actually references, in first-cited order,
    renumber them contiguously, rewrite the answer's [n], and pin each kept citation
    to its most-relevant supporting line. Leaves things unchanged if the model cited
    nothing resolvable (so we never blank the sources)."""
    refs: list[int] = []
    for m in re.findall(r"\[(\d+)\]", nl):
        r = int(m)
        if r not in refs:
            refs.append(r)
    by_ref = {c.get("ref"): c for c in citations}
    remap: dict[int, int] = {}
    kept: list[dict] = []
    for old in refs:
        c = by_ref.get(old)
        if c is None:
            continue
        remap[old] = len(kept) + 1
        c = {**c, "ref": remap[old]}
        pinned = _best_supporting_line(c.get("excerpt", ""), nl)
        if pinned:
            c["quote"] = pinned
        kept.append(c)
    if not kept:
        return nl, citations  # model didn't cite resolvably -> don't strip sources
    nl2 = re.sub(r"\[(\d+)\]", lambda m: f"[{remap[int(m.group(1))]}]"
                 if int(m.group(1)) in remap else "", nl)
    # a stripped hallucinated ref can leave " ." / doubled spaces — tidy them
    nl2 = re.sub(r"\s+([.,;:!?])", r"\1", nl2)
    nl2 = re.sub(r" {2,}", " ", nl2)
    return nl2, kept


def _apply_generated_answer(result: dict, nl: str) -> None:
    nl, kept = _finalize_citations(nl, result.get("citations") or [])
    result["answer"] = nl
    result["citations"] = kept
    # prose + a supporting-quote block per cited source (the exact line, [ref]).
    parts: list[dict] = [{"type": "text", "text": nl}]
    for c in kept:
        if c.get("quote"):
            parts.append({"type": "quote", "ref": c["ref"], "text": c["quote"]})
    result["answer_parts"] = parts
    result["generated"] = True


def _trim_extractive_citations(result: dict) -> None:
    """For an extractive (non-generated) answer, keep only the citations the quote
    blocks actually reference — otherwise a scoped whole-event query would list the
    entire event as sources (mirrors the generated path's referenced-only trim)."""
    refs = {
        p.get("ref")
        for p in (result.get("answer_parts") or [])
        if p.get("type") == "quote" and p.get("ref") is not None
    }
    if refs:
        result["citations"] = [
            c for c in (result.get("citations") or []) if c.get("ref") in refs
        ]


def _query_local(req: QueryRequest, scope_arc_ids: tuple[str, ...] = ()) -> dict:
    result = _local_retrieval(req, scope_arc_ids)
    if result.get("pre_summarized"):
        result["generated"] = True
        return result
    if _can_generate_over(result):
        from sekai_story_indexer.query.generate import generate_answer

        nl = generate_answer(result["resolved_question"], result["citations"])
        if nl:
            _apply_generated_answer(result, nl)
    if not result.get("generated"):
        _trim_extractive_citations(result)
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


def _query_full(req: QueryRequest, *, arc_ids: tuple[str, ...] = ()) -> dict:
    try:
        from sekai_story_indexer.database import initialize_query_settings
        from sekai_story_indexer.query.engine import RetrievalConfig, StoryQueryEngine
    except Exception as exc:  # pragma: no cover - optional heavy deps
        return {"answer": None, "error": f"full query engine unavailable: {exc}", "backend": "full"}
    try:
        initialize_query_settings()
        engine = StoryQueryEngine(retrieval_config=RetrievalConfig())
        # Pass the resolved event scope so a follow-up ("the climax of that story")
        # stays on the resolved arc instead of vector-searching the whole corpus.
        return _structure_full_answer(engine.query(req.question, arc_ids=arc_ids))
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


def _clarify_intercept(question: str, focus_character_id: int | None = None) -> dict | None:
    """Clarify-instead-of-guess: if the reference is ambiguous (>=2 distinct
    interpretations, e.g. an event title AND a character's multi-event arc), ask
    which one rather than silently answering from one. ``focus_character_id`` is the
    remembered focus (for the conversational event-vs-arc case). None otherwise."""
    try:
        from sekai_story_indexer.query.disambiguation import maybe_clarify

        return maybe_clarify(
            question, load_events(), _characters_meta(),
            focus_character_id=focus_character_id,
        )
    except Exception:
        return None


def _named_character_id(question: str) -> int | None:
    """The character explicitly named in the question, if any (for focus state)."""
    try:
        from sekai_story_indexer.query.metadata import _resolve_char

        return _resolve_char(question.lower(), _characters_meta())
    except Exception:
        return None


def _referenced_arcs(question: str, events: list[dict]) -> list[str]:
    """All arcs referenced by a nickname token in the question, in first-seen order.
    Scoping to the UNION (not just the first) keeps multi-story questions — e.g.
    "compare koha1 and mafu1" — confined to exactly those stories instead of
    hard-locking to whichever nickname appeared first."""
    from sekai_story_indexer.query.metadata import _NICK_RE

    by_nick = {
        (e.get("nickname") or "").lower(): e.get("arc_slug")
        for e in events
        if e.get("nickname") and e.get("arc_slug")
    }
    arcs: list[str] = []
    for m in _NICK_RE.finditer(question):
        arc = by_nick.get(m.group(1).lower())
        if arc and arc not in arcs:
            arcs.append(arc)
    return arcs


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
                "ref": 1, "arc_id": ev.get("arc_slug"),
                "label": f"{ev.get('name')} — event summary",
                "episode_title": "Event summary",
                "nickname": ev.get("nickname"), "excerpt": ent["summary"],
            }],
            "intent": "summarize", "backend": "summary", "error": None,
        }
    except Exception:
        return None


def _with_focus(result: dict, focus: Focus | None) -> dict:
    """Attach the current focus to a response so the UI chip reflects it, enriched
    with the focus event's unit + nickname (for the chip's icon/label)."""
    if focus and (focus.arcs or focus.character_id):
        fd = focus.as_dict()
        if focus.arcs:
            ev = next((e for e in load_events() if e.get("arc_slug") == focus.arcs[0]), None)
            if ev:
                fd["unit"] = ev.get("unit")
                fd["nickname"] = ev.get("nickname")
        result = {**result, "focus": fd}
    return result


def _remember_focus(session_id: str | None, focus: Focus) -> Focus:
    focus.updated_at = time.time()
    _SESSIONS.set(session_id, focus)
    return focus


def _rag_log(log: dict, result: dict, scope_arc_ids: tuple[str, ...], **extra) -> dict:
    """Uniform RAG-turn log (both JSON + streaming) — enough to SEE a silent guess:
    an unscoped summarize with a weak top score is the 'rise as one' fingerprint."""
    scope = result.get("scope") or {}
    cits = result.get("citations") or []
    return {
        **log, "route": "rag", "backend": result.get("backend"),
        "citations": len(cits),
        "scoped": bool(scope.get("arc_id") or scope.get("arc_ids") or scope.get("unit")
                       or scope_arc_ids),
        "scope_arc": scope.get("arc_id"),
        "top_arc": cits[0].get("arc_id") if cits else None,
        "top_score": cits[0].get("score") if cits else None,
        "fell_back": result.get("summarize_fell_back"),
        "error": result.get("error"),
        **extra,
    }


def _resolve_request(req: QueryRequest) -> tuple[dict | None, tuple[str, ...], Focus | None, dict]:
    """Shared pipeline for the JSON and streaming endpoints.

    Returns ``(early_result, scope_arc_ids, focus, log)``. When ``early_result`` is
    non-None it's a finished intercept answer (metadata / clarify / summary);
    otherwise the caller runs the RAG backend with ``scope_arc_ids``.
    """
    raw = req.question
    log: dict = {"question": raw, "history_len": len(req.history or []),
                 "session_id": req.session_id}
    followup = is_followup(raw)

    # Windowed condense: bound history, then rewrite follow-ups to standalone so
    # both backends + intercepts see full context.
    if req.history:
        try:
            from sekai_story_indexer.query.condense import condense, window_history

            req.history = window_history(req.history)
            req.question = condense(raw, req.history)
        except Exception:
            pass
        if req.question != raw:
            log["condensed"] = req.question
        req.history = []

    events = load_events()
    prev = _SESSIONS.get(req.session_id)

    # Pure identity/count/list focus-event questions -> deterministic answer.
    md = _metadata_intercept(req.question)
    if md is not None:
        return md, (), prev, {**log, "route": "metadata", "backend": md.get("backend"),
                              "citations": len(md.get("citations") or [])}
    # Ambiguous reference -> ask (with remembered focus for the conversational case).
    cl = _clarify_intercept(req.question, prev.character_id if prev else None)
    if cl is not None:
        return cl, (), prev, {**log, "route": "clarify", "backend": "clarify",
                              "candidates": [o.get("label") for o in cl.get("options") or []]}
    # "Summarize <event>" -> pre-computed summary; update focus to that event.
    sm = _summarize_intercept(req.question)
    if sm is not None:
        arcs = tuple(c.get("arc_id") for c in (sm.get("citations") or []) if c.get("arc_id"))
        focus = _remember_focus(req.session_id, Focus(
            arcs=arcs, character_id=prev.character_id if prev else None,
            label=(sm.get("citations") or [{}])[0].get("label"),
        )) if arcs else prev
        return sm, (), focus, {**log, "route": "summarize",
                               "citations": len(sm.get("citations") or [])}

    # Content path. Union of arcs the question references (so comparisons aren't
    # locked to one), plus the focus-resolved event.
    referenced = _referenced_arcs(req.question, events)
    named_cid = _named_character_id(req.question)
    ev = _resolve_focus_scope(req)
    label = None
    if ev:
        log["resolved_event"] = ev.get("arc_slug")
        label = ev.get("name")
        if ev.get("arc_slug") and ev["arc_slug"] not in referenced:
            referenced.append(ev["arc_slug"])
    # Focus character: a named character, else the resolved event's own focus
    # character (so a nickname turn like 'koha1' still seeds the focus character —
    # what the conversational event-vs-arc clarify needs on the next turn).
    focus_cid = named_cid or (ev.get("focus_character_id") if ev else None) or None

    focus, scope_arcs = resolve_turn(
        prev, referenced_arcs=tuple(referenced), character_id=focus_cid,
        label=label, followup=followup,
    )
    _remember_focus(req.session_id, focus)
    log["focus"] = focus.as_dict()
    if scope_arcs and not referenced:
        log["carried_focus_arc"] = list(scope_arcs)

    return None, tuple(scope_arcs), focus, log


@app.post("/api/query")
def query(req: QueryRequest) -> dict:
    """Answer a question. Uses the local lexical engine by default (runs anywhere);
    set SEKAI_QUERY_BACKEND=full for the Google/Chroma RAG stack."""
    early, scope_arc_ids, focus, log = _resolve_request(req)
    if early is not None:
        _log_turn(log)
        return _with_focus(early, focus)

    if QUERY_BACKEND == "full":
        result = _query_full(req, arc_ids=scope_arc_ids)
    else:
        try:
            result = _query_local(req, scope_arc_ids)
        except Exception as exc:  # pragma: no cover - defensive
            result = {"answer": None, "error": f"local query failed: {exc}", "backend": "local"}
    _log_turn(_rag_log(log, result, scope_arc_ids))
    return _with_focus(result, focus)


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _chunk_text(text: str, size: int = 24):
    for i in range(0, len(text), size):
        yield text[i : i + size]


def _stream_events(req: QueryRequest):
    """SSE event stream: a `meta` event, `delta` text events (real token streaming
    for local generation; chunked otherwise), then a `done` event with the full
    structured result (citations, scope, focus) for final rendering."""
    early, scope_arc_ids, focus, log = _resolve_request(req)
    focus_d = focus.as_dict() if focus and (focus.arcs or focus.character_id) else None

    if early is not None:
        _log_turn({**log, "streamed": True})
        yield _sse({"type": "meta", "backend": early.get("backend"),
                    "intent": early.get("intent"), "focus": focus_d})
        for piece in _chunk_text(early.get("answer") or ""):
            yield _sse({"type": "delta", "text": piece})
        yield _sse({"type": "done", **_with_focus(early, focus)})
        return

    if QUERY_BACKEND == "full":
        # NOTE: the full engine generates the whole answer before returning (its
        # citation post-processing needs the complete text), so these deltas are
        # chunked-after-the-fact, not true token streaming. Real token streaming
        # here would need engine.stream_query wired through _structure_full_answer.
        result = _query_full(req, arc_ids=scope_arc_ids)
        yield _sse({"type": "meta", "backend": result.get("backend"), "focus": focus_d})
        for piece in _chunk_text(result.get("answer") or ""):
            yield _sse({"type": "delta", "text": piece})
        _log_turn(_rag_log(log, result, scope_arc_ids, streamed=True))
        yield _sse({"type": "done", **_with_focus(result, focus)})
        return

    # Local: retrieve first (so we can stream the answer over the citations).
    try:
        result = _local_retrieval(req, scope_arc_ids)
    except Exception as exc:  # pragma: no cover - defensive
        yield _sse({"type": "done", "answer": None,
                    "error": f"local query failed: {exc}", "backend": "local"})
        return
    yield _sse({"type": "meta", "backend": "local", "intent": result.get("intent"),
                "scope": result.get("scope"), "focus": focus_d})

    streamed = ""
    if not result.get("pre_summarized") and _can_generate_over(result):
        from sekai_story_indexer.query.generate import generate_answer_stream

        for piece in generate_answer_stream(result["resolved_question"], result["citations"]):
            streamed += piece
            yield _sse({"type": "delta", "text": piece})
    if streamed:
        _apply_generated_answer(result, streamed)
    else:  # extractive / pre-summarized / no key -> chunk the computed answer
        if result.get("pre_summarized"):
            result["generated"] = True
        else:
            _trim_extractive_citations(result)
        for piece in _chunk_text(result.get("answer") or ""):
            yield _sse({"type": "delta", "text": piece})

    _log_turn(_rag_log(log, result, scope_arc_ids, streamed=True, generated=bool(streamed)))
    yield _sse({"type": "done", **_with_focus(result, focus)})


@app.post("/api/query/stream")
def query_stream(req: QueryRequest):
    """Streaming variant of /api/query (Server-Sent Events) for responsive output."""
    from fastapi.responses import StreamingResponse

    return StreamingResponse(
        _stream_events(req),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# Image proxy: the browser often can't reach the sekai.best asset CDN directly
# (restricted network), but the server can. Proxy those images through the app so
# event art / jackets / logos load from same-origin. Host-allowlisted (SSRF guard)
# + tiny in-memory cache.
_ART_HOST = "storage.sekai.best"
_ART_MAX_BYTES = 8 * 1024 * 1024  # event art / jackets are tiny; cap to be safe
_art_cache: dict[str, tuple[bytes, str]] = {}


def _art_ssl():
    import ssl

    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:  # pragma: no cover - certifi optional
        return ssl.create_default_context()


_ART_SSL = _art_ssl()


@app.get("/api/img")
def proxy_image(u: str) -> Response:
    import urllib.request
    from urllib.parse import urlparse

    parsed = urlparse(u)
    if parsed.scheme != "https" or parsed.hostname != _ART_HOST:
        return Response(status_code=400)  # only the sekai asset CDN
    cached = _art_cache.get(u)
    if cached is None:
        try:
            req = urllib.request.Request(u, headers={"User-Agent": "sekai-story-indexer"})
            with urllib.request.urlopen(req, timeout=15, context=_ART_SSL) as r:
                # cap the body so a large asset URL can't balloon server memory
                data = r.read(_ART_MAX_BYTES + 1)
                if len(data) > _ART_MAX_BYTES:
                    return Response(status_code=502)
                cached = (data, r.headers.get("Content-Type", "image/webp"))
        except Exception:
            return Response(status_code=502)
        if len(_art_cache) > 512:
            _art_cache.clear()
        _art_cache[u] = cached
    return Response(
        content=cached[0], media_type=cached[1],
        headers={"Cache-Control": "public, max-age=86400"},
    )


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "backend": QUERY_BACKEND, "events": len(load_events())}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


if STATIC.exists():
    app.mount("/static", StaticFiles(directory=STATIC), name="static")
