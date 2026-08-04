"""Lyric↔story resonance pass — provider-agnostic, keyless tests (mock generate +
mock lyric fetch). Backs `sekai resonance` and the Claude-subagent bootstrap.
"""

from unittest.mock import patch

from sekai_story_indexer.indexer.resonance import (
    ResonanceExtractor,
    assemble_resonance_inputs,
    resonance_fingerprint,
)

INPUTS = [
    {"arc": "0001-a", "name": "Ameagari", "song_title": "Stella",
     "overview": "Saki hides a fever.", "conclusion": "They reconcile.",
     "lyrics_text": "[Saki] tears / My tears", "attribution": "src"},
    {"arc": "0002-b", "name": "Marionette", "song_title": "Niigo",
     "overview": "Mafuyu can't speak.", "conclusion": "She opens up.",
     "lyrics_text": "[Mafuyu] doll / I am a doll", "attribution": "src"},
]


def _counting():
    calls = {"n": 0}
    def gen(prompt):
        calls["n"] += 1
        return f"resonance {calls['n']}"
    return calls, gen


def test_extract_limit_and_resume(tmp_path):
    cache = str(tmp_path / "r.json")
    calls, gen = _counting()
    ResonanceExtractor(gen, model="claude-subagent").extract(INPUTS, cache_file=cache, limit=1)
    assert calls["n"] == 1  # only one new note despite two inputs

    calls2, gen2 = _counting()
    final = ResonanceExtractor(gen2, model="claude-subagent").extract(INPUTS, cache_file=cache)
    assert calls2["n"] == 1  # the remaining one; first reused for free
    assert {k for k in final if k.startswith("EVENT|")} == {"EVENT|0001-a", "EVENT|0002-b"}


def test_fingerprint_is_content_only_so_model_switch_does_not_churn(tmp_path):
    cache = str(tmp_path / "r.json")
    _, gen = _counting()
    ResonanceExtractor(gen, model="claude-subagent").extract(INPUTS, cache_file=cache)

    # A different backend over identical content must NOT regenerate anything.
    def explode(prompt):
        raise AssertionError("should not regenerate on a model switch")

    ResonanceExtractor(explode, model="gemini-flash-latest").extract(INPUTS, cache_file=cache)


def test_changed_content_rederives(tmp_path):
    cache = str(tmp_path / "r.json")
    _, gen = _counting()
    ResonanceExtractor(gen, model="x").extract(INPUTS, cache_file=cache)

    edited = [dict(INPUTS[0], lyrics_text="[Saki] different lyrics"), INPUTS[1]]
    seen = []
    ResonanceExtractor(lambda p: seen.append(p) or "new", model="x").extract(edited, cache_file=cache)
    assert len(seen) == 1  # only the edited event re-derived


def test_resonance_fingerprint_excludes_model():
    a = resonance_fingerprint("o", "c", "l")
    b = resonance_fingerprint("o", "c", "l")
    assert a == b
    assert a != resonance_fingerprint("o", "c", "different lyrics")


def test_assemble_inputs_joins_and_skips(tmp_path):
    events_index = [
        {"arc_slug": "0001-a", "name": "Ameagari", "song_id": 64, "song_title": "ステラ"},  # JP
        {"arc_slug": "0002-b", "name": "No Summary", "song_id": 70, "song_title": "X"},  # no summary
        {"arc_slug": "0003-c", "name": "No Song", "song_title": "Y"},  # no song_id
        {"arc_slug": "0004-d", "name": "No Lyrics", "song_id": 99, "song_title": "Z"},  # fetch None
    ]
    summaries = {
        "EVENT|0001-a": {"summary": "Overview:\nSaki hides a fever; they reconcile.\n"},
        "EVENT|0004-d": {"summary": "Overview:\nsomething\n"},
    }
    page_map = {"64": {"pageid": 1289, "title": "Stella", "english": "Stella"},
                "99": {"pageid": 1, "title": "Z", "english": "Z"}}

    def fake_fetch(song_id, pm):
        if song_id == 64:
            return {"lines": [{"singer": "Saki", "japanese": "涙", "romaji": "namida",
                               "english": "tears"}], "attribution": "Wiki"}
        return None  # song 99 has no lyrics

    inputs = assemble_resonance_inputs(events_index, summaries, page_map, fetch=fake_fetch)
    assert [i["arc"] for i in inputs] == ["0001-a"]  # only the fully-resolved event
    got = inputs[0]
    # standardized English name from the map wins over the JP events_index title
    assert got["song_title"] == "Stella" and got["attribution"] == "Wiki"
    assert "tears" in got["lyrics_text"] and "Saki" in got["lyrics_text"]
    assert got["conclusion"]  # heuristic conclusion filled (no conclusions cache)


def test_assemble_prefers_cached_conclusion_and_arc_filter():
    events_index = [
        {"arc_slug": "0001-a", "name": "A", "song_id": 64, "song_title": "S"},
        {"arc_slug": "0009-z", "name": "Z", "song_id": 64, "song_title": "S"},
    ]
    summaries = {"EVENT|0001-a": {"summary": "Overview:\nx\n"},
                 "EVENT|0009-z": {"summary": "Overview:\ny\n"}}
    conclusions = {"EVENT|0001-a": {"conclusion": "CACHED ENDING"}}
    pm = {"64": {"pageid": 1, "title": "S"}}

    def fetch(sid, m):
        return {"lines": [{"singer": "", "japanese": "j", "english": "e"}], "attribution": ""}

    inputs = assemble_resonance_inputs(
        events_index, summaries, pm, conclusions=conclusions, arcs={"0001-a"}, fetch=fetch
    )
    assert len(inputs) == 1 and inputs[0]["arc"] == "0001-a"
    assert inputs[0]["conclusion"] == "CACHED ENDING"  # cache wins over heuristic


def test_resonance_regex_matches_and_rejects():
    import importlib

    from webapp import server as srv
    importlib.reload(srv)
    matches = [
        "how does the theme song relate to the story?",
        "what does the song mean?",
        "what's the meaning of the song",
        "how do the lyrics reflect the event",
        "song and story",
        "tell me about the resonance",
    ]
    rejects = [
        "who sang the song?",       # needle, no relation word
        "summarize this event",
        "what's the conclusion?",
        "what did Mizuki say?",
    ]
    assert all(srv._RESONANCE_RE.search(q) for q in matches)
    assert not any(srv._RESONANCE_RE.search(q) for q in rejects)


def test_intercept_serves_cached_resonance(monkeypatch):
    import importlib

    from webapp import server as srv
    from webapp.sessions import Focus

    importlib.reload(srv)
    ev = {"event_id": 1, "arc_slug": "0001-a", "name": "Ameagari", "nickname": "leo1"}
    monkeypatch.setattr(srv, "load_events", lambda: [ev])
    monkeypatch.setattr(
        srv, "_resonance_map",
        lambda: {"0001-a": {"resonance": "Stella mirrors Saki's arc.",
                            "attribution": "EN translation by X"}},
    )

    req = srv.QueryRequest(question="how does the theme song relate to the story?")
    out = srv._scoped_event_intercept(req, Focus(arcs=("0001-a",)))
    assert out and out["intent"] == "resonance" and out["backend"] == "summary"
    assert out["answer"] == "Stella mirrors Saki's arc."
    cit = out["citations"][0]
    assert cit["label"] == "Ameagari — song resonance"
    assert cit["excerpt"] == "EN translation by X"  # lyric source attribution


def test_intercept_resonance_falls_through_without_cache(monkeypatch):
    import importlib

    from webapp import server as srv
    from webapp.sessions import Focus

    importlib.reload(srv)
    ev = {"event_id": 1, "arc_slug": "0001-a", "name": "Ameagari"}
    monkeypatch.setattr(srv, "load_events", lambda: [ev])
    monkeypatch.setattr(srv, "_resonance_map", lambda: {})  # nothing cached

    req = srv.QueryRequest(question="what does the song mean?")
    # No fabricated song reading -> None (falls through to normal retrieval).
    assert srv._scoped_event_intercept(req, Focus(arcs=("0001-a",))) is None


def test_extract_writes_expected_entry_shape(tmp_path):
    cache = str(tmp_path / "r.json")
    with patch.object(ResonanceExtractor, "extract", ResonanceExtractor.extract):
        final = ResonanceExtractor(lambda p: "  note.  ", model="m").extract(
            INPUTS[:1], cache_file=cache
        )
    entry = final["EVENT|0001-a"]
    assert entry["resonance"] == "note."  # stripped
    assert entry["model"] == "m" and entry["attribution"] == "src"
    assert entry["schema_version"] == "1" and entry["fingerprint"]
