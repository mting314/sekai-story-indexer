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


def test_derived_index_gz_roundtrip_scope_and_coords(tmp_path):
    from sekai_story_indexer.query.derived_index import (
        build_derived_index,
        load_derived_index,
        score_query,
        write_derived_index,
    )

    eng = build_local_engine(SAMPLE_STORY, SAMPLE_INDEX)
    m0 = eng.nodes[0].metadata
    coords = {f"{m0.arc_id}/{m0.episode_name}": {"bundle": "b", "scenario_id": "s", "region": "jp"}}
    p = write_derived_index(build_derived_index(eng, coords), tmp_path / "d.json.gz")
    idx = load_derived_index(p)  # gzip round-trip

    refs = score_query(idx, "How does Kohane feel about singing?", arc_ids=("0006-lyric",))
    assert refs and all(r["arc_id"] == "0006-lyric" for r in refs)  # scope filter honored
    assert "source" in refs[0]  # live-fetch coords carried on refs


def test_names_absent_character_detects_stale_focus():
    """The signal that drives soft-scope fallback: is a named character actually in
    the scoped event? Precise (speaker-based), so generic word overlap can't fool it."""
    eng = build_local_engine(SAMPLE_STORY, SAMPLE_INDEX)
    # Kohane speaks in 0006-lyric -> present -> keep scope
    assert eng.names_absent_character("How does Kohane feel about singing?", ("0006-lyric",)) is False
    # Mafuyu (Nightcord) is not in the vivid_bad_squad event -> stale focus -> go global
    assert eng.names_absent_character("How does Mafuyu feel?", ("0006-lyric",)) is True
    # ...but she IS in her own event
    assert eng.names_absent_character("How does Mafuyu feel?", ("0002-marionette",)) is False
    # no character named -> None (caller uses the lexical-overlap signal instead)
    assert eng.names_absent_character("what happens at the concert", ("0006-lyric",)) is None
    # no scope -> None
    assert eng.names_absent_character("How does Kohane feel?", ()) is None


def test_episode_title_prefers_english_overlay():
    """When the event row carries an official-English episode-title overlay, the
    engine composes English citation labels; otherwise it falls back to the JP H1."""
    import copy

    idx = copy.deepcopy(SAMPLE_INDEX)
    row = next(r for r in idx if r.get("arc_slug") == "0006-lyric")
    row["episode_titles_en"] = {1: "Back-to-Back Lyrics (EN)"}
    eng = build_local_engine(SAMPLE_STORY, idx)

    ep1 = next(n for n in eng.nodes
               if n.metadata.arc_id == "0006-lyric" and n.metadata.episode_number == 1)
    assert eng._episode_title(ep1) == "1. Back-to-Back Lyrics (EN)"

    # an episode with no EN overlay keeps the JP H1
    other = next((n for n in eng.nodes
                  if n.metadata.arc_id == "0006-lyric" and n.metadata.episode_number != 1), None)
    if other is not None:
        assert not eng._episode_title(other).endswith("(EN)")


def test_episode_title_overlay_tolerates_string_keys():
    """A JSON-loaded overlay has string episode keys; the engine still matches."""
    import copy

    idx = copy.deepcopy(SAMPLE_INDEX)
    row = next(r for r in idx if r.get("arc_slug") == "0006-lyric")
    row["episode_titles_en"] = {"1": "Stringy Title (EN)"}
    eng = build_local_engine(SAMPLE_STORY, idx)
    ep1 = next(n for n in eng.nodes
               if n.metadata.arc_id == "0006-lyric" and n.metadata.episode_number == 1)
    assert eng._episode_title(ep1) == "1. Stringy Title (EN)"


def test_summarize_extractive_skim_when_no_cached_summary():
    """/summarize an event with no pre-computed summary + no LLM -> an extractive
    skim: opening line of each scene as quote parts (localized by the webapp), not
    a bare 'Summary of X' placeholder."""
    eng = build_local_engine(SAMPLE_STORY, SAMPLE_INDEX)
    eng._event_summaries = {}  # force the no-pre-summary branch
    r = eng.summarize("summarize the event", arc_ids=("0006-lyric",))
    assert r["intent"] == "summarize"
    assert "pre_summarized" not in r  # so the webapp still tries to refine/localize
    quotes = [p for p in r["answer_parts"] if p["type"] == "quote"]
    assert quotes, "extractive summarize should produce quote parts"
    # each quoted line is pinned onto its citation (so EN overlay can localize it)
    quoted_refs = {p["ref"] for p in quotes}
    assert all(c["quote"] for c in r["citations"] if c["ref"] in quoted_refs)


def test_positional_intent_detection():
    from sekai_story_indexer.query.local import _positional_intent

    assert _positional_intent("how does the event end?") == "late"
    assert _positional_intent("what's the climax?") == "late"
    assert _positional_intent("what happens at the end") == "late"
    assert _positional_intent("how does it begin?") == "early"
    assert _positional_intent("the opening scene") == "early"
    assert _positional_intent("what happens to Kohane?") is None   # not positional
    assert _positional_intent("summarize the whole event") is None
    assert _positional_intent("from the beginning to the finale") is None  # ambiguous


def test_budget_cover_bias_keeps_the_asked_end():
    eng = build_local_engine(SAMPLE_STORY, SAMPLE_INDEX)
    arc = "0006-lyric"
    idxs = sorted(
        (i for i, n in enumerate(eng.nodes) if n.metadata.arc_id == arc),
        key=lambda i: eng._sort_key(eng.nodes[i]),
    )
    assert len(idxs) >= 3
    budget = sum(len(eng.nodes[i].text) for i in idxs[:2]) - 1  # force a trim

    late = eng._budget_cover(idxs, budget, bias="late")
    early = eng._budget_cover(idxs, budget, bias="early")
    assert late[-1] == idxs[-1]   # 'ending' keeps the final scene
    assert early[0] == idxs[0]    # 'beginning' keeps the first scene
    assert late != early
    # both preserved in reading order
    assert late == sorted(late, key=lambda i: eng._sort_key(eng.nodes[i]))
    assert early == sorted(early, key=lambda i: eng._sort_key(eng.nodes[i]))
    # no bias -> head + tail (both ends present)
    default = eng._budget_cover(idxs, budget)
    assert idxs[0] in default and idxs[-1] in default


def test_scoped_event_hits_positional_score_boost():
    """'late' intent ranks later scenes above earlier ones, 'early' the reverse.
    Both questions ('how does it end' / '...begin') add no JP overlap, so with the
    same aux token their base scores are identical — only the boost direction
    differs, making the effect isolatable and deterministic."""
    eng = build_local_engine(SAMPLE_STORY, SAMPLE_INDEX)
    arc = "0006-lyric"
    ordered = sorted(
        (n for n in eng.nodes if n.metadata.arc_id == arc), key=eng._sort_key
    )
    assert len(ordered) >= 3
    first, last = ordered[0], ordered[-1]
    # a token present in BOTH the first and last scene, so both have non-zero base
    fi, li = eng.nodes.index(first), eng.nodes.index(last)
    tok = next(t for t in eng._tf[li] if t in eng._idf and t in eng._tf[fi])

    def scores(question):
        return {id(n): s for n, s in eng._scoped_event_hits(question, None, arc, aux_query=tok)}

    late = scores("how does it end")     # -> 'late'  (finale weighted up)
    early = scores("how does it begin")  # -> 'early' (opening weighted up)
    assert late[id(last)] > early[id(last)]     # finale scores higher under 'late'
    assert early[id(first)] > late[id(first)]   # opening scores higher under 'early'
