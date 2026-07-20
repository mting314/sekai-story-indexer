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


def test_glossary_bridge_enables_cross_lingual_query(tmp_path):
    # JP corpus, EN question -> should still retrieve via the glossary bridge
    d = tmp_path / "story" / "nightcord" / "event" / "0002-x"
    d.mkdir(parents=True)
    (d / "01.md").write_text("# 1\n\nまふゆ: わたしは朝比奈まふゆだよ。\n", encoding="utf-8")
    idx = [{"event_id": 2, "arc_slug": "0002-x", "indexed": True, "unit": "nightcord"}]
    glossary = {"characters": {"朝比奈まふゆ": "Mafuyu Asahina"}}
    eng = build_local_engine(tmp_path / "story", idx, glossary)
    # bare English given name must reach the Japanese scene
    r = eng.query("What happens to Mafuyu?")
    assert r["citations"], "glossary bridge should let an EN name hit JP text"
    assert r["citations"][0]["arc_id"] == "0002-x"


def test_scoped_query_falls_back_to_opening_when_no_lexical_overlap():
    eng = build_local_engine(SAMPLE_STORY, SAMPLE_INDEX)
    # koha1 scopes to 0006-lyric (indexed); gibberish content words won't match,
    # so it should still return that event's opening scenes, not empty.
    r = eng.query("koha1 zzqqxx")
    assert r["scope"]["arc_id"] == "0006-lyric"
    assert r["citations"], "scoped query should fall back to opening scenes"
    assert all(c["arc_id"] == "0006-lyric" for c in r["citations"])


def test_scoped_single_event_returns_whole_event_not_topk(tmp_path):
    # 8-episode event; a cross-lingual "climax" query (no JP overlap) must return
    # the WHOLE event (incl. the finale), not the first k episodes.
    d = tmp_path / "story" / "more_more_jump" / "event" / "0092-x"
    d.mkdir(parents=True)
    for n in range(1, 9):
        (d / f"{n:02d}.md").write_text(f"# {n}\n\nあいり: これは{n}話だよ。\n", encoding="utf-8")
    idx = [{"event_id": 92, "arc_slug": "0092-x", "indexed": True,
            "unit": "more_more_jump", "nickname": "airi9"}]
    eng = build_local_engine(tmp_path / "story", idx)
    r = eng.query("what happens at the climax", arc_ids=("0092-x",))
    episodes = {c["episode"] for c in r["citations"]}
    assert len(r["citations"]) == 8, "whole event should reach the answer"
    assert any("08" in e for e in episodes), "the finale episode must not be dropped"


def test_budget_cover_keeps_head_and_tail():
    eng = build_local_engine(SAMPLE_STORY, SAMPLE_INDEX)

    class _Fake:
        text = "x" * 100

    eng.nodes = [_Fake() for _ in range(10)]
    cover = eng._budget_cover(list(range(10)), 350)
    assert 0 in cover and 9 in cover  # opening AND finale survive
    assert cover == sorted(cover)  # reading order preserved
    assert len(cover) < 10  # middle dropped under budget
