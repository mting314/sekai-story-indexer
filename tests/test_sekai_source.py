from pathlib import Path

from sekai_story_indexer.indexer.processor import StoryProcessor
from sekai_story_indexer.source.assets import event_logo_url, music_jacket_url
from sekai_story_indexer.source.fetcher import plan_event, story_order_doc
from sekai_story_indexer.source.nicknames import (
    assign_focus_nicknames,
    nickname_for,
    resolve_nickname,
)
from sekai_story_indexer.source.transform import (
    arc_slug,
    episode_filename,
    focus_character_id,
    is_key_event_story,
    render_episode_markdown,
    resolve_unit,
    resolve_unit_from_story_units,
    scenario_to_lines,
    slugify,
    song_info,
    tree_relpath,
)


def test_slugify_ascii_and_japanese():
    assert slugify("Grow, Glorious!") == "grow-glorious"
    assert slugify("咲きたい！") == "saki-tai"
    assert slugify("雨上がりのステップ") == "ameagari-no-suteppu"
    assert slugify("VBS Live 2024") == "vbs-live-2024"


def test_arc_and_episode_naming_is_zero_padded():
    assert arc_slug(151, "Grow Glorious") == "0151-grow-glorious"
    assert arc_slug(7, "") == "0007"
    assert episode_filename(5, "The Title") == "05_the-title.md"
    assert episode_filename(12, "咲") == "12_saki.md"


def test_resolve_unit_by_db_field_and_characters():
    assert resolve_unit(db_unit="street") == "vivid_bad_squad"
    assert resolve_unit(character_ids=[9, 10, 11]) == "vivid_bad_squad"
    # spanning two real units -> mixed
    assert resolve_unit(character_ids=[1, 9]) == "mixed"
    # virtual singers only
    assert resolve_unit(character_ids=[21, 22]) == "virtual_singer"
    # nothing resolvable
    assert resolve_unit() == "mixed"


def test_scenario_to_lines_extracts_speaker_and_body():
    scenario = {
        "TalkData": [
            {"WindowDisplayName": "こはね", "Body": "おはよう\nみんな"},
            {"WindowDisplayName": "", "Body": "……"},
            {"WindowDisplayName": "杏", "Body": ""},  # dropped: empty body
        ]
    }
    assert scenario_to_lines(scenario) == [("こはね", "おはよう みんな"), ("", "……")]


def test_render_episode_markdown_uses_scene_delimiter():
    md = render_episode_markdown(
        "1. Opening",
        [[("A", "hi"), ("", "narration")], [("B", "bye")]],
    )
    assert md.startswith("# 1. Opening")
    assert "A: hi" in md
    assert "narration" in md
    assert "\n---\n" in md


def _event():
    return {"id": 151, "name": "Grow Glorious", "unit": "street", "startAt": 1000}


def _story():
    return {
        "eventId": 151,
        "assetbundleName": "event_grow",
        "outline": "an outline",
        "eventStoryEpisodes": [
            {"episodeNo": 2, "title": "Two", "scenarioId": "ev_151_02"},
            {"episodeNo": 1, "title": "One", "scenarioId": "ev_151_01"},
        ],
    }


def test_resolve_unit_from_story_units_prefers_main_relation():
    rows = [
        {"unit": "street", "eventStoryUnitRelation": "main"},
        {"unit": "piapro", "eventStoryUnitRelation": "sub"},
    ]
    assert resolve_unit_from_story_units(rows) == "vivid_bad_squad"
    assert is_key_event_story(rows) is True
    # two main units -> crossover
    cross = [
        {"unit": "street", "eventStoryUnitRelation": "main"},
        {"unit": "idol", "eventStoryUnitRelation": "main"},
    ]
    assert resolve_unit_from_story_units(cross) == "mixed"
    # no main relation -> not a key story; single present unit still resolves
    sub_only = [{"unit": "theme_park", "eventStoryUnitRelation": "sub"}]
    assert is_key_event_story(sub_only) is False
    assert resolve_unit_from_story_units(sub_only) == "wonderlands_showtime"


def test_plan_event_orders_episodes_and_resolves_unit():
    story_units = [{"unit": "street", "eventStoryUnitRelation": "main"}]
    plan = plan_event(_event(), _story(), story_units=story_units)
    assert plan.unit == "vivid_bad_squad"
    assert plan.is_key_story is True
    assert plan.arc_slug == "0151-grow-glorious"
    assert [e.scenario_id for e in plan.episodes] == ["ev_151_01", "ev_151_02"]
    assert plan.episodes[0].relpath == tree_relpath(
        "vivid_bad_squad", "event", "0151-grow-glorious", "01_one.md"
    )

    # without story_units, falls back to the event's own unit field
    fallback = plan_event(_event(), _story())
    assert fallback.unit == "vivid_bad_squad"
    assert fallback.is_key_story is False


def test_story_order_doc_is_chronological():
    late = plan_event({"id": 200, "name": "Later", "unit": "idol", "startAt": 5000}, _story() | {"eventId": 200})
    early = plan_event(_event(), _story())
    doc = story_order_doc([late, early])
    side_arcs = doc["chronological_order"][0]["arcs"]
    assert side_arcs.index("0151-grow-glorious") < side_arcs.index("0200-later")
    assert doc["chronological_order"] == doc["summary_order"]


def test_processor_reads_sekai_tree_and_populates_unit(tmp_path: Path):
    p = tmp_path / "story" / "vivid_bad_squad" / "event" / "0151-grow-glorious" / "01_one.md"
    p.parent.mkdir(parents=True)
    p.write_text("# 1. One\n\nこはね: おはよう\n\n---\n\n杏: またね\n", encoding="utf-8")
    nodes = StoryProcessor.process_file(p)
    assert len(nodes) == 2
    meta = nodes[0].metadata
    assert meta.unit == "vivid_bad_squad"
    assert meta.content_type == "event"
    assert meta.arc_id == "0151-grow-glorious"
    assert meta.story_type == "Side"
    assert meta.episode_number == 1
    assert meta.parent_year_id == "vivid_bad_squad|0151-grow-glorious"


def test_resolve_nickname_roundtrip():
    # Tsukasa=13, Mizuki=20 (user-confirmed abbreviations)
    assert resolve_nickname("kasa5") == (13, 5)
    assert resolve_nickname("mizu3") == (20, 3)
    assert resolve_nickname("kasa-5") == (13, 5)
    assert resolve_nickname("KASA 5") == (13, 5)
    assert resolve_nickname("nope99") is None
    assert nickname_for(13, 5) == "kasa5"


def test_assign_focus_nicknames_numbers_per_character_chronologically():
    events = [
        {"event_id": 30, "focus_character_id": 13, "started_at": 3000},
        {"event_id": 10, "focus_character_id": 13, "started_at": 1000},
        {"event_id": 20, "focus_character_id": 20, "started_at": 2000},
        {"event_id": 40, "focus_character_id": 0, "started_at": 4000},  # no focus -> skipped
    ]
    nn = assign_focus_nicknames(events)
    assert nn[10]["nickname"] == "kasa1"
    assert nn[30]["nickname"] == "kasa2"  # later date -> 2nd Tsukasa focus
    assert nn[20]["nickname"] == "mizu1"
    assert 40 not in nn


def test_focus_character_id_picks_featured_limited_card():
    cards = {
        100: {"id": 100, "characterId": 13, "cardRarityType": "rarity_2", "releaseAt": 5},
        101: {"id": 101, "characterId": 13, "cardRarityType": "rarity_4", "releaseAt": 10},
        102: {"id": 102, "characterId": 15, "cardRarityType": "rarity_4", "releaseAt": 20},
    }
    # earliest rarity_4 wins -> Tsukasa (13)
    assert focus_character_id([100, 101, 102], cards) == 13
    assert focus_character_id([], cards) == 0


def test_song_info_flattens_music_record():
    info = song_info({"title": "S", "composer": "C", "lyricist": "L", "arranger": "A", "assetbundleName": "ab"})
    assert info["song_title"] == "S"
    assert info["song_assetbundle"] == "ab"
    assert song_info(None) == {}


def test_asset_urls():
    assert event_logo_url("event_grow").endswith("/event/event_grow/logo/logo.webp")
    assert music_jacket_url("m01").endswith("/music/jacket/m01/m01.webp")


def test_build_catalog_enriches_and_numbers_nicknames():
    from sekai_story_indexer.source.catalog import build_catalog

    # marathon events (a focus event must be a marathon with single-unit 4* cards)
    events = [
        {"id": 6, "name": "Lyric", "startAt": 2000, "eventType": "marathon"},
        {"id": 2, "name": "Marionette", "startAt": 1000, "eventType": "marathon"},
    ]
    stories_by_event = {
        6: {"id": 106, "eventId": 6, "assetbundleName": "ab6",
            "eventStoryEpisodes": [{"episodeNo": 1}, {"episodeNo": 2}]},
        2: {"id": 102, "eventId": 2, "assetbundleName": "ab2", "eventStoryEpisodes": []},
    }
    story_units_by_story_id = {
        106: [{"unit": "street", "eventStoryUnitRelation": "main"}],
        102: [{"unit": "school_refusal", "eventStoryUnitRelation": "main"}],
    }
    banner_char_by_event = {6: 9, 2: 18}  # Kohane(9) / Mafuyu(18)
    # single-unit 4* cards -> these qualify as focus events
    event_card_ids = {6: [901], 2: [902]}
    cards_by_id = {
        901: {"id": 901, "characterId": 9, "cardRarityType": "rarity_4"},   # VBS
        902: {"id": 902, "characterId": 18, "cardRarityType": "rarity_4"},  # N25
    }
    music_by_event = {6: {"title": "Hibana", "assetbundleName": "m6"}}

    cat = build_catalog(
        events,
        stories_by_event=stories_by_event,
        story_units_by_story_id=story_units_by_story_id,
        banner_char_by_event=banner_char_by_event,
        event_card_ids=event_card_ids,
        cards_by_id=cards_by_id,
        music_by_event=music_by_event,
    )
    # chronological
    assert [r["event_id"] for r in cat] == [2, 6]
    marionette, lyric = cat[0], cat[1]
    assert marionette["unit"] == "nightcord"
    assert marionette["nickname"] == "mafu1"      # Mafuyu (18) 1st focus
    assert lyric["unit"] == "vivid_bad_squad"
    assert lyric["nickname"] == "koha1"           # Kohane (9) 1st focus
    assert lyric["song_title"] == "Hibana"
    assert lyric["episodes"] == 2
    assert lyric["has_story"] is True
    assert lyric["logo_url"].endswith("/event/ab6/logo/logo.webp")


def test_plot_weight_classifier():
    from sekai_story_indexer.source.relevance import classify_event, weight_factor

    # key + single unit + focus + song -> high
    assert classify_event({"is_key_story": True, "unit": "leo_need",
                           "focus_character": "x", "song_title": "s"}) == "high"
    # key crossover -> medium
    assert classify_event({"is_key_story": True, "unit": "mixed"}) == "medium"
    # not key -> filler (still indexed)
    assert classify_event({"is_key_story": False, "unit": "leo_need"}) == "filler"
    # boost ordering: high > medium > filler, filler never zero
    assert weight_factor("high") > weight_factor("medium") > weight_factor("filler") > 0


def test_build_catalog_no_focus_when_no_banner():
    from sekai_story_indexer.source.catalog import build_catalog

    events = [{"id": 99, "name": "Anniversary", "startAt": 500}]
    stories = {99: {"id": 199, "eventId": 99, "assetbundleName": "ab99", "eventStoryEpisodes": []}}
    su = {199: [{"unit": "light_sound", "eventStoryUnitRelation": "sub"},
                {"unit": "idol", "eventStoryUnitRelation": "sub"}]}
    cat = build_catalog(
        events, stories_by_event=stories, story_units_by_story_id=su,
        music_by_event={}, banner_char_by_event={},  # no banner -> no focus
    )
    r = cat[0]
    assert r["focus_character_id"] == 0
    assert r["focus_character"] == ""
    assert r["nickname"] is None


def test_event_without_banner_character_is_not_a_focus_event():
    # crossover / anniversary events have no bannerGameCharacterUnitId -> no single
    # focus, so no nickname (the banner char is the authoritative focus signal).
    from sekai_story_indexer.source.catalog import build_catalog

    events = [{"id": 22, "name": "Anniversary", "startAt": 1000, "eventType": "marathon"}]
    stories = {22: {"id": 122, "eventId": 22, "assetbundleName": "ab", "eventStoryEpisodes": []}}
    su = {122: [{"unit": "school_refusal", "eventStoryUnitRelation": "main"}]}
    cat = build_catalog(events, stories_by_event=stories, story_units_by_story_id=su,
                        music_by_event={}, banner_char_by_event={})  # no banner
    r = cat[0]
    assert r["is_focus_event"] is False
    assert r["nickname"] is None and r["focus_character"] == ""


def test_cross_unit_guest_spotlight_is_still_a_focus_event():
    # a modern character-spotlight marathon with cross-unit GUEST 4* cards is still
    # that character's focus event — the banner char is authoritative (event 0209:
    # "アイドル・花里みのり" banner=Minori with VBS/WxS guests). Regression.
    from sekai_story_indexer.source.catalog import build_catalog

    events = [{"id": 50, "name": "Idol Minori", "startAt": 1000, "eventType": "marathon"}]
    stories = {50: {"id": 150, "eventId": 50, "assetbundleName": "ab", "eventStoryEpisodes": []}}
    # story's main unit is MMJ (guest 4* cards from other units don't change this)
    su = {150: [{"unit": "idol", "eventStoryUnitRelation": "main"}]}
    banner = {50: 5}  # Minori (MORE MORE JUMP!)
    cat = build_catalog(events, stories_by_event=stories, story_units_by_story_id=su,
                        music_by_event={}, banner_char_by_event=banner)
    r = cat[0]
    assert r["is_focus_event"] is True
    assert r["focus_character_id"] == 5 and r["nickname"] == "mino1"


def test_backfill_story_tree(tmp_path: Path):
    import json

    from sekai_story_indexer.source.backfill_slugs import backfill_story_tree

    story_root = tmp_path / "story"
    ep_dir = story_root / "leo_need" / "event" / "0001"
    ep_dir.mkdir(parents=True)
    ep_file = ep_dir / "01.md"
    ep_file.write_text("# 1. 雨上がりのステップ\n\n咲希: こんにちは", encoding="utf-8")

    index_file = tmp_path / "events_index.json"
    index_file.write_text(
        json.dumps([{"event_id": 1, "name": "雨上がりのステップ", "arc_slug": "0001"}], ensure_ascii=False),
        encoding="utf-8",
    )

    stats = backfill_story_tree(story_root, index_file, tmp_path / "story_order.yaml")
    assert stats["dirs_renamed"] == 1
    assert stats["files_renamed"] == 1
    assert (
        story_root / "leo_need" / "event" / "0001-ameagari-no-suteppu" / "01_ameagari-no-suteppu.md"
    ).exists()


def test_remap_summary_cache_keys(tmp_path):
    """Backfill remaps arc_slug-keyed summary caches so they survive a rename."""
    import json

    from sekai_story_indexer.source.backfill_slugs import _remap_cache_keys

    p = tmp_path / "event_summaries.json"
    p.write_text(
        json.dumps({"0097": {"summary": "x"}, "0100-keep": {"summary": "y"}}),
        encoding="utf-8",
    )
    changed = _remap_cache_keys(p, {"0097": "0097-hashire"}, log=lambda m: None)
    assert changed == 1
    data = json.loads(p.read_text(encoding="utf-8"))
    assert "0097-hashire" in data and "0097" not in data      # renamed
    assert data["0100-keep"] == {"summary": "y"}              # unmapped key preserved


def test_cheerful_carnival_single_unit_is_a_focus_event():
    # Cheerful Carnival with a single-unit 4* focus counts toward nickname numbering
    # (the community numbers CC events too) — regression for saki7-vs-saki6.
    from sekai_story_indexer.source.catalog import build_catalog

    events = [{"id": 30, "name": "CC", "startAt": 1000, "eventType": "cheerful_carnival"}]
    stories = {30: {"id": 130, "eventId": 30, "assetbundleName": "ab", "eventStoryEpisodes": []}}
    su = {130: [{"unit": "light_sound", "eventStoryUnitRelation": "main"}]}  # Leo/need
    banner = {30: 2}  # Saki
    event_card_ids = {30: [1]}
    cards_by_id = {1: {"id": 1, "characterId": 2, "cardRarityType": "rarity_4"}}  # Saki / Leo/need
    cat = build_catalog(events, stories_by_event=stories, story_units_by_story_id=su,
                        music_by_event={}, banner_char_by_event=banner,
                        event_card_ids=event_card_ids, cards_by_id=cards_by_id)
    r = cat[0]
    assert r["is_focus_event"] is True
    assert r["focus_character_id"] == 2 and r["nickname"] == "saki1"


def test_virtual_singer_banner_is_not_a_focus_event():
    # VS headline some events (e.g. New Year) but never get a solo focus event;
    # the banner char must belong to the single focus unit. Regression for miku1.
    from sekai_story_indexer.source.catalog import build_catalog

    events = [{"id": 40, "name": "New Year", "startAt": 1000, "eventType": "marathon"}]
    stories = {40: {"id": 140, "eventId": 40, "assetbundleName": "ab", "eventStoryEpisodes": []}}
    su = {140: [{"unit": "leo_need", "eventStoryUnitRelation": "main"}]}
    banner = {40: 21}  # Miku (Virtual Singer) on the banner
    event_card_ids = {40: [1, 2]}
    cards_by_id = {
        1: {"id": 1, "characterId": 21, "cardRarityType": "rarity_4"},  # Miku / VS (excluded)
        2: {"id": 2, "characterId": 2, "cardRarityType": "rarity_4"},   # Saki / Leo/need
    }
    cat = build_catalog(events, stories_by_event=stories, story_units_by_story_id=su,
                        music_by_event={}, banner_char_by_event=banner,
                        event_card_ids=event_card_ids, cards_by_id=cards_by_id)
    r = cat[0]
    assert r["is_focus_event"] is False
    assert r["focus_character_id"] == 0 and r["nickname"] is None


def test_multi_unit_cheerful_carnival_is_not_a_focus_event():
    # a seasonal collab CC with guest sub-units is not a solo focus (unlike a
    # single-unit CC). Regression for airi3 (Valentine) / Tsukasa White Day.
    from sekai_story_indexer.source.catalog import build_catalog

    events = [{"id": 60, "name": "Valentine CC", "startAt": 1000, "eventType": "cheerful_carnival"}]
    stories = {60: {"id": 160, "eventId": 60, "assetbundleName": "ab", "eventStoryEpisodes": []}}
    su = {160: [{"unit": "idol", "eventStoryUnitRelation": "main"},
                {"unit": "light_sound", "eventStoryUnitRelation": "sub"}]}
    banner = {60: 7}  # Airi (MMJ) on banner, but it's a multi-unit collab
    cat = build_catalog(events, stories_by_event=stories, story_units_by_story_id=su,
                        music_by_event={}, banner_char_by_event=banner)
    assert cat[0]["is_focus_event"] is False
