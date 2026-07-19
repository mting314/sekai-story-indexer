from sekai_story_indexer.query.scoping import ScopeIndex, chroma_where

INDEX = [
    {"event_id": 2, "arc_slug": "0002-x", "unit": "nightcord", "nickname": "mafu1"},
    {"event_id": 13, "arc_slug": "0130-y", "unit": "wonderlands_showtime", "nickname": "kasa5"},
]


def test_resolve_by_nickname():
    s = ScopeIndex(INDEX).resolve("what happens in kasa5?")
    assert s.arc_id == "0130-y" and s.unit == "wonderlands_showtime" and s.nickname == "kasa5"


def test_resolve_by_event_id():
    s = ScopeIndex(INDEX).resolve("anything", event_id=2)
    assert s.arc_id == "0002-x" and s.unit == "nightcord"


def test_explicit_unit_wins_when_no_event():
    s = ScopeIndex(INDEX).resolve("kasa5 stuff", unit="nightcord")
    assert s.unit == "nightcord" and s.arc_id is None  # explicit unit short-circuits


def test_no_scope():
    s = ScopeIndex(INDEX).resolve("generic question")
    assert s.unit is None and s.arc_id is None and s.nickname is None and not s.arc_ids


def test_world_link_series_scopes_all_parts():
    idx = [
        {"event_id": 202, "arc_slug": "0202-a", "world_link_series": 3, "world_link_part": 1},
        {"event_id": 205, "arc_slug": "0205-b", "world_link_series": 3, "world_link_part": 2},
        {"event_id": 112, "arc_slug": "0112-c", "world_link_series": 1, "world_link_part": 1},
    ]
    si = ScopeIndex(idx)
    s = si.resolve("summarize all parts of world link 3")
    assert s.label == "World Link 3"
    assert set(s.arc_ids) == {"0202-a", "0205-b"}
    s2 = si.resolve("world link 3 part 2")
    assert s2.arc_id == "0205-b" and not s2.arc_ids


def test_chroma_where():
    assert chroma_where(ScopeIndex(INDEX).resolve("kasa5")) == {"arc_id": "0130-y"}
    assert chroma_where(ScopeIndex(INDEX).resolve("q", unit="nightcord")) == {"unit": "nightcord"}
    assert chroma_where(ScopeIndex(INDEX).resolve("q")) is None
