"""Focused event conclusions — keyless read side (pure heuristic + cache serve +
the webapp intercept wiring). No API key, no generation deps.

The premise: Project Sekai event stories close on an epilogue (a live, a reward
scene) AFTER the climax, so "the last episode" is the wrong pick. These lock in
that the conclusion is the *resolution* beat, not the coda.
"""

from sekai_story_indexer.query.conclusion import (
    derive_conclusion,
    heuristic_conclusion,
    parse_episode_index,
)

# Modelled on Leo/need's real first event: ep7 = resolution, ep8 = planetarium coda.
SUMMARY = (
    "Overview:\n"
    "Saki wants to make memories with Leo/need but hides a rising fever.\n\n"
    "Episode Index:\n"
    "- Episode 1: Saki wakes from a dream and happily attends band practice.\n"
    "- Episode 5: Saki hides her fever during practice; Shiho angrily confronts her.\n"
    "- Episode 7: The band finds Saki, Shiho apologizes and everyone promises to "
    "make memories at a steady pace.\n"
    "- Episode 8: Honami organizes a planetarium simulation so they can enjoy the "
    "stars together.\n\n"
    "Continuity Facts:\n"
    "- The band grows closer.\n"
)


def test_parse_episode_index():
    beats = parse_episode_index(SUMMARY)
    assert [n for n, _ in beats] == [1, 5, 7, 8]
    assert "planetarium" in dict(beats)[8]


def test_heuristic_picks_resolution_not_last_episode():
    climax, text = heuristic_conclusion(SUMMARY)
    assert climax == 7  # NOT 8 (the epilogue)
    assert "apologizes" in text and "promises" in text
    assert "planetarium" not in text  # the coda is not the conclusion


def test_heuristic_falls_back_to_overview_tail_without_index():
    summary = (
        "Overview:\n"
        "A conflict begins. Tension rises through the week. In the end they "
        "reconcile and vow to keep singing.\n\n"
        "Continuity Facts:\n- They grow closer.\n"
    )
    climax, text = heuristic_conclusion(summary)
    assert climax is None
    assert "reconcile" in text  # the resolution tail...
    assert "conflict begins" not in text  # ...not the setup
    assert "grow closer" not in text  # never Continuity Facts


def test_derive_prefers_cache_entry_over_heuristic():
    entry = {"climax_episode": 6, "conclusion": "Ena forgives Mafuyu and the group reunites."}
    body = derive_conclusion(SUMMARY, name="Test Event", cache_entry=entry)
    assert "forgives" in body and "Episode 6" in body
    assert "apologizes" not in body  # heuristic path was not taken


def test_derive_uses_heuristic_when_no_cache_entry():
    body = derive_conclusion(SUMMARY, name="Test Event", cache_entry=None)
    assert body.startswith("How Test Event concludes (climax in Episode 7)")
    assert "apologizes" in body


def _write_summary_cache(tmp_path, arc="0042-x"):
    import json as _json

    cache = tmp_path / "summaries_cache.json"
    cache.write_text(_json.dumps({f"EVENT|{arc}": {"summary": SUMMARY}}), encoding="utf-8")
    return cache


def test_intercept_serves_cached_conclusion(tmp_path, monkeypatch):
    """The webapp conclusion intercept prefers the pre-built conclusions cache."""
    import importlib

    from webapp import server as srv
    from webapp.sessions import Focus

    importlib.reload(srv)
    ev = {"event_id": 42, "arc_slug": "0042-x", "name": "Steady Pace", "nickname": "leo1"}
    cache = _write_summary_cache(tmp_path)
    monkeypatch.setattr(srv, "load_events", lambda: [ev])
    monkeypatch.setattr(srv, "_hierarchical_cache_path", lambda: cache)
    monkeypatch.setattr(
        srv,
        "_conclusions_map",
        lambda: {"0042-x": {"climax_episode": 7,
                            "conclusion": "Shiho apologizes and the band promises a steady pace."}},
    )

    req = srv.QueryRequest(question="what's the conclusion?")
    out = srv._scoped_event_intercept(req, Focus(arcs=("0042-x",)))
    assert out and out["intent"] == "conclusion" and out["backend"] == "summary"
    assert "steady pace" in out["answer"].lower() and "Episode 7" in out["answer"]


def test_intercept_heuristic_when_no_conclusions_cache(tmp_path, monkeypatch):
    """With no conclusions cache, the intercept still avoids the epilogue + the old
    Overview/Continuity dump, using the keyless heuristic."""
    import importlib

    from webapp import server as srv
    from webapp.sessions import Focus

    importlib.reload(srv)
    ev = {"event_id": 42, "arc_slug": "0042-x", "name": "Steady Pace"}
    cache = _write_summary_cache(tmp_path)
    monkeypatch.setattr(srv, "load_events", lambda: [ev])
    monkeypatch.setattr(srv, "_hierarchical_cache_path", lambda: cache)
    monkeypatch.setattr(srv, "_conclusions_map", lambda: {})

    req = srv.QueryRequest(question="how does it end?")
    out = srv._scoped_event_intercept(req, Focus(arcs=("0042-x",)))
    assert out and out["intent"] == "conclusion"
    assert "apologizes" in out["answer"]  # the resolution beat
    assert "planetarium" not in out["answer"]  # not the epilogue
    assert "closer" not in out["answer"]  # not Continuity Facts
