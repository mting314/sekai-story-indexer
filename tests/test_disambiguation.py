"""Clarify-instead-of-guess gate.

The 3-event sample corpus can't produce a natural title collision, so the fire
cases use inline synthetic events (house style: see test_scoping.py) that
reproduce the "rise as one" shape — a phrase matching an event *title* AND a
character whose arc spans multiple focus events.
"""

from sekai_story_indexer.query.disambiguation import (
    find_candidates,
    maybe_clarify,
)

# Honami (id 20) has TWO focus events; one of them is titled "Rise as One", so
# "rise as one" is ambiguous: the specific event vs. her overall arc.
CHARACTERS = {
    "20": {"en": "Honami Mochizuki"},
    "9": {"en": "Kohane Azusawa"},
}
EVENTS = [
    {"event_id": 30, "arc_slug": "0030-rise", "name": "Rise as One",
     "nickname": "hona2", "focus_character_id": 20},
    {"event_id": 12, "arc_slug": "0012-warm", "name": "A Warm Welcome",
     "nickname": "hona1", "focus_character_id": 20},
    {"event_id": 6, "arc_slug": "0006-lyric", "name": "Back-to-Back Lyrics",
     "nickname": "koha1", "focus_character_id": 9},
]


def test_named_multi_event_character_clarifies():
    # Honami has two focus events; "Honami's story" (no ordinal/nickname) is
    # ambiguous -> offer the overall arc plus each specific event.
    resp = maybe_clarify("summarize Honami's story", EVENTS, CHARACTERS)
    assert resp is not None
    assert resp["backend"] == "clarify"
    kinds = {o["type"] for o in resp["options"]}
    assert kinds == {"event", "character_arc"}
    arcs = {o.get("arc_slug") for o in resp["options"] if o["type"] == "event"}
    assert arcs == {"0030-rise", "0012-warm"}
    assert any(o.get("character_id") == 20 for o in resp["options"])


def test_explicit_nickname_short_circuits():
    # user already disambiguated -> no clarify, let normal resolution handle it
    assert maybe_clarify("summarize hona2", EVENTS, CHARACTERS) is None


def test_ordinal_short_circuits():
    assert maybe_clarify("summarize Honami's first focus event", EVENTS, CHARACTERS) is None


def test_single_focus_character_not_ambiguous():
    # Kohane has one focus event -> naming her is not an ambiguous "arc"
    assert maybe_clarify("how does Kohane feel about singing", EVENTS, CHARACTERS) is None


def test_incidental_word_overlap_does_not_match_title():
    # "one" alone must not resolve the "Rise as One" title (needs >=2 distinctive
    # tokens); no multi-focus character named either -> no candidates.
    assert find_candidates("tell me about that one moment", EVENTS, CHARACTERS) == []


def test_two_colliding_event_titles_clarify():
    # a reused title (e.g. a rerun/collab) -> two events match the same phrase
    events = [
        {"event_id": 1, "arc_slug": "0001-a", "name": "Miracle Paint", "nickname": "x1",
         "focus_character_id": 1},
        {"event_id": 2, "arc_slug": "0002-b", "name": "Miracle Paint", "nickname": "x2",
         "focus_character_id": 2},
    ]
    resp = maybe_clarify("summarize miracle paint", events, {})
    assert resp is not None
    arcs = {o.get("arc_slug") for o in resp["options"]}
    assert arcs == {"0001-a", "0002-b"}


def test_single_title_match_proceeds():
    events = [
        {"event_id": 1, "arc_slug": "0001-a", "name": "Miracle Paint", "nickname": "x1",
         "focus_character_id": 1},
    ]
    # one interpretation only -> not ambiguous, caller proceeds normally
    assert maybe_clarify("summarize miracle paint", events, {}) is None
