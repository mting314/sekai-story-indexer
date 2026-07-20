"""Multilingual event-title matching (JP / official EN / romaji)."""

from sekai_story_indexer.query.aliases import (
    _romaji_from_slug,
    event_alias_texts,
    event_title_matches,
)

# saki6: JP title + romaji slug; plus an official-EN title carried as name_jp's
# counterpart (here we put EN in `name` and JP in `name_jp`, as the webapp overlay
# does for localized events).
EVENTS = [
    {
        "event_id": 188, "arc_slug": "0188-aogu-yozora-ni-hoshi-ha-magire-te",
        "name": "The Stars Blend into the Night Sky", "name_jp": "仰ぐ夜空に、星は紛れて",
        "nickname": "saki6",
    },
    {
        "event_id": 6, "arc_slug": "0006-lyric",
        "name": "Back-to-Back Lyrics", "nickname": "koha1",
    },
]


def test_romaji_from_slug():
    assert _romaji_from_slug("0188-aogu-yozora-ni-hoshi-ha-magire-te") == \
        "aogu yozora ni hoshi ha magire te"


def test_matches_official_english_title():
    hits = event_title_matches("what happens in The Stars Blend into the Night Sky", EVENTS)
    assert [e["arc_slug"] for e in hits] == ["0188-aogu-yozora-ni-hoshi-ha-magire-te"]


def test_matches_japanese_title_substring():
    hits = event_title_matches("summarize 仰ぐ夜空に、星は紛れて please", EVENTS)
    assert hits and hits[0]["arc_slug"] == "0188-aogu-yozora-ni-hoshi-ha-magire-te"


def test_matches_romaji():
    hits = event_title_matches("what is aogu yozora hoshi about", EVENTS)
    assert hits and hits[0]["arc_slug"] == "0188-aogu-yozora-ni-hoshi-ha-magire-te"


def test_literal_translation_partial_overlap_still_matches():
    # a fan/literal translation sharing content words with the official EN
    hits = event_title_matches("the night sky where stars blend", EVENTS)
    assert hits and hits[0]["arc_slug"] == "0188-aogu-yozora-ni-hoshi-ha-magire-te"


def test_common_word_query_does_not_false_match():
    # a single incidental word must not resolve a title
    assert event_title_matches("tell me a story about the sky", EVENTS) == []


def test_alias_texts_include_jp_en_romaji():
    aliases = event_alias_texts(EVENTS[0])
    assert "The Stars Blend into the Night Sky" in aliases
    assert "仰ぐ夜空に、星は紛れて" in aliases
    assert any("aogu" in a for a in aliases)
