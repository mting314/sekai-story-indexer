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
