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
    assert ScopeIndex(INDEX).resolve("generic question").as_dict() == {
        "unit": None, "arc_id": None, "nickname": None
    }


def test_chroma_where():
    assert chroma_where(ScopeIndex(INDEX).resolve("kasa5")) == {"arc_id": "0130-y"}
    assert chroma_where(ScopeIndex(INDEX).resolve("q", unit="nightcord")) == {"unit": "nightcord"}
    assert chroma_where(ScopeIndex(INDEX).resolve("q")) is None
