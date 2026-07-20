"""Deterministic contextual-retrieval prefix builder."""

from sekai_story_indexer.query.context import arc_context_line


def test_focus_event_line_includes_nickname_ordinal_unit_song():
    meta = {
        "name": "ここからRE:START！", "nickname": "airi1", "arc_slug": "0005-x",
        "focus_character": "桃井愛莉", "focus_character_id": 7, "focus_index": 1,
        "unit": "more_more_jump", "song_title": "Song X",
    }
    line = arc_context_line(meta, focus_name_en="Airi Momoi")
    assert "nickname airi1" in line
    assert "Airi Momoi" in line and "桃井愛莉" in line
    assert "1st focus event" in line
    assert "commissioned song Song X" in line


def test_ordinal_forms():
    for idx, word in [(1, "1st"), (2, "2nd"), (3, "3rd"), (4, "4th"), (11, "11th"),
                      (13, "13th"), (21, "21st"), (22, "22nd"), (23, "23rd"), (31, "31st")]:
        meta = {"name": "E", "focus_character": "C", "focus_character_id": 9,
                "focus_index": idx, "unit": "leo_need"}
        assert f"{word} focus event" in arc_context_line(meta)


def test_non_focus_event_omits_focus_clause():
    meta = {"name": "Crossover", "arc_slug": "0009-y", "focus_character_id": 0,
            "unit": "mixed"}
    line = arc_context_line(meta)
    assert "focus event" not in line
    assert "Crossover" in line


def test_missing_meta_is_empty():
    assert arc_context_line(None) == ""
    assert arc_context_line({}) == ""
