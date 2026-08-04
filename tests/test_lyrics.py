"""Sekaipedia lyric parser + fetch (keyless, offline — mocked network).

Locks the {{Lyrics line}} / {{Lyric}} / <tabber> parsing verified live against the
real Stella page (pageid 1289). No lyric text is persisted — this is the read side.
"""

from sekai_story_indexer.source import lyrics

WIKITEXT = """\
{{Infobox song|song id=64|english=Stella}}

== Lyrics ==
<tabber>
Game Version=
{{Lyrics head
|columns = japanese,romaji,english
}}
{{Lyrics line
| japanese = {{Lyric|Saki|涙が夜に
空が遠くなる}}
| romaji = {{Lyric|Saki|namida ga yoru ni
sora ga tooku naru}}
| english = {{Lyric|Saki|My tears dissolve,
the sky grows far}}
}}
|-|
Full Version=
{{Lyrics head
|columns = japanese,romaji,english
}}
{{Lyrics line
| japanese = {{Lyric|Saki|涙が夜に}}
| romaji = {{Lyric|Saki|namida ga yoru ni}}
| english = {{Lyric|Saki|My tears}}
}}
{{Lyrics line
| japanese = {{Lyric|Ichika|そう}}{{Lyric|Miku|誰かの}}
| romaji = {{Lyric|Ichika|sou}}{{Lyric|Miku|dareka no}}
| english = {{Lyric|Ichika|Yes}}{{Lyric|Miku|Someone}}
}}
{{Lyrics tail|English translation by X. Retrieved from [[mh:vocaloidlyrics:foo|Wiki]].}}
</tabber>

== Trivia ==
Should NOT be parsed as lyrics. {{Lyrics line|japanese={{Lyric|Nope|x}}}}
"""


def test_parse_versions_and_attribution():
    parsed = lyrics.parse_lyrics_wikitext(WIKITEXT)
    assert set(parsed["versions"]) == {"Game Version", "Full Version"}
    assert len(parsed["versions"]["Game Version"]) == 1
    assert len(parsed["versions"]["Full Version"]) == 3
    assert "English translation by X" in parsed["attribution"]


def test_multiline_and_columns_aligned():
    seg = lyrics.parse_lyrics_wikitext(WIKITEXT)["versions"]["Game Version"][0]
    assert seg["singer"] == "Saki"
    assert seg["japanese"] == "涙が夜に\n空が遠くなる"  # newline preserved
    assert seg["english"] == "My tears dissolve,\nthe sky grows far"


def test_multiple_singers_in_one_block_split():
    full = lyrics.parse_lyrics_wikitext(WIKITEXT)["versions"]["Full Version"]
    ichika, miku = full[1], full[2]
    assert (ichika["singer"], ichika["english"]) == ("Ichika", "Yes")
    assert (miku["singer"], miku["japanese"], miku["english"]) == ("Miku", "誰かの", "Someone")


def test_section_boundary_excludes_trivia():
    # The {{Lyric|Nope|x}} under == Trivia == must not leak into parsed lyrics.
    for lines in lyrics.parse_lyrics_wikitext(WIKITEXT)["versions"].values():
        assert all(seg["singer"] != "Nope" for seg in lines)


def test_choose_version_prefers_full():
    assert lyrics.choose_version({"Game Version": [1], "Full Version": [1]}) == "Full Version"
    assert lyrics.choose_version({"Game Version": [1]}) == "Game Version"
    assert lyrics.choose_version({"default": [1]}) == "default"
    assert lyrics.choose_version({}) is None


def test_no_lyrics_section():
    assert lyrics.parse_lyrics_wikitext("{{Infobox song|song id=1}}\n== Trivia ==\nx")["versions"] == {}


def test_lyrics_to_text_default_jp_en():
    lines = lyrics.parse_lyrics_wikitext(WIKITEXT)["versions"]["Game Version"]
    text = lyrics.lyrics_to_text(lines)
    assert text.startswith("[Saki] 涙が夜に")
    assert "My tears dissolve" in text and "namida" not in text  # romaji excluded by default


def test_fetch_lyrics_via_page_map(monkeypatch):
    monkeypatch.setattr(lyrics, "_fetch_page_wikitext", lambda pageid: WIKITEXT)
    page_map = {"64": {"pageid": 1289, "title": "Stella"}}
    res = lyrics.fetch_lyrics(64, page_map)
    assert res["version"] == "Full Version" and res["title"] == "Stella"
    assert res["pageid"] == 1289 and len(res["lines"]) == 3
    # unmapped song -> None (coverage gap, never a wrong page)
    assert lyrics.fetch_lyrics(999, page_map) is None


def test_fetch_lyrics_none_when_no_lyrics(monkeypatch):
    monkeypatch.setattr(lyrics, "_fetch_page_wikitext", lambda pageid: "== Trivia ==\nx")
    assert lyrics.fetch_lyrics(64, {"64": {"pageid": 1, "title": "X"}}) is None
