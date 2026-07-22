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


@app.command("build-index")
def build_index_command(
    story_root: Path = typer.Option(Path("story")),
    out: Path = typer.Option(Path("derived_index.json.gz"), help="Output (.gz to compress)"),
):
    """Build the prose-free derived index (token counts + coords, NO transcript
    text) for copyright-clean public hosting. See docs/derived-hosting.md."""
    from .query.derived_index import build_index_file

    p = build_index_file(story_root, out_path=out)
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
