"""Constant-ingestion pipeline: one ordered run that brings the corpus, the
indexes and (optionally) the LLM-derived caches up to date with the live game.

Two tiers, by design:

* **Tier 1 (keyless, always runs)** — fetch new story text, re-link card/area
  content to its parent event, refresh the song→wiki page map, re-classify
  ``plot_weight``, rebuild the derived index. Everything the lexical backend
  needs to answer about a brand-new event is here, so a fresh event becomes
  queryable the moment Tier 1 lands.
* **Tier 2 (needs a key, best-effort)** — summaries, conclusions, resonance.
  Rate-budgeted via ``batch_limit`` so a scheduled run drains a backlog a few
  events at a time instead of tripping a spend cap or a CI timeout.

The runner is deliberately dumb and inspectable: a list of :class:`Step`, run in
order, each producing a :class:`StepResult`. An *optional* step that raises is
recorded and the run continues; a *required* step that raises aborts the rest
(they're reported as ``skipped``, never silently dropped). That's what makes
"no ``GOOGLE_API_KEY`` today" a normal outcome rather than a broken pipeline.

The step bodies import lazily, so importing this module stays dependency-light
(``localcli`` imports it inside the command body anyway).
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

INGEST_STATE_SCHEMA_VERSION = 1

#: Steps whose failure is expected in the wild (no key, quota, source hiccup).
STATUS_OK = "ok"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"


@dataclass
class Step:
    """One pipeline stage. ``run`` returns a short human-readable detail line."""

    name: str
    run: Callable[[], str]
    required: bool = True
    tier: int = 1


@dataclass
class StepResult:
    name: str
    status: str
    detail: str = ""
    seconds: float = 0.0
    tier: int = 1
    required: bool = True

    @property
    def ok(self) -> bool:
        return self.status == STATUS_OK


@dataclass
class IngestReport:
    steps: list[StepResult] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True when nothing *required* failed or was skipped by an abort."""
        return not any(r.status in (STATUS_FAILED, STATUS_SKIPPED) for r in self.steps if r.required)

    def summary(self) -> str:
        lines = [
            f"  {r.status:<7} tier{r.tier} {r.name}"
            + (f" — {r.detail}" if r.detail else "")
            + (f" ({r.seconds:.1f}s)" if r.seconds >= 0.1 else "")
            for r in self.steps
        ]
        counts = {s: sum(1 for r in self.steps if r.status == s) for s in
                  (STATUS_OK, STATUS_FAILED, STATUS_SKIPPED)}
        head = (f"ingest: {counts[STATUS_OK]} ok, {counts[STATUS_FAILED]} failed, "
                f"{counts[STATUS_SKIPPED]} skipped")
        return "\n".join([head, *lines])


def run_steps(
    steps: Sequence[Step],
    *,
    log: Callable[[str], None] = print,
    clock: Callable[[], float] = time.monotonic,
) -> IngestReport:
    """Run ``steps`` in order; return a report. Never raises for a step failure.

    A required step that raises aborts the remainder (recorded ``skipped``); an
    optional one is recorded ``failed`` and the run continues.
    """
    report = IngestReport()
    aborted = False
    for step in steps:
        result = StepResult(
            name=step.name, status=STATUS_SKIPPED, tier=step.tier, required=step.required
        )
        if aborted:
            result.detail = "skipped after an earlier required step failed"
            report.steps.append(result)
            continue
        log(f"→ {step.name}")
        started = clock()
        try:
            result.detail = step.run() or ""
            result.status = STATUS_OK
        except Exception as exc:  # noqa: BLE001 — the whole point is to keep going
            result.status = STATUS_FAILED
            result.detail = f"{type(exc).__name__}: {exc}"[:300]
            if step.required:
                aborted = True
        result.seconds = clock() - started
        log(f"  {result.status}: {result.detail}" if result.detail else f"  {result.status}")
        report.steps.append(result)
    return report


# --------------------------------------------------------------------------- #
# Ingest state (what the web app reads to render a "NEW" badge)
# --------------------------------------------------------------------------- #


def _baseline_stamp(row: dict, now: float) -> float:
    """First-seen for a *baseline* run: the event's release date, not the clock.

    A baseline sees the whole back catalogue at once; stamping it all "now" would
    make the app badge 200+ events as NEW for two weeks. The release date
    (``started_at``, epoch millis) is the honest answer, and it's never in the
    future relative to a corpus we just fetched.
    """
    started = row.get("started_at")
    if isinstance(started, (int, float)) and started > 0:
        return min(now, started / 1000)
    return now


def merge_ingest_state(
    state: dict | None, events: Iterable[dict], *, now: float, backdate: bool = False
) -> tuple[dict, list[int]]:
    """Fold the current events index into the persisted ingest state.

    Records the first timestamp at which we ever saw each ``event_id`` — the web
    app turns "first seen recently" into the timeline's NEW badge. Pure: no I/O,
    no clock, so it's directly testable. Returns ``(state, newly_seen_ids)``.

    ``backdate`` (baseline runs only) stamps release dates instead of ``now``.
    """
    state = dict(state or {})
    first_seen: dict[str, float] = dict(state.get("first_seen") or {})
    new_ids: list[int] = []
    for row in events:
        eid = row.get("event_id")
        if not isinstance(eid, int):
            continue
        key = str(eid)
        if key not in first_seen:
            first_seen[key] = _baseline_stamp(row, now) if backdate else now
            new_ids.append(eid)
    state.update(
        schema_version=INGEST_STATE_SCHEMA_VERSION,
        last_run=now,
        first_seen=first_seen,
        last_new_event_ids=sorted(new_ids),
        known_events=len(first_seen),
    )
    return state, sorted(new_ids)


def update_ingest_state(
    events_index: Path, state_path: Path, *, now: float | None = None
) -> list[int]:
    """Read the events index, merge into ``state_path``, write it back.

    A first run over an existing corpus sees *every* event at once. Flagging all
    of them as new would light up the whole timeline for the freshness window, so
    a baseline run is stamped from release dates and announces nothing.
    """
    now = time.time() if now is None else now
    rows = json.loads(events_index.read_text(encoding="utf-8")) if events_index.exists() else []
    prior = None
    if state_path.exists():
        try:
            prior = json.loads(state_path.read_text(encoding="utf-8"))
        except ValueError:
            prior = None
    baseline = prior is None
    state, new_ids = merge_ingest_state(prior, rows, now=now, backdate=baseline)
    if baseline:
        state["last_new_event_ids"] = []
        state["baseline"] = True
        new_ids = []
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    return new_ids


# --------------------------------------------------------------------------- #
# Pipeline construction
# --------------------------------------------------------------------------- #


@dataclass
class IngestConfig:
    story_root: Path = Path("story")
    events_index: Path = Path("events_index.json")
    content_parents: Path = Path("content_parents.json")
    lyric_map: Path = Path("lyric_page_map.json")
    derived_index: Path = Path("derived_index.json.gz")
    summaries_cache: Path = Path("summaries_cache.json")
    conclusions_cache: Path = Path("conclusions_cache.json")
    resonance_cache: Path = Path("resonance_cache.json")
    state_path: Path = Path("ingest_state.json")
    skip_existing: bool = True
    limit_events: int = 0
    cards: bool = True
    areas: bool = True
    unit_stories: bool = False
    with_llm: bool = False
    batch_limit: int = 5
    model: str = ""


def _fetch_events(cfg: IngestConfig) -> str:
    from .source.fetcher import fetch_and_write

    plans = fetch_and_write(
        cfg.story_root,
        limit=cfg.limit_events or None,
        skip_existing=cfg.skip_existing,
        log=lambda _msg: None,  # per-episode chatter is noise in a scheduled run
    )
    return f"{len(plans)} events on disk under {cfg.story_root}"


def _fetch_unit_stories(cfg: IngestConfig) -> str:
    from .source.fetcher import fetch_unit_stories

    return f"{fetch_unit_stories(cfg.story_root)} unit-story episodes"


def _fetch_cards(cfg: IngestConfig) -> str:
    from .source.fetcher import fetch_card_stories

    n = fetch_card_stories(cfg.story_root, skip_existing=cfg.skip_existing)
    return f"{n} card-story episodes written"


def _fetch_areas(cfg: IngestConfig) -> str:
    from .source.fetcher import fetch_area_conversations

    n = fetch_area_conversations(cfg.story_root, skip_existing=cfg.skip_existing)
    return f"{n} area-conversation talks written"


def _link_content(cfg: IngestConfig) -> str:
    from .source import client
    from .source.transform import (
        build_area_event_map,
        build_card_parent_map,
        build_content_parents,
    )

    cards_by_id = {c["id"]: c for c in client.cards()}
    card_map = build_card_parent_map(client.event_cards(), cards_by_id)
    area_map = build_area_event_map(
        client.action_sets(), client.release_conditions(), client.event_stories()
    )
    events_by_id = {e["id"]: e for e in client.events()}
    doc = build_content_parents(card_map, area_map, events_by_id)
    cfg.content_parents.write_text(
        json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return f"{len(doc['cards'])} cards + {len(doc['areas'])} area talks linked"


def _build_lyric_map(cfg: IngestConfig) -> str:
    from .source import client
    from .source.transform import build_lyric_page_map

    known_ids = {m["id"] for m in client.musics() if isinstance(m.get("id"), int)}
    result = build_lyric_page_map(client.sekaipedia_song_pages(), known_ids)
    payload = {
        "_meta": {
            "master_songs": len(known_ids),
            "mapped": len(result["mapping"]),
            "missing": result["missing"],
            "wiki_only": len(result["extra"]),
        },
        "map": {str(k): result["mapping"][k] for k in sorted(result["mapping"])},
    }
    cfg.lyric_map.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return f"{len(result['mapping'])}/{len(known_ids)} songs mapped"


def _classify(cfg: IngestConfig) -> str:
    import collections

    from .source.relevance import classify_catalog

    rows = json.loads(cfg.events_index.read_text(encoding="utf-8"))
    classify_catalog(rows)
    cfg.events_index.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    dist = collections.Counter(r.get("plot_weight") for r in rows)
    return f"{len(rows)} events: {dict(dist)}"


def _build_index(cfg: IngestConfig) -> str:
    from .query.derived_index import build_index_file

    p = build_index_file(
        cfg.story_root, events_index_path=cfg.events_index, out_path=cfg.derived_index
    )
    return f"wrote {p}"


def _record_state(cfg: IngestConfig) -> str:
    new_ids = update_ingest_state(cfg.events_index, cfg.state_path)
    return f"{len(new_ids)} new event(s): {new_ids}" if new_ids else "no new events"


def _summarize(cfg: IngestConfig) -> str:
    from .localcli import summarize

    summarize(
        story_root=cfg.story_root,
        cache=cfg.summaries_cache,
        limit=cfg.batch_limit,
        include_unit_stories=False,
        skip_existing=True,
        model=cfg.model,
        ollama=False,
        ollama_url="http://localhost:11434/v1",
    )
    return f"summaries cache at {cfg.summaries_cache}"


def _conclusions(cfg: IngestConfig) -> str:
    from .localcli import conclusions

    conclusions(
        summaries=cfg.summaries_cache,
        cache=cfg.conclusions_cache,
        limit=cfg.batch_limit,
        skip_existing=True,
        model=cfg.model,
    )
    return f"conclusions cache at {cfg.conclusions_cache}"


def _resonance(cfg: IngestConfig) -> str:
    from .localcli import resonance

    resonance(
        events_index=cfg.events_index,
        summaries=cfg.summaries_cache,
        page_map=cfg.lyric_map,
        conclusions=cfg.conclusions_cache,
        cache=cfg.resonance_cache,
        limit=cfg.batch_limit,
        skip_existing=True,
        model=cfg.model,
    )
    return f"resonance cache at {cfg.resonance_cache}"


def build_pipeline(cfg: IngestConfig) -> list[Step]:
    """The ordered step list for ``sekai ingest``.

    Required (abort on failure): the ones the rest of the run depends on, plus the
    cheap local rebuilds. Optional (log and continue): every network side-quest
    and all of Tier 2 — a scheduled run must still land Tier 1 when Sekaipedia is
    down or the LLM key is missing.
    """
    steps: list[Step] = [Step("fetch", lambda: _fetch_events(cfg), required=True)]
    if cfg.unit_stories:
        steps.append(Step("fetch-unit-stories", lambda: _fetch_unit_stories(cfg), required=False))
    if cfg.cards:
        steps.append(Step("fetch-card-stories", lambda: _fetch_cards(cfg), required=False))
    if cfg.areas:
        steps.append(Step("fetch-area-conversations", lambda: _fetch_areas(cfg), required=False))
    steps += [
        Step("link-content", lambda: _link_content(cfg), required=False),
        Step("build-lyric-map", lambda: _build_lyric_map(cfg), required=False),
        Step("classify", lambda: _classify(cfg), required=True),
        Step("build-index", lambda: _build_index(cfg), required=True),
        Step("record-state", lambda: _record_state(cfg), required=True),
    ]
    if cfg.with_llm:
        steps += [
            Step("summarize", lambda: _summarize(cfg), required=False, tier=2),
            Step("conclusions", lambda: _conclusions(cfg), required=False, tier=2),
            Step("resonance", lambda: _resonance(cfg), required=False, tier=2),
        ]
    return steps


def run_ingest(cfg: IngestConfig, *, log: Callable[[str], None] = print) -> IngestReport:
    """Build + run the pipeline for ``cfg``."""
    return run_steps(build_pipeline(cfg), log=log)
