import json
from pathlib import Path

from sekai_story_indexer.query.local import LocalQueryEngine, build_local_engine, tokenize

REPO = Path(__file__).resolve().parent.parent
SAMPLE_STORY = REPO / "sample" / "story"
SAMPLE_INDEX = json.loads((REPO / "sample" / "events_index.json").read_text(encoding="utf-8"))


def test_tokenize_ascii_and_cjk_bigrams():
    toks = tokenize("Kohane 小豆沢")
    assert "kohane" in toks
    assert "小豆" in toks and "豆沢" in toks  # CJK bigrams


def test_engine_restricts_to_indexed_arcs():
    eng = build_local_engine(SAMPLE_STORY, SAMPLE_INDEX)
    arcs = {n.metadata.arc_id for n in eng.nodes}
    assert "0006-lyric" in arcs and "0002-marionette" in arcs
    assert "0021-stray" not in arcs  # indexed:false in the sample index


def test_retrieval_finds_relevant_scene():
    eng = build_local_engine(SAMPLE_STORY, SAMPLE_INDEX)
    r = eng.query("How does Kohane feel about singing?")
    assert r["citations"], "expected at least one citation"
    assert r["citations"][0]["arc_id"] == "0006-lyric"
    assert r["backend"] == "local"


def test_nickname_scopes_query():
    eng = build_local_engine(SAMPLE_STORY, SAMPLE_INDEX)
    r = eng.query("What happens in koha1?")
    assert r["scope"]["arc_id"] == "0006-lyric"
    assert all(c["arc_id"] == "0006-lyric" for c in r["citations"])


def test_explicit_unit_filter():
    eng = build_local_engine(SAMPLE_STORY, SAMPLE_INDEX)
    r = eng.query("How did the team become united?", unit="vivid_bad_squad")
    assert r["citations"]
    assert all(c["unit"] == "vivid_bad_squad" for c in r["citations"])


def test_not_indexed_event_is_refused_with_reason():
    eng = build_local_engine(SAMPLE_STORY, SAMPLE_INDEX)
    r = eng.query("What is akito1 about?")  # akito1 -> 0021-stray, indexed:false
    assert r["citations"] == []
    assert "not indexed" in r["answer"].lower()
    assert r["scope"]["arc_id"] == "0021-stray"


def test_answer_is_deterministic():
    eng = build_local_engine(SAMPLE_STORY, SAMPLE_INDEX)
    a = eng.query("Why did Mafuyu stop logging in?")
    b = eng.query("Why did Mafuyu stop logging in?")
    assert a == b


def test_empty_query_returns_no_match_gracefully():
    eng = LocalQueryEngine([], [])
    r = eng.query("anything")
    assert r["citations"] == []
