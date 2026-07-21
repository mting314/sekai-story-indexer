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


def test_aux_query_bridges_arbitrary_vocabulary(tmp_path):
    # A word NOT in any glossary (kinship etc.) reaches the JP scene only via the
    # translated aux_query — proving the query-translation bridge that replaced the
    # retired hand-maintained kinship map. Deterministic (aux_query passed directly).
    d = tmp_path / "story" / "leo_need" / "event" / "0001-x"
    d.mkdir(parents=True)
    (d / "01.md").write_text("# 1\n\n穂波: 弟もいるから、慣れてるだけだよ\n", encoding="utf-8")
    idx = [{"event_id": 1, "arc_slug": "0001-x", "indexed": True, "unit": "leo_need"}]
    eng = build_local_engine(tmp_path / "story", idx)
    # EN question alone -> no lexical overlap with the JP scene
    assert not eng.query("Does she have a brother?")["citations"]
    # with the JP translation supplied as aux_query -> the scene is retrieved
    r = eng.query("Does she have a brother?", aux_query="弟はいますか")
    assert r["citations"], "aux_query should bridge EN->JP retrieval"
    assert r["citations"][0]["arc_id"] == "0001-x"


def test_derived_index_is_prose_free_and_ranks():
    # Phase-1 copyright-clean hosting: the derived index must rank correctly while
    # containing NO transcript prose (see docs/derived-hosting.md).
    from sekai_story_indexer.query.derived_index import build_derived_index, score_query

    eng = build_local_engine(SAMPLE_STORY, SAMPLE_INDEX)
    idx = json.loads(json.dumps(build_derived_index(eng), ensure_ascii=False))  # hostable JSON

    # ranking parity: same top event as the full engine for a known query
    top = score_query(idx, "How does Kohane feel about singing?")
    assert top and top[0]["arc_id"] == "0006-lyric"
    assert "score" in top[0]
    assert "text" not in top[0] and "excerpt" not in top[0]  # refs only, no prose

    # NO transcript prose leaked: a real dialogue line from the sample corpus must
    # not appear anywhere in the serialized index (only derived token counts do).
    sample_md = next(SAMPLE_STORY.rglob("0006-lyric/*.md"))
    prose_line = next(
        s
        for s in (ln.strip() for ln in sample_md.read_text(encoding="utf-8").splitlines())
        if s and s != "---" and not s.startswith("#") and len(s) > 12
    )
    assert prose_line not in json.dumps(idx, ensure_ascii=False)


def test_translation_disabled_falls_back_to_empty(monkeypatch):
    from sekai_story_indexer.query import translate

    # No key -> disabled -> "" so the caller stays lexical-only (evals deterministic)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    assert translate.translation_enabled() is False
    assert translate.translate_to_japanese("Does she have a brother?") == ""
    # Explicit opt-out flag disables it even with a key present
    monkeypatch.setenv("GOOGLE_API_KEY", "x")
    monkeypatch.setenv("SEKAI_TRANSLATE_QUERY", "0")
    assert translate.translation_enabled() is False


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
