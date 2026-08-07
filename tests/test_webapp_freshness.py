"""Ingestion freshness on the serve side: `/api/events` status flags, the
mtime-driven cache invalidation that lets a live server pick up an ingest run,
and the `/api/admin/reload` escape hatch.

Offline: everything is pointed at tmp files / the sample corpus.
"""

import importlib
import json
import os
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def srv(tmp_path, monkeypatch):
    """A freshly reloaded server module with every on-disk artifact under tmp_path."""
    monkeypatch.setenv("SEKAI_QUERY_BACKEND", "local")
    monkeypatch.setenv("SEKAI_STORY_ROOT", str(REPO / "sample" / "story"))
    monkeypatch.setenv("SEKAI_EVENTS_INDEX", str(tmp_path / "events_index.json"))
    monkeypatch.setenv("SEKAI_SUMMARIES_CACHE", str(tmp_path / "summaries_cache.json"))
    monkeypatch.setenv("SEKAI_INGEST_STATE", str(tmp_path / "ingest_state.json"))
    monkeypatch.delenv("SEKAI_ADMIN_TOKEN", raising=False)

    from webapp import server

    importlib.reload(server)
    return server


def _write_index(tmp_path, rows):
    (tmp_path / "events_index.json").write_text(json.dumps(rows), encoding="utf-8")


def _events(srv, tmp_path, rows, **state):
    """Serve ``rows`` as the timeline (bypassing the live master DB) and read
    ``/api/events`` back."""
    _write_index(tmp_path, rows)
    if state:
        (tmp_path / "ingest_state.json").write_text(json.dumps(state), encoding="utf-8")
    srv.load_events = lambda: rows
    return {r["event_id"]: r for r in TestClient(srv.app).get("/api/events").json()}


# --- summary_status -------------------------------------------------------


def test_summary_status_reflects_the_two_tiers(srv, tmp_path):
    (tmp_path / "summaries_cache.json").write_text(
        json.dumps({"EVENT|0001-a": {"content": "…"}}), encoding="utf-8"
    )
    rows = [
        {"event_id": 1, "arc_slug": "0001-a", "indexed": True},   # tier 1 + tier 2
        {"event_id": 2, "arc_slug": "0002-b", "indexed": True},   # tier 1 only
        {"event_id": 3, "arc_slug": "0003-c", "indexed": False},  # not fetched
    ]
    got = _events(srv, tmp_path, rows)
    assert got[1]["summary_status"] == "complete"
    assert got[2]["summary_status"] == "pending"
    assert got[3]["summary_status"] == "none"


def test_a_summary_without_a_transcript_is_not_complete(srv, tmp_path):
    """The caches and the corpus can disagree (e.g. a summaries cache shipped with a
    partial story tree). `indexed` wins — the app can't actually answer yet."""
    (tmp_path / "summaries_cache.json").write_text(
        json.dumps({"EVENT|0001-a": {"content": "…"}}), encoding="utf-8"
    )
    rows = [{"event_id": 1, "arc_slug": "0001-a", "indexed": False}]
    assert _events(srv, tmp_path, rows)[1]["summary_status"] == "none"


def test_no_summaries_cache_means_everything_indexed_is_pending(srv, tmp_path):
    rows = [{"event_id": 1, "arc_slug": "0001-a", "indexed": True}]
    assert _events(srv, tmp_path, rows)[1]["summary_status"] == "pending"


def test_annotation_does_not_mutate_the_cached_timeline_rows(srv, tmp_path):
    """`load_events()` is a cached snapshot of the source; annotations are per-request."""
    rows = [{"event_id": 1, "arc_slug": "0001-a", "indexed": True}]
    _events(srv, tmp_path, rows)
    assert "summary_status" not in rows[0] and "is_new" not in rows[0]


# --- is_new ---------------------------------------------------------------


def test_is_new_from_the_ingest_state_first_seen(srv, tmp_path):
    import time

    now = time.time()
    rows = [{"event_id": 1, "arc_slug": "0001-a", "indexed": True},
            {"event_id": 2, "arc_slug": "0002-b", "indexed": True}]
    got = _events(
        srv, tmp_path, rows,
        first_seen={"1": now - 3600, "2": now - 60 * 86400},  # 1h ago vs 60 days ago
    )
    assert got[1]["is_new"] is True
    assert got[2]["is_new"] is False


def test_is_new_falls_back_to_the_release_date_without_ingest_state(srv, tmp_path):
    """A deployment that never ran the pipeline (e.g. the committed indexes) still
    gets a sensible badge from ``started_at`` (epoch millis)."""
    import time

    now_ms = time.time() * 1000
    rows = [
        {"event_id": 1, "arc_slug": "0001-a", "indexed": True, "started_at": now_ms - 86400_000},
        {"event_id": 2, "arc_slug": "0002-b", "indexed": True, "started_at": now_ms - 400 * 86400_000},
    ]
    got = _events(srv, tmp_path, rows)
    assert got[1]["is_new"] is True
    assert got[2]["is_new"] is False


def test_a_future_event_is_not_new(srv, tmp_path):
    """Sekai announces events ahead of release; an unreleased one isn't 'newly ingested'."""
    import time

    rows = [{"event_id": 1, "arc_slug": "0001-a", "indexed": False,
             "started_at": (time.time() + 7 * 86400) * 1000}]
    assert _events(srv, tmp_path, rows)[1]["is_new"] is False


def test_freshness_window_is_configurable(srv, tmp_path, monkeypatch):
    import time

    monkeypatch.setenv("SEKAI_NEW_EVENT_DAYS", "1")
    importlib.reload(srv)
    now = time.time()
    rows = [{"event_id": 1, "arc_slug": "0001-a", "indexed": True}]
    assert _events(srv, tmp_path, rows, first_seen={"1": now - 3 * 86400})[1]["is_new"] is False


def test_unreadable_ingest_state_degrades_to_the_release_date(srv, tmp_path):
    rows = [{"event_id": 1, "arc_slug": "0001-a", "indexed": True, "started_at": 0}]
    (tmp_path / "ingest_state.json").write_text("{oops", encoding="utf-8")
    got = _events(srv, tmp_path, rows)
    assert got[1]["is_new"] is False  # epoch 1970 -> long past the window, no crash


# --- hot reload -----------------------------------------------------------


def test_summaries_are_picked_up_without_a_restart(srv, tmp_path):
    """The Tier-2 backlog drains while the server runs; the badge must follow."""
    rows = [{"event_id": 1, "arc_slug": "0001-a", "indexed": True}]
    assert _events(srv, tmp_path, rows)[1]["summary_status"] == "pending"

    (tmp_path / "summaries_cache.json").write_text(
        json.dumps({"EVENT|0001-a": {"content": "…"}}), encoding="utf-8"
    )
    assert _events(srv, tmp_path, rows)[1]["summary_status"] == "complete"


def test_load_events_reruns_when_the_index_file_changes(srv, tmp_path, monkeypatch):
    """The TTL is 6h, but an ingest run must not wait it out."""
    calls = {"n": 0}

    def fake_static():
        calls["n"] += 1
        return [{"event_id": calls["n"], "arc_slug": "x", "indexed": True}]

    monkeypatch.setattr(srv, "_static_events", fake_static)
    monkeypatch.setattr(srv, "_overlay_en_titles", lambda rows: None)
    # Force the "source unreachable -> static snapshot" path (and keep the test offline).
    monkeypatch.setattr(
        "sekai_story_indexer.source.client.load_catalog_tables",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("offline")),
    )

    _write_index(tmp_path, [{"event_id": 1}])
    first = srv.load_events()
    assert srv.load_events() is first, "cached within the TTL while the file is unchanged"

    os.utime(tmp_path / "events_index.json", (10_000, 10_000))  # ingest rewrote it
    assert srv.load_events() is not first, "a new mtime invalidates the snapshot"


def test_admin_reload_clears_caches(srv, tmp_path, monkeypatch):
    rows = [{"event_id": 1, "arc_slug": "0001-a", "indexed": True}]
    _write_index(tmp_path, rows)
    # A flush must not repopulate: rebuilding here would block the caller on a live
    # master-DB pull and time out the ingest run's ping.
    monkeypatch.setattr(
        srv, "load_events", lambda: (_ for _ in ()).throw(AssertionError("must not reload"))
    )
    client = TestClient(srv.app)

    body = client.post("/api/admin/reload").json()
    assert body["status"] == "ok"
    assert {"events", "summaries", "local_engine", "derived_index"} <= set(body["cleared"])
    assert srv._local_engine["engine"] is None
    assert srv._derived_cache["index"] is None


def test_admin_reload_honours_a_configured_token(srv, tmp_path, monkeypatch):
    monkeypatch.setenv("SEKAI_ADMIN_TOKEN", "s3cret")
    _write_index(tmp_path, [{"event_id": 1, "arc_slug": "0001-a", "indexed": True}])
    client = TestClient(srv.app)

    assert client.post("/api/admin/reload").json()["status"] == "forbidden"
    assert client.post("/api/admin/reload", headers={"X-Admin-Token": "wrong"}).status_code == 403
    ok = client.post("/api/admin/reload", headers={"X-Admin-Token": "s3cret"})
    assert ok.status_code == 200 and ok.json()["status"] == "ok"


def test_local_engine_rebuilds_after_the_index_changes(srv, tmp_path):
    _write_index(tmp_path, [{"event_id": 1, "arc_slug": "0001-a", "indexed": True}])
    engine = srv._get_local_engine()
    assert srv._get_local_engine() is engine

    os.utime(tmp_path / "events_index.json", (10_000, 10_000))
    assert srv._get_local_engine() is not engine
