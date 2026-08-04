"""`--limit` / resume / fingerprint semantics of the conclusions 'refine' pass
(mocked generation, so it's keyless). Backs `sekai conclusions --limit N`.

Skips if the generation stack (pydantic-ai/google → database) isn't importable.
"""

import pytest

pytest.importorskip("pydantic_ai")
pytest.importorskip("chromadb")

from unittest.mock import patch  # noqa: E402

from sekai_story_indexer.indexer.conclusions import ConclusionExtractor  # noqa: E402

SUMMARIES = {
    "EVENT|0001-a": {"summary": "Overview:\nA.\n\nEpisode Index:\n- Episode 3: X apologizes.\n"},
    "EVENT|0002-b": {"summary": "Overview:\nB.\n\nEpisode Index:\n- Episode 4: Y confesses.\n"},
    "EVENT|0003-c": {"summary": "Overview:\nC.\n\nEpisode Index:\n- Episode 2: Z reconciles.\n"},
    "ARC|meta": "not an event, ignored",
}


def _fake_gen(self, summary):
    return (7, f"conclusion for {summary[:12]!r}")


def test_limit_stops_after_n_and_resumes(tmp_path):
    cache = str(tmp_path / "c.json")
    calls = {"n": 0}

    def counting_gen(self, summary):
        calls["n"] += 1
        return (7, f"c{calls['n']}")

    with patch.object(ConclusionExtractor, "_generate", counting_gen):
        ConclusionExtractor().extract(SUMMARIES, cache_file=cache, limit=1)
    assert calls["n"] == 1  # only ONE new conclusion despite 3 event summaries

    with patch.object(ConclusionExtractor, "_generate", counting_gen):
        final = ConclusionExtractor().extract(SUMMARIES, cache_file=cache, limit=0)
    assert calls["n"] == 3  # remaining two generated, first reused for free
    assert sum(1 for k in final if k.startswith("EVENT|")) == 3
    assert "ARC|meta" not in final  # non-event keys skipped


def test_cached_entries_reused_by_fingerprint(tmp_path):
    cache = str(tmp_path / "c.json")
    with patch.object(ConclusionExtractor, "_generate", _fake_gen):
        ConclusionExtractor().extract(SUMMARIES, cache_file=cache)

    calls = {"n": 0}

    def counting_gen(self, summary):
        calls["n"] += 1
        return (7, "x")

    # Same summaries -> same fingerprints -> nothing regenerated.
    with patch.object(ConclusionExtractor, "_generate", counting_gen):
        ConclusionExtractor().extract(SUMMARIES, cache_file=cache)
    assert calls["n"] == 0


def test_changed_summary_rederives(tmp_path):
    cache = str(tmp_path / "c.json")
    with patch.object(ConclusionExtractor, "_generate", _fake_gen):
        ConclusionExtractor().extract(SUMMARIES, cache_file=cache)

    edited = dict(SUMMARIES)
    edited["EVENT|0001-a"] = {"summary": "Overview:\nA changed.\n\nEpisode Index:\n- Episode 3: X apologizes now.\n"}
    calls = {"keys": []}

    def tracking_gen(self, summary):
        calls["keys"].append(summary[:20])
        return (7, "x")

    with patch.object(ConclusionExtractor, "_generate", tracking_gen):
        ConclusionExtractor().extract(edited, cache_file=cache)
    assert len(calls["keys"]) == 1  # only the edited event re-derived
