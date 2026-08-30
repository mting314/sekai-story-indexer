"""Dependency-light CLI (`sekai`) for the no-API paths: fetch, local query,
web app, and the regression eval.

Deliberately imports nothing heavy at module load (no chromadb / google), so it
runs with just `typer` + this package. Command bodies import what they need
lazily. The full RAG CLI is `indexer` (see cli.py).
"""

from __future__ import annotations

import json
from pathlib import Path

import typer

app = typer.Typer(help="Sekai story indexer — lightweight, no-API commands.")


def _events(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []


@app.command()
def fetch(
    story_root: Path = typer.Option(Path("story")),
    limit: int = typer.Option(0, help="Only the N earliest events (0 = all)"),
    event_id: list[int] = typer.Option(None),
    skip_existing: bool = typer.Option(
        False, help="Resumable: skip episodes already on disk (don't re-download)"
    ),
):
    """Download story text + build the story tree, story_order.yaml, events_index.json."""
    from .source.fetcher import fetch_and_write

    plans = fetch_and_write(
        story_root,
        limit=limit or None,
        event_ids=list(event_id) if event_id else None,
        skip_existing=skip_existing,
    )
    typer.echo(f"Fetched {len(plans)} events into {story_root}")


@app.command("fetch-unit-stories")
def fetch_unit_stories_command(story_root: Path = typer.Option(Path("story"))):
    """Fetch the units' main (formation) stories into story/<unit>/unit/…"""
    from .source.fetcher import fetch_unit_stories

    n = fetch_unit_stories(story_root)
    typer.echo(f"Wrote {n} unit-story episodes into {story_root}")


@app.command("fetch-card-stories")
def fetch_card_stories_command(
    story_root: Path = typer.Option(Path("story")),
    limit: int = typer.Option(0, help="Only the first N cards (0 = all ~1350 cards)."),
    skip_existing: bool = typer.Option(True, help="Resume: skip episodes already on disk."),
):
    """Fetch per-card side-stories into story/<unit>/card/… (2 episodes per card)."""
    from .source.fetcher import fetch_card_stories

    n = fetch_card_stories(story_root, limit=limit or None, skip_existing=skip_existing)
    typer.echo(f"Wrote {n} card-story episodes into {story_root}")


@app.command("fetch-area-conversations")
def fetch_area_conversations_command(
    story_root: Path = typer.Option(Path("story")),
    limit: int = typer.Option(0, help="Only the first N areas (0 = all)."),
    skip_existing: bool = typer.Option(True, help="Resume: skip talks already on disk."),
):
    """Fetch area conversations into story/<unit>/area/… (one file per actionSet)."""
    from .source.fetcher import fetch_area_conversations

    n = fetch_area_conversations(story_root, limit=limit or None, skip_existing=skip_existing)
    typer.echo(f"Wrote {n} area-conversation talks into {story_root}")


@app.command("link-content")
def link_content_command(
    story_root: Path = typer.Option(Path("story")),
    out: Path = typer.Option(None, help="Output path (default: content_parents.json next to story_root)"),
):
    """Build content_parents.json (card/area → parent event) so the processor can
    nest card side-stories and area conversations under their event."""
    from .source import client
    from .source.transform import (
        build_area_event_map,
        build_card_parent_map,
        build_content_parents,
    )

    out = out or (story_root.parent / "content_parents.json")
    cards_by_id = {c["id"]: c for c in client.cards()}
    card_map = build_card_parent_map(client.event_cards(), cards_by_id)
    area_map = build_area_event_map(
        client.action_sets(), client.release_conditions(), client.event_stories()
    )
    events_by_id = {e["id"]: e for e in client.events()}
    doc = build_content_parents(card_map, area_map, events_by_id)
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    ce = sum(1 for v in doc["cards"].values() if v["parent_event_id"])
    ae = sum(1 for v in doc["areas"].values() if v["parent_event_id"])
    typer.echo(
        f"wrote {out}: {len(doc['cards'])} cards ({ce} event-linked), "
        f"{len(doc['areas'])} area talks ({ae} event-linked)"
    )


@app.command("build-lyric-map")
def build_lyric_map_command(
    out: Path = typer.Option(Path("lyric_page_map.json")),
    page_size: int = typer.Option(500, help="Cargo rows per query (Cargo caps ~500)."),
):
    """Build lyric_page_map.json — map each master-DB song id to its Sekaipedia page
    (stable pageid) via a deterministic join on song_id (Cargo `songs` table).

    Our id data, not lyric text — safe to commit. Refreshable; songs with no wiki
    page are reported as `missing` (never a silent wrong-page grab). Network, no key.
    Downstream, lyric text is fetched LIVE by pageid at analysis time, never rehosted."""
    from .source import client
    from .source.transform import build_lyric_page_map

    known_ids = {m["id"] for m in client.musics() if isinstance(m.get("id"), int)}
    cargo_rows = client.sekaipedia_song_pages(page_size=page_size)
    result = build_lyric_page_map(cargo_rows, known_ids)

    payload = {
        "_meta": {
            "master_songs": len(known_ids),
            "mapped": len(result["mapping"]),
            "missing": result["missing"],
            "wiki_only": len(result["extra"]),
        },
        "map": {str(k): result["mapping"][k] for k in sorted(result["mapping"])},
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    typer.echo(
        f"Mapped {len(result['mapping'])}/{len(known_ids)} songs -> {out} "
        f"({len(result['missing'])} missing, {len(result['extra'])} wiki-only)"
    )


@app.command("build-index")
def build_index_command(
    story_root: Path = typer.Option(Path("story")),
    out: Path = typer.Option(Path("derived_index.json.gz"), help="Output (.gz to compress)"),
    all_scenes: bool = typer.Option(
        False,
        "--all-scenes",
        help="Include scenes with no live-fetch coords (they rank but cannot be opened).",
    ),
):
    """Build the prose-free derived index (token counts + coords, NO transcript
    text) for copyright-clean public hosting. See docs/derived-hosting.md."""
    from .query.derived_index import build_index_file

    p = build_index_file(story_root, out_path=out, quotable_only=not all_scenes)
    typer.echo(f"wrote {p}")


@app.command("backfill-slugs")
def backfill_slugs_command(
    story_root: Path = typer.Option(Path("story")),
    events_index: Path = typer.Option(Path("events_index.json")),
    story_order: Path = typer.Option(Path("story_order.yaml")),
):
    """Backfill existing story tree directories and files with Romanized slugs."""
    from .source.backfill_slugs import backfill_story_tree

    stats = backfill_story_tree(story_root, events_index, story_order)
    typer.echo(
        f"Backfill complete: {stats['events_updated']} event slugs updated in index, "
        f"{stats['dirs_renamed']} directories renamed, {stats['files_renamed']} files renamed, "
        f"{stats['summaries_remapped']} summary-cache keys remapped."
    )


@app.command()
def ask(
    question: str,
    story_root: Path = typer.Option(Path("story")),
    events_index: Path = typer.Option(Path("events_index.json")),
    unit: str = typer.Option("", help="Scope to a unit slug"),
):
    """Query with the local lexical engine (no API). Supports unit + nickname
    (e.g. 'kasa5') scoping. Deterministic."""
    from .query.local import build_local_engine

    engine = build_local_engine(story_root, _events(events_index))
    result = engine.query(question, unit=unit or None)
    typer.echo(result["answer"])
    for c in result["citations"]:
        typer.echo(f"  · {c['unit']} · {c['arc_id']} · {c['episode']} (score {c['score']})")


@app.command()
def classify(events_index: Path = typer.Option(Path("events_index.json"))):
    """(Re)compute plot_weight for every event in the index (heuristic; no LLM)."""
    import collections

    from .source.relevance import classify_catalog

    rows = _events(events_index)
    classify_catalog(rows)
    events_index.write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    dist = collections.Counter(r["plot_weight"] for r in rows)
    typer.echo(f"classified {len(rows)} events: {dict(dist)}")


@app.command()
def summarize(
    story_root: Path = typer.Option(Path("story")),
    cache: Path = typer.Option(Path("summaries_cache.json")),
    limit: int = typer.Option(
        0, help="Generate at most N NEW event summaries (0 = all). Cached ones are "
        "reused for free, so this is resumable + a cost knob for partial runs."
    ),
    include_unit_stories: bool = typer.Option(
        False, help="Also summarize unit-story arcs (default: event arcs only)."
    ),
    skip_existing: bool = typer.Option(
        False, help="Keep events that already have a summary even if the model/prompt "
        "changed — fill only the gaps (e.g. a local Ollama model without clobbering "
        "existing Gemini summaries)."
    ),
    model: str = typer.Option(
        "", help="Generation model to use (overrides SEKAI_INGEST_MODEL), "
        "e.g. 'qwen2.5:14b' for Ollama or 'gemini-flash-latest' for Google."
    ),
    ollama: bool = typer.Option(
        False, "--ollama", help="Route generation through a local Ollama server "
        "(sets the OpenAI-compatible provider + URL + a dummy key for you). Pair with "
        "--model <ollama-tag>."
    ),
    ollama_url: str = typer.Option(
        "http://localhost:11434/v1", help="Ollama OpenAI-compatible base URL (with --ollama)."
    ),
):
    """LLM 'Refine' event-tier summaries into the summaries cache. Defaults to the
    Google provider; use --ollama --model <tag> for a local, free, no-cap run.
    Fingerprint-cached + resumable; threads a rolling previous-event summary for
    continuity. Skips Chroma entirely."""
    import os
    import re

    # CLI overrides -> the env the generation layer reads (before it's initialized).
    if ollama:
        os.environ["SEKAI_INGEST_PROVIDER"] = "openai"
        os.environ["OPENAI_BASE_URL"] = ollama_url
        os.environ.setdefault("OPENAI_API_KEY", "ollama")  # dummy; Ollama ignores it
    if model:
        os.environ["SEKAI_INGEST_MODEL"] = model

    try:
        from .database import (
            get_chat_model_name,
            get_embedding_model_name,
            get_generation_model_name,
            get_generation_provider_name,
            initialize_ingest_settings,
        )
        from .indexer.manifest import (
            SUMMARY_CACHE_SCHEMA_VERSION,
            SummaryCacheContext,
            hash_files,
            hash_json_file,
        )
        from .indexer.parser import PARSER_VERSION
        from .indexer.processor import StoryProcessor
        from .indexer.summarizer import SUMMARIZATION_PROMPT_VERSION, HierarchicalSummarizer
        from .story_order import load_story_order
    except ImportError as exc:  # generation stack not installed
        typer.secho(
            f"`sekai summarize` needs the generation deps (pydantic-ai + google): {exc}\n"
            "Install with `uv sync`.",
            fg=typer.colors.RED,
        )
        raise typer.Exit(1) from exc

    initialize_ingest_settings()
    typer.echo(
        f"generation: {get_generation_provider_name()} · model={get_generation_model_name()}"
    )
    md_files = sorted(story_root.rglob("*.md"), key=str)  # *.md.en excluded (ends .en)
    story_order = load_story_order()  # no story_root validation (unit arcs not in yaml)
    raw_nodes = []
    for f in md_files:
        raw_nodes.extend(StoryProcessor.process_file(f))

    event_arc = re.compile(r"^\d{4}-")  # event arcs; excludes unit-story arcs (NN-…)
    nodes = raw_nodes if include_unit_stories else [
        n for n in raw_nodes if event_arc.match(n.metadata.arc_id)
    ]
    arcs = {n.metadata.arc_id for n in nodes}
    cache_dict = json.loads(cache.read_text(encoding="utf-8")) if cache.exists() else {}
    already = sum(1 for a in arcs if f"EVENT|{a}" in cache_dict)
    typer.echo(
        f"{len(arcs)} arcs in scope · {already} already cached · "
        f"limit={'all' if not limit else limit}"
    )

    glossary_path = Path("glossary.json")
    glossary = json.loads(glossary_path.read_text(encoding="utf-8")) if glossary_path.exists() else None
    cache_context = SummaryCacheContext(
        source_file_hashes=hash_files(md_files),
        parser_version=PARSER_VERSION,
        summarization_prompt_version=SUMMARIZATION_PROMPT_VERSION,
        glossary_hash=hash_json_file(glossary_path),
        chat_model=get_chat_model_name(),
        generation_provider=get_generation_provider_name(),
        generation_model=get_generation_model_name(),
        embedding_model=get_embedding_model_name(),
        summary_cache_schema_version=SUMMARY_CACHE_SCHEMA_VERSION,
    )
    summarizer = HierarchicalSummarizer(
        glossary=glossary, story_order=story_order, cache_context=cache_context
    )
    try:
        summarizer.summarize_events(
            nodes, cache_file=str(cache), limit=limit, skip_existing=skip_existing
        )
    except Exception as exc:
        msg = str(exc)
        # A rate/spend-cap stop is expected + resumable (cache saved per-event);
        # anything else is a real error and should surface with its traceback.
        if not any(s in msg for s in ("429", "RESOURCE_EXHAUSTED", "spend", "quota")):
            raise
        typer.secho(
            f"\nStopped early (API limit): {msg[:200]}\n"
            "Per-event progress is saved; re-run `sekai summarize` to resume.",
            fg=typer.colors.YELLOW,
        )
        raise typer.Exit(1) from exc

    final = json.loads(cache.read_text(encoding="utf-8"))
    have = sum(1 for a in arcs if f"EVENT|{a}" in final)
    typer.echo(f"Done. {have}/{len(arcs)} event summaries cached in {cache}.")


@app.command()
def conclusions(
    summaries: Path = typer.Option(Path("summaries_cache.json")),
    cache: Path = typer.Option(Path("conclusions_cache.json")),
    limit: int = typer.Option(
        0, help="Derive at most N NEW conclusions (0 = all). Cached ones are reused "
        "for free, so this is resumable + a cost knob for partial runs."
    ),
    skip_existing: bool = typer.Option(
        False, help="Keep conclusions that already exist even if their fingerprint "
        "changed (fill only the gaps)."
    ),
    model: str = typer.Option(
        "", help="Generation model (overrides SEKAI_INGEST_MODEL)."
    ),
):
    """Derive a focused 'how it ends' per event from the pre-built summaries — a
    cheap second LLM pass (Overview + Episode Index only) into the conclusions
    cache, served keyless by the app. Fingerprint-cached + resumable; skips Chroma.
    Run `sekai summarize` first so the summaries exist."""
    import os

    if model:
        os.environ["SEKAI_INGEST_MODEL"] = model

    try:
        from .database import (
            get_generation_model_name,
            get_generation_provider_name,
            initialize_ingest_settings,
        )
        from .indexer.conclusions import ConclusionExtractor
    except ImportError as exc:  # generation stack not installed
        typer.secho(
            f"`sekai conclusions` needs the generation deps (pydantic-ai + google): "
            f"{exc}\nInstall with `uv sync`.",
            fg=typer.colors.RED,
        )
        raise typer.Exit(1) from exc

    if not summaries.exists():
        typer.secho(
            f"No summaries cache at {summaries}. Run `sekai summarize` first.",
            fg=typer.colors.RED,
        )
        raise typer.Exit(1)

    initialize_ingest_settings()
    typer.echo(
        f"generation: {get_generation_provider_name()} · model={get_generation_model_name()}"
    )
    summaries_cache = json.loads(summaries.read_text(encoding="utf-8"))
    total = sum(1 for k in summaries_cache if k.startswith("EVENT|"))
    already = 0
    if cache.exists():
        existing = json.loads(cache.read_text(encoding="utf-8"))
        already = sum(1 for k in existing if k.startswith("EVENT|"))
    typer.echo(
        f"{total} event summaries · {already} conclusions cached · "
        f"limit={'all' if not limit else limit}"
    )

    extractor = ConclusionExtractor(
        generation_model=get_generation_model_name(),
        generation_provider=get_generation_provider_name(),
    )
    try:
        final = extractor.extract(
            summaries_cache, cache_file=str(cache), limit=limit, skip_existing=skip_existing
        )
    except Exception as exc:
        msg = str(exc)
        if not any(s in msg for s in ("429", "RESOURCE_EXHAUSTED", "spend", "quota")):
            raise
        typer.secho(
            f"\nStopped early (API limit): {msg[:200]}\n"
            "Per-event progress is saved; re-run `sekai conclusions` to resume.",
            fg=typer.colors.YELLOW,
        )
        raise typer.Exit(1) from exc

    have = sum(1 for k in final if k.startswith("EVENT|"))
    typer.echo(f"Done. {have}/{total} conclusions cached in {cache}.")


@app.command()
def resonance(
    events_index: Path = typer.Option(Path("events_index.json")),
    summaries: Path = typer.Option(Path("summaries_cache.json")),
    page_map: Path = typer.Option(Path("lyric_page_map.json")),
    conclusions: Path = typer.Option(Path("conclusions_cache.json")),
    cache: Path = typer.Option(Path("resonance_cache.json")),
    limit: int = typer.Option(0, help="Generate at most N NEW resonance notes (0 = all)."),
    skip_existing: bool = typer.Option(False),
    model: str = typer.Option("", help="Generation model (overrides SEKAI_INGEST_MODEL)."),
):
    """Derive a lyric↔story 'resonance' note per event — how the theme song mirrors
    the story's arc/ending — into resonance_cache.json. Fetches lyrics live from
    Sekaipedia (never rehosted), reuses summaries + (heuristic or cached) conclusions.
    Needs GOOGLE_API_KEY + generation deps; fingerprint-cached, resumable. (While
    waiting on credits, notes can also be populated via Claude subagents — same cache
    format, content-only fingerprint.)"""
    import os

    if model:
        os.environ["SEKAI_INGEST_MODEL"] = model
    try:
        from .database import (
            create_generation_text_agent,
            get_generation_model_name,
            initialize_ingest_settings,
        )
        from .indexer.resonance import (
            RESONANCE_SYSTEM_INSTRUCTIONS,
            ResonanceExtractor,
            assemble_resonance_inputs,
        )
        from .source.lyrics import load_page_map
    except ImportError as exc:
        typer.secho(
            f"`sekai resonance` needs the generation deps: {exc}\nInstall with `uv sync`.",
            fg=typer.colors.RED,
        )
        raise typer.Exit(1) from exc

    if not summaries.exists() or not page_map.exists():
        typer.secho(
            "Need summaries_cache.json (run `sekai summarize`) and lyric_page_map.json "
            "(run `sekai build-lyric-map`).",
            fg=typer.colors.RED,
        )
        raise typer.Exit(1)

    initialize_ingest_settings()
    typer.echo(f"generation: model={get_generation_model_name()}")
    evidx = json.loads(events_index.read_text(encoding="utf-8"))
    summ = json.loads(summaries.read_text(encoding="utf-8"))
    concl = json.loads(conclusions.read_text(encoding="utf-8")) if conclusions.exists() else None
    inputs = assemble_resonance_inputs(evidx, summ, load_page_map(page_map), conclusions=concl)
    typer.echo(f"{len(inputs)} events with summary + fetchable lyrics · limit={limit or 'all'}")

    def generate(prompt: str) -> str:
        return create_generation_text_agent(RESONANCE_SYSTEM_INSTRUCTIONS).run_sync(prompt).output

    extractor = ResonanceExtractor(generate, model=get_generation_model_name())
    try:
        final = extractor.extract(inputs, cache_file=str(cache), limit=limit, skip_existing=skip_existing)
    except Exception as exc:
        msg = str(exc)
        if not any(s in msg for s in ("429", "RESOURCE_EXHAUSTED", "spend", "quota")):
            raise
        typer.secho(
            f"\nStopped early (API limit): {msg[:200]}\nProgress saved; re-run to resume.",
            fg=typer.colors.YELLOW,
        )
        raise typer.Exit(1) from exc

    have = sum(1 for k in final if k.startswith("EVENT|"))
    typer.echo(f"Done. {have} resonance notes cached in {cache}.")


@app.command()
def ingest(
    story_root: Path = typer.Option(Path("story")),
    events_index: Path = typer.Option(Path("events_index.json")),
    skip_existing: bool = typer.Option(
        True, help="Resume: don't re-download episodes already on disk. Turn off for a "
        "full re-fetch (slow, and only needed when the source text itself changed)."
    ),
    limit_events: int = typer.Option(0, help="Only the N earliest events (0 = all)."),
    cards: bool = typer.Option(True, help="Also fetch card side-stories."),
    areas: bool = typer.Option(True, help="Also fetch area conversations."),
    unit_stories: bool = typer.Option(False, help="Also refetch the unit (formation) stories."),
    with_llm: bool = typer.Option(
        False, "--with-llm", "--summarize", help="Also run Tier 2 (summaries → conclusions → "
        "resonance). Needs GOOGLE_API_KEY + generation deps; failures are logged, not fatal."
    ),
    batch_limit: int = typer.Option(
        5, help="Tier 2 only: how many NEW items each LLM pass may generate this run. "
        "Keeps a scheduled drain under the spend cap; use 0 for 'no cap'."
    ),
    model: str = typer.Option("", help="Tier 2 generation model (overrides SEKAI_INGEST_MODEL)."),
    reload_url: str = typer.Option(
        "", help="POST here after a successful run to flush a live server's caches "
        "(e.g. http://127.0.0.1:8000/api/admin/reload)."
    ),
    state_path: Path = typer.Option(
        Path("ingest_state.json"), help="Where to record first-seen event timestamps (drives "
        "the timeline's NEW badge)."
    ),
):
    """One-shot 'catch up with the game' run: fetch → link → map → classify → index,
    then optionally the LLM passes.

    Tier 1 is keyless and always runs, so a brand-new event becomes queryable
    immediately; Tier 2 (summaries/conclusions/resonance) is rate-budgeted by
    ``--batch-limit`` and never fails the run. Safe to schedule."""
    from .ingest import IngestConfig, run_ingest

    cfg = IngestConfig(
        story_root=story_root,
        events_index=events_index,
        state_path=state_path,
        skip_existing=skip_existing,
        limit_events=limit_events,
        cards=cards,
        areas=areas,
        unit_stories=unit_stories,
        with_llm=with_llm,
        batch_limit=batch_limit,
        model=model,
    )
    report = run_ingest(cfg, log=typer.echo)
    typer.echo(report.summary())
    if report.ok and reload_url:
        typer.echo(_notify_reload(reload_url))
    raise typer.Exit(code=0 if report.ok else 1)


def _notify_reload(url: str) -> str:
    """Best-effort cache-flush ping to a running server (never fails the ingest)."""
    import os
    import urllib.error
    import urllib.request

    req = urllib.request.Request(url, data=b"", method="POST")
    token = os.environ.get("SEKAI_ADMIN_TOKEN")
    if token:
        req.add_header("X-Admin-Token", token)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 - operator-supplied URL
            return f"reload {url}: HTTP {resp.status}"
    except urllib.error.HTTPError as exc:
        # The server answered and refused — 403 (loopback-only / bad token) and 429
        # (cooldown) are actionable, so don't bury them under "not running".
        hint = {403: " — set SEKAI_ADMIN_TOKEN on both sides", 429: " — cooldown, try later"}
        return f"reload {url}: HTTP {exc.code}{hint.get(exc.code, '')}"
    except (urllib.error.URLError, OSError) as exc:
        return f"reload {url} failed (server not running?): {exc}"


@app.command("eval")
def eval_command(golden: Path = typer.Option(Path("eval/golden_local.json"))):
    """Run the local regression eval; non-zero exit on any regression."""
    from .eval.local_eval import run_golden_local

    report = run_golden_local(golden, base_dir=Path.cwd())
    typer.echo(report.summary())
    raise typer.Exit(code=0 if report.ok else 1)


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1"),
    port: int = typer.Option(8000),
    backend: str = typer.Option("local", help="'local' (no API) or 'full' (Google/Chroma)"),
    story_root: Path = typer.Option(Path("story")),
    events_index: Path = typer.Option(Path("events_index.json")),
):
    """Launch the web app (chat + event timeline)."""
    import importlib.util
    import os
    import sys

    if importlib.util.find_spec("fastapi") is None:
        typer.secho(
            "fastapi/uvicorn not installed. Run: uv sync   (or: uv pip install fastapi uvicorn)",
            fg="red",
        )
        raise typer.Exit(code=1)

    import uvicorn

    os.environ["SEKAI_QUERY_BACKEND"] = backend
    os.environ["SEKAI_STORY_ROOT"] = str(story_root)
    os.environ["SEKAI_EVENTS_INDEX"] = str(events_index)
    if os.getcwd() not in sys.path:
        sys.path.insert(0, os.getcwd())
    typer.echo(f"Serving http://{host}:{port}  (backend={backend})")
    uvicorn.run("webapp.server:app", host=host, port=port)


def main():
    app()


if __name__ == "__main__":
    main()
