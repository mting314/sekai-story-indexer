"""Tests for song resonance data loading and schema validation."""

from sekai_story_indexer.source.resonance import (
    get_resonance_for_event,
    load_song_resonance,
    validate_resonance_entry,
)


def test_load_song_resonance_file_exists():
    data = load_song_resonance("song_resonance.json")
    assert isinstance(data, dict)
    assert "0001-ameagari-no-ichiban-hoshi" in data


def test_validate_resonance_entry_valid():
    entry = {
        "event_id": 1,
        "song_title": "ステラ",
        "unit": "leo_need",
        "resonance_mappings": [
            {
                "lyric_jp": "描いてた未来ってなんだっけな",
                "lyric_en": "What was the future we used to paint together?",
                "story_episode": "07_yukkuri-susumo-u",
                "line_range": "L46-L59",
                "speaker": "Saki Tenma",
                "story_quote_jp": "あたし、何にもできなかった間も...",
                "story_quote_en": "I kept thinking, we should be having classes...",
                "resonance_commentary": "Test commentary.",
            }
        ],
    }
    assert validate_resonance_entry(entry) is True


def test_validate_resonance_entry_invalid():
    invalid_entry = {"event_id": 1, "song_title": "ステラ"}
    assert validate_resonance_entry(invalid_entry) is False


def test_get_resonance_for_event():
    res = get_resonance_for_event("0001-ameagari-no-ichiban-hoshi")
    assert res is not None
    assert res["song_title_en"] == "stella"
    assert len(res["resonance_mappings"]) >= 1
    assert "line_range" in res["resonance_mappings"][0]
