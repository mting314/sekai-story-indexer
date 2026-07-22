"""`--limit` / resume semantics of the LLM Refine event summarizer (mocked
generation, so it's keyless). Backs `sekai summarize --limit N`.

Skips if the generation stack (pydantic-ai/google → database) isn't importable.
"""

from pathlib import Path

import pytest

pytest.importorskip("pydantic_ai")
pytest.importorskip("chromadb")

from unittest.mock import patch  # noqa: E402

from sekai_story_indexer.indexer.processor import StoryProcessor  # noqa: E402
from sekai_story_indexer.indexer.summarizer import HierarchicalSummarizer  # noqa: E402
from sekai_story_indexer.story_order import load_story_order  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
SAMPLE = REPO / "sample" / "story"


def _sample_event_nodes():
    nodes = []
    for md in sorted(SAMPLE.rglob("*.md")):
        if md.name.endswith(".md.en"):
            continue
        nodes.extend(StoryProcessor.process_file(md))
    # event arcs only (NNNN-slug)
    return [n for n in nodes if n.metadata.arc_id[:4].isdigit()]


def _summarizer():
    return HierarchicalSummarizer(
        glossary=None, story_order=load_story_order(), cache_context=None
    )


def test_limit_stops_after_n_generations_and_resumes(tmp_path):
    nodes = _sample_event_nodes()
    arcs = {n.metadata.arc_id for n in nodes}
    assert len(arcs) >= 3, "sample should have >=3 event arcs"
    cache = str(tmp_path / "c.json")
    calls = {"n": 0}

    def fake_gen(self, current_text, prev_summary=None, level_name="Event"):
        calls["n"] += 1
        return f"summary {calls['n']}"

    with patch.object(HierarchicalSummarizer, "_generate_rolling_summary", fake_gen):
        _summarizer().summarize_events(nodes, cache_file=cache, limit=1)
    assert calls["n"] == 1  # only ONE new summary despite multiple uncached arcs

    # resume: cached one is reused (free), generation continues for the rest
    with patch.object(HierarchicalSummarizer, "_generate_rolling_summary", fake_gen):
        _summarizer().summarize_events(nodes, cache_file=cache, limit=0)
    assert calls["n"] == len(arcs)  # exactly the remaining arcs generated, no re-do


def test_limit_zero_generates_all(tmp_path):
    nodes = _sample_event_nodes()
    arcs = {n.metadata.arc_id for n in nodes}
    cache = str(tmp_path / "c.json")
    calls = {"n": 0}

    def fake_gen(self, current_text, prev_summary=None, level_name="Event"):
        calls["n"] += 1
        return f"summary {calls['n']}"

    with patch.object(HierarchicalSummarizer, "_generate_rolling_summary", fake_gen):
        _summarizer().summarize_events(nodes, cache_file=cache, limit=0)
    assert calls["n"] == len(arcs)


def test_skip_existing_keeps_summaries_from_another_model(tmp_path):
    """--skip-existing keeps an event that already has a summary even when its
    fingerprint no longer matches (e.g. built by a different model), so a new/local
    model fills only the gaps without clobbering existing summaries."""
    import json

    nodes = _sample_event_nodes()
    arcs = sorted({n.metadata.arc_id for n in nodes})
    cache = tmp_path / "c.json"
    # seed one arc with a stale-fingerprint summary (as if built by another model)
    cache.write_text(json.dumps(
        {f"EVENT|{arcs[0]}": {"summary": "KEEP ME", "fingerprint": "stale", "inputs": {}}}
    ))
    calls = {"n": 0}

    def fake_gen(self, current_text, prev_summary=None, level_name="Event"):
        calls["n"] += 1
        return f"new {calls['n']}"

    with patch.object(HierarchicalSummarizer, "_generate_rolling_summary", fake_gen):
        _summarizer().summarize_events(nodes, cache_file=str(cache), skip_existing=True)

    data = json.loads(cache.read_text())
    assert data[f"EVENT|{arcs[0]}"]["summary"] == "KEEP ME"  # kept, not regenerated
    assert calls["n"] == len(arcs) - 1  # only the gaps generated
