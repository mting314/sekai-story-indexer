"""End-to-end API tests through the real FastAPI app (local backend, sample data).

Skips if fastapi isn't installed (it's in the optional [web] extra).
"""

import os
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def client():
    import json

    os.environ["SEKAI_QUERY_BACKEND"] = "local"
    os.environ["SEKAI_STORY_ROOT"] = str(REPO / "sample" / "story")
    os.environ["SEKAI_EVENTS_INDEX"] = str(REPO / "sample" / "events_index.json")
    # import after env is set (module reads backend at import time)
    import importlib

    from webapp import server as server_module

    importlib.reload(server_module)
    # Pin the timeline/events source to the sample so tests are deterministic and
    # offline (load_events() otherwise hits the live master DB, whose arc slugs
    # differ from the sample the engine indexes). In prod both are the same source.
    sample_events = json.loads((REPO / "sample" / "events_index.json").read_text())
    server_module.load_events = lambda: sample_events
    return TestClient(server_module.app)


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["backend"] == "local"


def test_index_html_served(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "Sekai" in r.text


def test_events_endpoint(client):
    rows = client.get("/api/events").json()
    assert len(rows) >= 3
    assert {"nickname", "unit", "indexed"} <= set(rows[0])


def test_query_open_question(client):
    r = client.post("/api/query", json={"question": "How does Kohane feel about singing?"})
    body = r.json()
    assert body["error"] is None
    assert body["citations"][0]["arc_id"] == "0006-lyric"


def test_query_nickname_scoping(client):
    r = client.post("/api/query", json={"question": "What happens in koha1?"})
    assert r.json()["scope"]["arc_id"] == "0006-lyric"


def test_query_returns_quotes_and_excerpts(client):
    body = client.post(
        "/api/query", json={"question": "How does Kohane feel about singing?"}
    ).json()
    quotes = [p for p in body["answer_parts"] if p["type"] == "quote"]
    assert quotes, "expected at least one clickable quote part"
    # every quote references a citation that carries a full excerpt for the sidebar
    refs = {c["ref"] for c in body["citations"]}
    assert all(p["ref"] in refs for p in quotes)
    top = body["citations"][0]
    assert top["excerpt"] and top["quote"]


def test_query_not_indexed_event(client):
    r = client.post("/api/query", json={"question": "Tell me about akito1"})
    assert "not indexed" in r.json()["answer"].lower()


def test_clarify_gate_does_not_misfire_on_sample(client):
    # existing sample questions are unambiguous -> must never return a clarify turn
    for q in ["How does Kohane feel about singing?", "What happens in koha1?",
              "Why has Mafuyu disappeared?"]:
        assert client.post("/api/query", json={"question": q}).json()["backend"] != "clarify"


def test_clarify_gate_fires_on_ambiguous_reference(client, monkeypatch):
    # inject a colliding fixture (title event + character with a multi-event arc),
    # since the 3-event sample can't collide naturally.
    from webapp import server as server_module

    events = [
        {"event_id": 30, "arc_slug": "0030-rise", "name": "Rise as One",
         "nickname": "hona2", "focus_character_id": 20, "indexed": True},
        {"event_id": 12, "arc_slug": "0012-warm", "name": "A Warm Welcome",
         "nickname": "hona1", "focus_character_id": 20, "indexed": True},
    ]
    monkeypatch.setattr(server_module, "load_events", lambda: events)
    monkeypatch.setattr(server_module, "_characters_meta", lambda: {"20": {"en": "Honami"}})

    # Honami has two focus events -> "Honami's story" is ambiguous.
    body = client.post("/api/query", json={"question": "summarize Honami's story"}).json()
    assert body["backend"] == "clarify"
    assert body["options"], "clarify turn should carry structured options"


def test_referenced_arcs_unions_multiple_nicknames():
    # a comparison must scope to BOTH stories, not lock to the first nickname
    from webapp import server
    events = [{"nickname": "koha1", "arc_slug": "0006-lyric"},
              {"nickname": "mafu1", "arc_slug": "0002-marionette"}]
    assert server._referenced_arcs("compare koha1 and mafu1", events) == \
        ["0006-lyric", "0002-marionette"]


def test_referenced_arcs_empty_on_topic_switch():
    # a genuine topic switch (no nickname) resolves no scope -> not locked to prior arc
    from webapp import server
    events = [{"nickname": "koha1", "arc_slug": "0006-lyric"}]
    assert server._referenced_arcs("what about Mafuyu's disappearance?", events) == []


def _parse_sse(raw: str) -> list[dict]:
    import json
    return [json.loads(line[len("data: "):]) for line in raw.splitlines()
            if line.startswith("data: ")]


def test_query_stream_emits_meta_deltas_and_done(client):
    with client.stream("POST", "/api/query/stream",
                       json={"question": "How does Kohane feel about singing?"}) as r:
        assert r.status_code == 200
        assert "text/event-stream" in r.headers["content-type"]
        events = _parse_sse(b"".join(r.iter_bytes()).decode())
    types = [e["type"] for e in events]
    assert types[0] == "meta" and types[-1] == "done"
    assert "delta" in types  # progressive text
    done = events[-1]
    assert done["citations"][0]["arc_id"] == "0006-lyric"
    # streamed deltas reconstruct the answer
    streamed = "".join(e["text"] for e in events if e["type"] == "delta")
    assert streamed.strip()


def _pin_sample_events(monkeypatch):
    """In tests load_events() hits the live master DB (slugs differ from the sample
    the engine uses); pin it to the sample so focus + engine agree, as in prod."""
    import json as _json

    from webapp import server
    events = _json.loads((REPO / "sample" / "events_index.json").read_text())
    monkeypatch.setattr(server, "load_events", lambda: events)


def test_session_focus_carries_scope_on_followup(client, monkeypatch):
    # Turn 1 scopes to koha1; turn 2 is a pronoun follow-up naming no entity — the
    # session focus must carry the scope so it stays on 0006-lyric.
    from webapp import server
    _pin_sample_events(monkeypatch)
    server._SESSIONS.clear("t-focus")
    r1 = client.post("/api/query", json={"question": "What happens in koha1?", "session_id": "t-focus"})
    assert r1.json()["scope"]["arc_id"] == "0006-lyric"
    # focus character is seeded from the resolved event (koha1 -> Kohane, id 9),
    # even though the nickname string doesn't name her — needed for later clarify.
    assert r1.json().get("focus", {}).get("character_id") == 9
    r2 = client.post("/api/query", json={
        "question": "what happens at the climax of that story?", "session_id": "t-focus",
    })
    body = r2.json()
    assert body.get("scope", {}).get("arc_id") == "0006-lyric"  # carried, not global
    assert body.get("focus", {}).get("arcs") == ["0006-lyric"]


def test_session_focus_resets_on_topic_switch(client, monkeypatch):
    from webapp import server
    _pin_sample_events(monkeypatch)
    server._SESSIONS.clear("t-switch")
    client.post("/api/query", json={"question": "What happens in koha1?", "session_id": "t-switch"})
    # switch to a different explicit event -> focus follows the new topic
    r = client.post("/api/query", json={"question": "What happens in mafu1?", "session_id": "t-switch"})
    assert r.json()["scope"]["arc_id"] == "0002-marionette"
    assert r.json().get("focus", {}).get("arcs") == ["0002-marionette"]


def test_finalize_citations_keeps_only_referenced_and_renumbers():
    from webapp import server
    cits = [
        {"ref": i, "arc_id": "0188-x", "excerpt": f"# ep{i}\n\nline about topic {i}\n"}
        for i in range(1, 9)
    ]
    nl = "She reaches out to them [8]. Earlier she hesitated [3]."
    nl2, kept = server._finalize_citations(nl, cits)
    assert [c["ref"] for c in kept] == [1, 2]  # renumbered in first-cited order
    assert kept[0]["arc_id"] == "0188-x"
    assert "[1]" in nl2 and "[2]" in nl2 and "[8]" not in nl2 and "[3]" not in nl2
    # each kept citation pins a supporting line from its own excerpt
    assert kept[0]["quote"].startswith("line about topic 8")


def test_finalize_citations_noop_when_nothing_cited():
    from webapp import server
    cits = [{"ref": 1, "arc_id": "a", "excerpt": "x"}]
    nl = "An answer with no citations."
    nl2, kept = server._finalize_citations(nl, cits)
    assert nl2 == nl and kept == cits  # don't blank sources when model didn't cite


def test_finalize_citations_attaches_official_en_quote(monkeypatch):
    from webapp import server

    jp = "穂波: 弟もいるから"
    monkeypatch.setattr(server, "_official_en_map", lambda: {jp: "Honami: I have a younger brother too"})
    cits = [{"ref": 1, "arc_id": "0001-x", "excerpt": f"# 5\n\n{jp}\n"}]
    nl = "She has a younger brother [1]."
    _nl2, kept = server._finalize_citations(nl, cits, grounding={1: jp})
    assert kept[0]["quote"] == jp  # verbatim JP source line highlighted in transcript
    assert kept[0]["quote_en"] == "Honami: I have a younger brother too"  # official EN attached


def test_finalize_citations_no_en_when_unlocalized(monkeypatch):
    from webapp import server

    monkeypatch.setattr(server, "_official_en_map", lambda: {})  # nothing localized
    cits = [{"ref": 1, "arc_id": "0001-x", "excerpt": "# 5\n\n穂波: 弟もいるから\n"}]
    _nl2, kept = server._finalize_citations("x [1]", cits, grounding={1: "穂波: 弟もいるから"})
    assert "quote_en" not in kept[0]  # JP-only fallback


def test_image_proxy_rejects_non_sekai_host(client):
    # SSRF guard: only the sekai asset CDN may be proxied.
    assert client.get("/api/img", params={"u": "https://example.com/x.png"}).status_code == 400
    assert client.get("/api/img", params={"u": "http://storage.sekai.best/x"}).status_code == 400


def test_trim_extractive_citations_keeps_only_quoted():
    from webapp import server
    result = {
        "answer_parts": [
            {"type": "text", "text": "..."},
            {"type": "quote", "ref": 2, "text": "a"},
            {"type": "quote", "ref": 5, "text": "b"},
        ],
        "citations": [{"ref": i} for i in range(1, 9)],
    }
    server._trim_extractive_citations(result)
    assert {c["ref"] for c in result["citations"]} == {2, 5}


def test_hierarchical_summaries_endpoint(client, tmp_path, monkeypatch):
    """The tiered event->episode->part tree renders from a hierarchical cache."""
    import json

    cache = {
        "EVENT|0001-x": {
            "summary": "Overview:\nAn event overview.\n\nContinuity Facts:\n- A durable fact.",
            "inputs": {"level": "event"},
        },
        "0001-x|Event|01_ep|01_ep": {
            "summary": "Overview:\nA part overview.",
            "inputs": {"level": "part"},
        },
    }
    cache_path = tmp_path / "summaries_cache.json"
    cache_path.write_text(json.dumps(cache), encoding="utf-8")
    monkeypatch.setenv("SEKAI_SUMMARIES_CACHE", str(cache_path))

    data = client.get("/api/hierarchical-summaries").json()
    assert data["counts"]["events"] == 1
    assert data["roots"] == ["event:0001-x"]
    event = data["nodes"]["event:0001-x"]
    assert event["kind"] == "event"
    assert event["summaryId"]  # has an event-tier summary
    # the part is reachable under a (synthesized) episode node
    episode_id = event["children"][0]
    assert data["nodes"][episode_id]["kind"] == "episode"


def test_hierarchical_summaries_empty_when_cache_absent(client, tmp_path, monkeypatch):
    monkeypatch.setenv("SEKAI_SUMMARIES_CACHE", str(tmp_path / "missing.json"))
    data = client.get("/api/hierarchical-summaries").json()
    assert data["roots"] == []
    assert data["counts"] == {"events": 0, "episodes": 0, "parts": 0}


def test_episode_raw_endpoint(client):
    """Raw transcript endpoint returns the H1 title + full text from the story tree."""
    data = client.get("/api/episode-raw?arc=0002-marionette&episode=01_disappearance").json()
    assert data["title"] == "1. The Captive Marionette"
    assert "Kanade" in data["text"]
    assert "# 1. The Captive Marionette" not in data["text"]  # H1 pulled into title, not duplicated


def test_episode_raw_rejects_path_traversal(client):
    data = client.get("/api/episode-raw?arc=../../etc&episode=passwd").json()
    assert data["text"] == ""


def test_summarize_intercept_serves_hierarchical_cache(client, tmp_path, monkeypatch):
    """'summarize <nickname>' returns the hierarchical event summary (summaries_cache.json
    EVENT|<arc>), not the retired local event_summaries.json."""
    import json

    from webapp import server as server_module

    monkeypatch.setattr(
        server_module, "load_events",
        lambda: [{"nickname": "test1", "arc_slug": "0999-testarc",
                  "name": "Test Event", "unit": "leo_need", "focus_character_id": 1}],
    )
    monkeypatch.setattr(server_module, "_characters_meta", lambda: {})
    cache = {"EVENT|0999-testarc": {
        "summary": "Overview:\nHierarchical event summary.\n\nEpisode Index:\n- Episode 1: a beat.",
        "inputs": {"level": "event"},
    }}
    cp = tmp_path / "summaries_cache.json"
    cp.write_text(json.dumps(cache), encoding="utf-8")
    monkeypatch.setenv("SEKAI_SUMMARIES_CACHE", str(cp))

    body = client.post("/api/query", json={"question": "summarize test1"}).json()
    assert body["backend"] == "summary"
    assert "Hierarchical event summary" in body["answer"]
    assert "Episode Index" in body["answer"]


# --- Chat slash commands -----------------------------------------------------

_MARION = {
    "nickname": "marion1", "arc_slug": "0002-marionette", "name": "Marionette",
    "unit": "nightcord", "focus_character": "Kanade Yoisaki", "focus_character_id": 17,
    "song_title": "Marionette Song", "song_composer": "MaruMaru", "song_lyricist": "LyriCo",
}


def _mock_marion(server_module, monkeypatch):
    monkeypatch.setattr(server_module, "load_events", lambda: [_MARION])
    monkeypatch.setattr(server_module, "_characters_meta", lambda: {"17": {"en": "Kanade"}})


def test_command_help(client):
    body = client.post("/api/command", json={"command": "/help"}).json()
    assert body["backend"] == "command"
    assert "summarize" in body["answer"] and "lines" in body["answer"]


def test_command_unknown(client):
    body = client.post("/api/command", json={"command": "/bogus"}).json()
    assert "Unknown command" in body["answer"]


def test_command_summarize_uses_hierarchical(client, tmp_path, monkeypatch):
    import json

    from webapp import server as server_module

    _mock_marion(server_module, monkeypatch)
    cache = {"EVENT|0002-marionette": {"summary": "Overview:\nMarionette event summary.",
                                       "inputs": {"level": "event"}}}
    cp = tmp_path / "summaries_cache.json"
    cp.write_text(json.dumps(cache), encoding="utf-8")
    monkeypatch.setenv("SEKAI_SUMMARIES_CACHE", str(cp))
    body = client.post("/api/command", json={"command": "/summarize marion1"}).json()
    assert body["backend"] == "summary"
    assert "Marionette event summary" in body["answer"]


def test_command_lines_counts_from_story_files(client, monkeypatch):
    from webapp import server as server_module

    _mock_marion(server_module, monkeypatch)
    body = client.post("/api/command", json={"command": "/lines marion1"}).json()
    assert "episodes" in body["answer"] and "lines" in body["answer"]
    assert "Episode 1" in body["answer"]


def test_command_song(client, monkeypatch):
    from webapp import server as server_module

    _mock_marion(server_module, monkeypatch)
    body = client.post("/api/command", json={"command": "/song marion1"}).json()
    assert "Marionette Song" in body["answer"] and "MaruMaru" in body["answer"]


def test_command_scope_then_clear(client, monkeypatch):
    from webapp import server as server_module

    _mock_marion(server_module, monkeypatch)
    scoped = client.post("/api/command",
                         json={"command": "/scope marion1", "session_id": "cmd-sess"}).json()
    assert scoped.get("focus", {}).get("arcs") == ["0002-marionette"]
    cleared = client.post("/api/command",
                          json={"command": "/clear", "session_id": "cmd-sess"}).json()
    assert cleared["focus"] is None


def test_commands_catalog(client):
    cmds = client.get("/api/commands").json()
    names = {c["command"] for c in cmds}
    assert {"help", "summarize", "lines", "song", "scope", "clear"} <= names
    assert all({"command", "args", "desc"} <= set(c) for c in cmds)


def test_command_summarize_terse_char_number(client, tmp_path, monkeypatch):
    """'/summarize minori 7' resolves via the terse '<character> <N>' form."""
    import json

    from webapp import server as server_module

    monkeypatch.setattr(server_module, "load_events", lambda: [{
        "nickname": "mino7", "arc_slug": "0209-x", "name": "Minori Event",
        "unit": "more_more_jump", "focus_character_id": 5, "focus_index": 7,
    }])
    monkeypatch.setattr(server_module, "_characters_meta", lambda: {"5": {"en": "Minori Hanasato"}})
    cache = {"EVENT|0209-x": {"summary": "Overview:\nMinori's frontline.", "inputs": {"level": "event"}}}
    cp = tmp_path / "summaries_cache.json"
    cp.write_text(json.dumps(cache), encoding="utf-8")
    monkeypatch.setenv("SEKAI_SUMMARIES_CACHE", str(cp))
    body = client.post("/api/command", json={"command": "/summarize minori 7"}).json()
    assert body["backend"] == "summary"
    assert "Minori's frontline" in body["answer"]


# --- Live-scene fetch (derived-index public deploy: fetch, don't rehost) ------

def test_fetch_scene_live_renders_from_injected_fetcher():
    from webapp import server

    scenario = {"TalkData": [
        {"WindowDisplayName": "穂波", "Body": "弟もいるから"},
        {"WindowDisplayName": "", "Body": "……"},
    ]}
    out = server._fetch_scene_live(
        {"bundle": "b", "scenario_id": "s", "region": "jp"}, fetch=lambda ab, sid: scenario
    )
    assert out["text"] == "穂波: 弟もいるから\n……"


def test_scene_live_endpoint_fetches_then_empty_for_unknown(client, monkeypatch):
    from sekai_story_indexer.source import client as sclient
    from webapp import server

    monkeypatch.setattr(
        server, "_scene_sources",
        lambda: {"0001-x/05_y": {"bundle": "b", "scenario_id": "s", "region": "jp"}},
    )
    monkeypatch.setattr(sclient, "en_event_scenario", lambda ab, sid: {})  # not localized
    monkeypatch.setattr(
        sclient, "event_scenario",
        lambda ab, sid: {"TalkData": [{"WindowDisplayName": "A", "Body": "hi"}]},
    )
    r = client.get("/api/scene?arc=0001-x&episode=05_y").json()
    assert "A: hi" in r["text"] and r["region"] == "jp"  # EN absent -> JP fallback
    # unknown scene -> empty (no coords, no fetch)
    assert client.get("/api/scene?arc=9999-z&episode=00_none").json()["text"] == ""


def test_scene_live_prefers_english(client, monkeypatch):
    from sekai_story_indexer.source import client as sclient
    from webapp import server

    monkeypatch.setattr(
        server, "_scene_sources",
        lambda: {"0001-x/05_y": {"bundle": "b", "scenario_id": "s", "region": "jp"}},
    )
    monkeypatch.setattr(
        sclient, "en_event_scenario",
        lambda ab, sid: {"TalkData": [{"WindowDisplayName": "Saki", "Body": "hello"}]},
    )
    monkeypatch.setattr(sclient, "event_scenario", lambda ab, sid: {"TalkData": [{"Body": "JP"}]})
    r = client.get("/api/scene?arc=0001-x&episode=05_y").json()
    assert r["region"] == "en" and "Saki: hello" in r["text"]  # EN preferred over JP


def test_episode_raw_prefers_en_sidecar(client, tmp_path, monkeypatch):
    from webapp import server

    d = tmp_path / "leo_need" / "event" / "0001-x"
    d.mkdir(parents=True)
    (d / "01_y.md").write_text("# 1. T\n\n穂波: こんにちは\n", encoding="utf-8")
    (d / "01_y.md.en").write_text("# 1. T\n\nHonami: Hello\n", encoding="utf-8")
    monkeypatch.setenv("SEKAI_STORY_ROOT", str(tmp_path))
    r = server.episode_raw(arc="0001-x", episode="01_y")
    assert r["region"] == "en" and "Honami: Hello" in r["text"]
    # remove the EN sidecar -> falls back to JP
    (d / "01_y.md.en").unlink()
    r2 = server.episode_raw(arc="0001-x", episode="01_y")
    assert r2["region"] == "jp" and "穂波: こんにちは" in r2["text"]


def test_query_derived_returns_scene_refs_without_prose(monkeypatch):
    from webapp import server
    from webapp.server import QueryRequest

    idx = {
        "scenes": [{
            "id": 0, "arc_id": "0006-x", "episode": "01_y", "unit": "vivid_bad_squad",
            "label": "VBS — Event [koha1] · Ep 1", "nickname": "koha1",
            "source": {"bundle": "b", "scenario_id": "s", "region": "jp"},
            "tf": {"kohane": 1, "sing": 1},
        }],
        "idf": {"kohane": 2.0, "sing": 2.0}, "expansions": [],
    }
    monkeypatch.setattr(server, "_derived_index", lambda: idx)
    res = server._query_derived(QueryRequest(question="how does kohane sing"))
    assert res["backend"] == "derived"
    assert res["citations"] and res["citations"][0]["source"]["bundle"] == "b"
    assert all("excerpt" not in c and "quote" not in c for c in res["citations"])  # no prose
    assert res["answer"]  # a framing answer is produced


def test_event_summaries_map_reads_hierarchical_not_legacy(tmp_path, monkeypatch):
    """The full-backend/metadata excerpt map now comes from the hierarchical cache
    (EVENT| tier), not the retired event_summaries.json."""
    import json

    from webapp import server

    cache = {
        "EVENT|0002-marionette": {"summary": "Marionette overview."},
        "EPISODE|0002-marionette|01": {"summary": "ep — ignored"},  # non-EVENT tier skipped
    }
    cp = tmp_path / "summaries_cache.json"
    cp.write_text(json.dumps(cache), encoding="utf-8")
    monkeypatch.setenv("SEKAI_SUMMARIES_CACHE", str(cp))
    m = server._event_summaries_map()
    assert m == {"0002-marionette": {"summary": "Marionette overview."}}


def test_event_summaries_map_cached_then_invalidated_on_mtime(tmp_path, monkeypatch):
    """Cached per query (same object on re-call), but a re-ingest (new mtime) is
    picked up without a restart."""
    import json
    import os

    from webapp import server

    cp = tmp_path / "summaries_cache.json"
    cp.write_text(json.dumps({"EVENT|0001-x": {"summary": "v1"}}), encoding="utf-8")
    monkeypatch.setenv("SEKAI_SUMMARIES_CACHE", str(cp))
    monkeypatch.setattr(server, "_event_summaries_cache", {"key": object(), "map": {}})

    m1 = server._event_summaries_map()
    assert m1 == {"0001-x": {"summary": "v1"}}
    assert server._event_summaries_map() is m1  # cache hit: same object, no re-parse

    # re-ingest: new content + bumped mtime -> invalidated, picks up v2
    cp.write_text(json.dumps({"EVENT|0001-x": {"summary": "v2"}}), encoding="utf-8")
    st = cp.stat()
    os.utime(cp, (st.st_atime, st.st_mtime + 10))
    assert server._event_summaries_map() == {"0001-x": {"summary": "v2"}}


def test_no_lexical_overlap_helper():
    from webapp import server

    # only zero-score citations -> the scoped event shares nothing with the query
    assert server._no_lexical_overlap({"citations": [{"score": 0.0}, {"score": 0}]})
    assert server._no_lexical_overlap({"citations": []})
    assert server._no_lexical_overlap({})
    # any positive score means real overlap
    assert not server._no_lexical_overlap({"citations": [{"score": 0.0}, {"score": 1.2}]})


def test_soft_scope_falls_back_when_named_character_absent(monkeypatch):
    """A carried (soft) focus must not trap a topic change: if the question names a
    character who isn't in the remembered event, re-query globally. An explicitly-
    named (hard) scope never falls back."""
    from webapp import server

    class _FakeEngine:
        def query(self, q, *, unit=None, event_id=None, arc_ids=(), aux_query=""):
            if arc_ids:  # scoped: generic words still overlap, so score>0 ...
                return {"answer": "scoped", "citations": [{"ref": 1, "score": 2.0,
                        "arc_id": arc_ids[0]}], "scope": {}, "backend": "local"}
            return {"answer": "global", "citations": [{"ref": 1, "score": 5.0,
                    "arc_id": "0021-stray"}], "scope": {}, "backend": "local"}

        def names_absent_character(self, q, arc_ids):
            return True  # ... but the named character isn't in the scoped event

    monkeypatch.setattr(server, "_get_local_engine", lambda: _FakeEngine())
    req = server.QueryRequest(question="how does Kohane feel about singing")

    soft = server._local_retrieval(req, ("0076-echo-my-melody",), soft_scope=True)
    assert soft.get("soft_scope_fell_back") is True
    assert soft["citations"][0]["arc_id"] == "0021-stray"  # answered globally

    hard = server._local_retrieval(req, ("0076-echo-my-melody",), soft_scope=False)
    assert hard.get("soft_scope_fell_back") is None  # explicit scope is respected
    assert hard["answer"] == "scoped"


def test_soft_scope_keeps_scope_when_named_character_present(monkeypatch):
    """A named character who IS in the carried event keeps the scope, even though
    generic words also overlap ("when did Honami ask Kanade for help?")."""
    from webapp import server

    class _FakeEngine:
        def query(self, q, *, unit=None, event_id=None, arc_ids=(), aux_query=""):
            return {"answer": "scoped", "citations": [{"ref": 1, "score": 3.1,
                    "arc_id": arc_ids[0] if arc_ids else "global"}],
                    "scope": {}, "backend": "local"}

        def names_absent_character(self, q, arc_ids):
            return False  # Honami + Kanade are both in the scoped event

    monkeypatch.setattr(server, "_get_local_engine", lambda: _FakeEngine())
    req = server.QueryRequest(question="when did Honami ask Kanade for help")
    res = server._local_retrieval(req, ("0076-echo-my-melody",), soft_scope=True)
    assert res.get("soft_scope_fell_back") is None
    assert res["citations"][0]["arc_id"] == "0076-echo-my-melody"


def test_soft_scope_falls_back_on_no_overlap_without_named_character(monkeypatch):
    """No character named + zero lexical overlap with the carried event -> global."""
    from webapp import server

    class _FakeEngine:
        def query(self, q, *, unit=None, event_id=None, arc_ids=(), aux_query=""):
            if arc_ids:
                return {"answer": "scoped", "citations": [{"ref": 1, "score": 0.0,
                        "arc_id": arc_ids[0]}], "scope": {}, "backend": "local"}
            return {"answer": "global", "citations": [{"ref": 1, "score": 4.0,
                    "arc_id": "0021-stray"}], "scope": {}, "backend": "local"}

        def names_absent_character(self, q, arc_ids):
            return None  # no character named

    monkeypatch.setattr(server, "_get_local_engine", lambda: _FakeEngine())
    req = server.QueryRequest(question="what happens at the concert")
    res = server._local_retrieval(req, ("0076-echo-my-melody",), soft_scope=True)
    assert res.get("soft_scope_fell_back") is True
    assert res["citations"][0]["arc_id"] == "0021-stray"


def test_overlay_attaches_en_episode_titles(monkeypatch):
    from webapp import server

    monkeypatch.setattr(
        server, "_en_maps",
        lambda: ({76: "Echo My Melody"}, {}, {76: {1: "Melody EN", 2: "Savior EN"}}),
    )
    rows = [{"event_id": 76, "name": "エコー", "song_id": None, "song_title": None}]
    server._overlay_en_titles(rows)
    assert rows[0]["name"] == "Echo My Melody" and rows[0]["name_jp"] == "エコー"
    assert rows[0]["episode_titles_en"] == {1: "Melody EN", 2: "Savior EN"}


def test_derived_soft_scope_falls_back_to_global(monkeypatch):
    """Derived backend: a carried (soft) focus that finds nothing in the scoped arc
    re-queries globally; an explicit (hard) scope does not."""
    import sekai_story_indexer.query.derived_index as di
    from webapp import server

    def fake_score_query(index, q, *, top_k=5, aux_query="", arc_ids=(), unit=None):
        if arc_ids:  # scoped -> no evidence in the carried event
            return []
        return [{"arc_id": "0021-stray", "episode": "e", "unit": "vivid_bad_squad",
                 "label": "L", "nickname": None, "source": None}]

    monkeypatch.setattr(di, "score_query", fake_score_query)
    monkeypatch.setattr(server, "_derived_index", lambda: {})
    req = server.QueryRequest(question="what happens at the concert")

    soft = server._query_derived(req, ("0006-lyric",), soft_scope=True)
    assert soft.get("soft_scope_fell_back") is True
    assert soft["citations"][0]["arc_id"] == "0021-stray"

    hard = server._query_derived(req, ("0006-lyric",), soft_scope=False)
    assert hard.get("soft_scope_fell_back") is None
    assert hard["citations"] == []  # explicit scope respected, no global bleed
