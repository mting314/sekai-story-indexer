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
):
    """Download story text + build the story tree, story_order.yaml, events_index.json."""
    from .source.fetcher import fetch_and_write

    plans = fetch_and_write(
        story_root, limit=limit or None, event_ids=list(event_id) if event_id else None
    )
    typer.echo(f"Fetched {len(plans)} events into {story_root}")


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
