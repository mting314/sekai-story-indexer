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
    os.environ["SEKAI_QUERY_BACKEND"] = "local"
    os.environ["SEKAI_STORY_ROOT"] = str(REPO / "sample" / "story")
    os.environ["SEKAI_EVENTS_INDEX"] = str(REPO / "sample" / "events_index.json")
    # import after env is set (module reads backend at import time)
    import importlib

    from webapp import server as server_module

    importlib.reload(server_module)
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
