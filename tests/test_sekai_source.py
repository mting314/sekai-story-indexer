from pathlib import Path

from sekai_story_indexer.indexer.processor import StoryProcessor
from sekai_story_indexer.query.official_en import load_official_en
from sekai_story_indexer.source.assets import event_logo_url, music_jacket_url
from sekai_story_indexer.source.fetcher import (
    fetch_area_conversations,
    fetch_card_stories,
    fetch_unit_stories,  # noqa: F401  (import sanity)
    plan_event,
    story_order_doc,
)
from sekai_story_indexer.source.nicknames import (
    assign_focus_nicknames,
    nickname_for,
    resolve_nickname,
)
from sekai_story_indexer.source.transform import (
    align_en_to_jp,
    arc_slug,
    build_area_event_map,
    build_card_parent_map,
    en_sidecar_path,
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


def test_build_card_parent_map_links_and_falls_back():
    cards_by_id = {
        10: {"id": 10, "characterId": 1, "cardRarityType": "rarity_4"},   # event-linked
        20: {"id": 20, "characterId": 2, "cardRarityType": "rarity_4"},   # multi-event
        30: {"id": 30, "characterId": 3, "cardRarityType": "rarity_birthday"},  # birthday
        40: {"id": 40, "characterId": 4, "cardRarityType": "rarity_1"},   # permanent/other
    }
    event_cards = [
        {"cardId": 10, "eventId": 5, "isDisplayCardStory": True},
        # card 20 appears in two events: the earlier (3) has no story flag, the
        # later (9) does -> the story-displaying event wins the tie-break.
        {"cardId": 20, "eventId": 3, "isDisplayCardStory": False},
        {"cardId": 20, "eventId": 9, "isDisplayCardStory": True},
    ]
    m = build_card_parent_map(event_cards, cards_by_id)
    assert m[10] == {"kind": "event", "event_id": 5, "character_id": 1, "is_display_card_story": True}
    assert m[20]["kind"] == "event" and m[20]["event_id"] == 9  # story-flag beats earlier id
    assert m[30]["kind"] == "birthday" and m[30]["event_id"] is None and m[30]["character_id"] == 3
    assert m[40]["kind"] == "other" and m[40]["event_id"] is None


def test_build_area_event_map_links_via_event_story_condition():
    event_stories = [
        {"id": 2, "eventId": 2, "eventStoryEpisodes": [{"id": 1000016}, {"id": 1000017}]},
    ]
    release_conditions = [
        {"id": 100108, "releaseConditionType": "event_story", "releaseConditionTypeId": 1000016},
        {"id": 500, "releaseConditionType": "none"},
        {"id": 600, "releaseConditionType": "event_story", "releaseConditionTypeId": 999999},  # unknown episode
    ]
    action_sets = [
        {"id": 839, "scenarioId": "areatalk_ev_night_01_001", "releaseConditionId": 100108},  # -> event 2
        {"id": 900, "scenarioId": "areatalk02_129", "releaseConditionId": 500},               # permanent
        {"id": 901, "scenarioId": "areatalk_ev_x_001", "releaseConditionId": 600},            # event but unresolved
        {"id": 902, "scriptId": "no_scenario"},                                                # skipped (no scenarioId)
    ]
    action_sets += [
        # campaign talk: no event_story condition, but scenarioId is April-Fool-tagged
        {"id": 903, "scenarioId": "areatalk_aprilfool2022_002", "releaseConditionId": 500},
        # movie/theater talk gated by serial code (permanent condition) but ev-tagged
        {"id": 904, "scenarioId": "areatalk_ev_theater_037", "releaseConditionId": 500},
    ]
    m = build_area_event_map(action_sets, release_conditions, event_stories)
    assert 902 not in m  # no scenarioId -> excluded
    assert m[839] == {"kind": "event", "event_id": 2, "campaign": None, "scenario_id": "areatalk_ev_night_01_001"}
    assert m[901]["kind"] == "event" and m[901]["event_id"] is None  # event-gated but episode unknown
    # areatalk02_129 is generic base chatter -> permanent (NOT mislabeled)
    assert m[900]["kind"] == "permanent" and m[900]["campaign"] is None
    # campaign-tagged talks are recovered from the "permanent" bucket by scenarioId
    assert m[903]["kind"] == "campaign" and m[903]["campaign"] == "aprilfool2022"
    assert m[904]["kind"] == "campaign" and m[904]["campaign"] == "ev_theater"


def test_area_campaign_tag_ignores_generic_chatter():
    from sekai_story_indexer.source.transform import area_campaign_tag
    assert area_campaign_tag("areatalk03_121") is None
    assert area_campaign_tag("op_02area") is None
    assert area_campaign_tag("areatalk_aprilfool2023_007") == "aprilfool2023"
    assert area_campaign_tag("areatalk_3rdaniv_098") == "3rdaniv"
    assert area_campaign_tag("areatalk_ev_theater_037") == "ev_theater"
    assert area_campaign_tag("areatalk_wl_wonder_01_001") == "wl_wonder_01"


def test_build_card_parent_map_earliest_event_when_no_story_flag():
    cards_by_id = {50: {"id": 50, "characterId": 1, "cardRarityType": "rarity_2"}}
    event_cards = [
        {"cardId": 50, "eventId": 12, "isDisplayCardStory": False},
        {"cardId": 50, "eventId": 7, "isDisplayCardStory": False},
    ]
    m = build_card_parent_map(event_cards, cards_by_id)
    assert m[50]["event_id"] == 7  # no story flag anywhere -> earliest event id


def test_fetch_card_stories_writes_per_card_tree(tmp_path):
    cards_rows = [{"id": 1, "characterId": 1, "prefix": "card one"}]  # char 1 = leo_need
    episode_rows = [
        {"id": 2, "cardId": 1, "seq": 2, "title": "b", "scenarioId": "s2",
         "assetbundleName": "res001", "cardEpisodePartType": "second_part"},
        {"id": 1, "cardId": 1, "seq": 1, "title": "a", "scenarioId": "s1",
         "assetbundleName": "res001", "cardEpisodePartType": "first_part"},
    ]
    seen: list[tuple[str, str]] = []

    def fake(bundle, sid):
        seen.append((bundle, sid))
        return {"TalkData": [{"WindowDisplayName": "一歌", "Body": "line"}]}

    n = fetch_card_stories(
        tmp_path, cards_rows=cards_rows, episode_rows=episode_rows,
        scenario_fetch=fake, en_scenario_fetch=lambda *a: {}, log=lambda *_: None,
    )
    assert n == 2
    card_dirs = list((tmp_path / "leo_need" / "card").iterdir())
    assert len(card_dirs) == 1 and card_dirs[0].name.startswith("0001-")
    files = sorted(p.name for p in card_dirs[0].glob("*.md"))
    assert files[0].startswith("01_") and files[1].startswith("02_")  # part order
    assert seen == [("res001", "s1"), ("res001", "s2")]  # fetched in seq order


def test_fetch_area_conversations_resolves_unit_and_skips_scenarioless(tmp_path):
    area_rows = [{"id": 4, "name": "area name"}]
    action_set_rows = [
        {"id": 5, "areaId": 4, "scenarioId": "as_a", "characterIds": [1]},      # leo_need
        {"id": 6, "areaId": 4, "scenarioId": "as_b", "characterIds": [1, 5]},   # spans units -> mixed
        {"id": 7, "areaId": 4, "scriptId": "x"},                                 # no scenarioId -> skipped
    ]
    seen: list[tuple[int, str]] = []

    def fake(aid, sid):
        seen.append((aid, sid))
        return {"TalkData": [{"WindowDisplayName": "", "Body": "hi"}]}

    n = fetch_area_conversations(
        tmp_path, area_rows=area_rows, action_set_rows=action_set_rows,
        scenario_fetch=fake, en_scenario_fetch=lambda *a: {}, log=lambda *_: None,
    )
    assert n == 2  # the scenarioId-less actionSet is skipped
    assert seen == [(5, "as_a"), (6, "as_b")]  # area-talk id rides through to the scenario fetch
    assert list((tmp_path / "leo_need" / "area").rglob("001_*.md"))   # single-unit talk
    assert list((tmp_path / "mixed" / "area").rglob("002_*.md"))      # cross-unit talk -> mixed


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
    assert meta.story_type == "Event"
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
    # a songless multi-unit CC is a seasonal collab, not a solo focus (Valentine /
    # White Day / New Year all lack a commissioned song).
    from sekai_story_indexer.source.catalog import build_catalog

    events = [{"id": 60, "name": "Valentine CC", "startAt": 1000, "eventType": "cheerful_carnival"}]
    stories = {60: {"id": 160, "eventId": 60, "assetbundleName": "ab", "eventStoryEpisodes": []}}
    # 3+ units -> seasonal collab, not a solo focus (a 2-unit CC would count)
    su = {160: [{"unit": "idol", "eventStoryUnitRelation": "main"},
                {"unit": "light_sound", "eventStoryUnitRelation": "sub"},
                {"unit": "theme_park", "eventStoryUnitRelation": "sub"}]}
    banner = {60: 7}  # Airi (MMJ) on banner, but it's a multi-unit collab
    cat = build_catalog(events, stories_by_event=stories, story_units_by_story_id=su,
                        music_by_event={}, banner_char_by_event=banner)
    assert cat[0]["is_focus_event"] is False


def test_focus_override_forces_and_excludes():
    # curated overrides: force a focus char (banner!=story lead), or force-exclude.
    from sekai_story_indexer.source.catalog import build_catalog

    events = [{"id": 97, "name": "Light Up the Fire", "startAt": 1000, "eventType": "marathon"},
              {"id": 98, "name": "Excluded", "startAt": 2000, "eventType": "marathon"}]
    stories = {97: {"id": 197, "eventId": 97, "assetbundleName": "a", "eventStoryEpisodes": []},
               98: {"id": 198, "eventId": 98, "assetbundleName": "b", "eventStoryEpisodes": []}}
    su = {197: [{"unit": "street", "eventStoryUnitRelation": "main"}],
          198: [{"unit": "street", "eventStoryUnitRelation": "main"}]}
    banner = {97: 9, 98: 9}  # master DB banner = Kohane for both
    cat = build_catalog(events, stories_by_event=stories, story_units_by_story_id=su,
                        music_by_event={}, banner_char_by_event=banner,
                        focus_overrides={97: 10, 98: 0})
    by = {r["event_id"]: r for r in cat}
    assert by[97]["focus_character_id"] == 10 and by[97]["nickname"] == "an1"  # forced -> An
    assert by[98]["is_focus_event"] is False and by[98]["focus_character_id"] == 0  # excluded


def test_multi_unit_marathon_requires_a_song():
    # multi-unit marathon: a real focus has a commissioned song; a songless
    # multi-unit event is a collab (regression for 響くトワイライトパレード / 夏祭り).
    from sekai_story_indexer.source.catalog import build_catalog

    ev = [{"id": 70, "name": "E", "startAt": 1000, "eventType": "marathon"}]
    base = dict(
        stories_by_event={70: {"id": 170, "eventId": 70, "assetbundleName": "a", "eventStoryEpisodes": []}},
        story_units_by_story_id={170: [{"unit": "street", "eventStoryUnitRelation": "main"},
                                       {"unit": "idol", "eventStoryUnitRelation": "sub"}]},
        banner_char_by_event={70: 9},  # Kohane (VBS) in the main unit
    )
    with_song = build_catalog(ev, music_by_event={70: {"title": "Song", "assetbundleName": "m"}}, **base)
    assert with_song[0]["is_focus_event"] is True
    songless = build_catalog(ev, music_by_event={}, **base)
    assert songless[0]["is_focus_event"] is False


def test_single_unit_event_without_song_is_still_a_focus():
    # a single-unit event is a focus even with no commissioned song (カーテンコール).
    from sekai_story_indexer.source.catalog import build_catalog

    ev = [{"id": 71, "name": "Curtain", "startAt": 1000, "eventType": "marathon"}]
    cat = build_catalog(
        ev,
        stories_by_event={71: {"id": 171, "eventId": 71, "assetbundleName": "a", "eventStoryEpisodes": []}},
        story_units_by_story_id={171: [{"unit": "theme_park", "eventStoryUnitRelation": "main"}]},
        music_by_event={}, banner_char_by_event={71: 16},  # Rui (WxS), no song
    )
    assert cat[0]["is_focus_event"] is True


# --- Official English quotes -------------------------------------------------

def _talk(*bodies):
    return {"TalkData": [{"WindowDisplayName": "", "Body": b} for b in bodies]}


def test_align_en_to_jp_aligns_when_counts_match():
    jp = [("穂波", "弟もいるから"), ("一歌", "すごいな")]
    en = _talk("I have a younger brother too", "That's amazing")
    assert align_en_to_jp(jp, en) == [("", "I have a younger brother too"), ("", "That's amazing")]


def test_align_en_to_jp_none_on_mismatch_or_empty():
    jp = [("穂波", "弟もいるから"), ("一歌", "すごいな")]
    assert align_en_to_jp(jp, _talk("only one line")) is None  # count mismatch
    assert align_en_to_jp(jp, {}) is None  # EN not localized
    assert align_en_to_jp([], _talk("x")) is None  # no JP


def test_en_sidecar_path_is_off_the_md_glob():
    p = en_sidecar_path(Path("story/leo_need/event/0001-x/05_y.md"))
    assert p.name == "05_y.md.en"
    assert not p.match("*.md")  # never picked up as JP story text


def test_write_en_sidecar_only_when_aligned(tmp_path: Path):
    from sekai_story_indexer.source.fetcher import _write_en_sidecar

    jp_path = tmp_path / "05_y.md"
    jp_path.write_text("# 5. t\n\n穂波: 弟もいるから\n一歌: すごいな", encoding="utf-8")
    jp_lines = [("穂波", "弟もいるから"), ("一歌", "すごいな")]

    # aligned EN -> sidecar written
    ok = _write_en_sidecar(
        jp_path, "5. t", jp_lines,
        lambda ab, sid: _talk("I have a younger brother too", "That's amazing"),
        "bundle", "scenario",
    )
    assert ok
    en_path = en_sidecar_path(jp_path)
    assert en_path.exists()
    assert "I have a younger brother too" in en_path.read_text(encoding="utf-8")

    # mismatched EN -> no sidecar (JP stays the fallback)
    en_path.unlink()
    assert not _write_en_sidecar(
        jp_path, "5. t", jp_lines, lambda ab, sid: _talk("one line only"), "b", "s"
    )
    assert not en_path.exists()


def test_load_official_en_maps_jp_lines_to_en(tmp_path: Path):
    d = tmp_path / "leo_need" / "event" / "0001-x"
    d.mkdir(parents=True)
    (d / "05_y.md").write_text("# 5. t\n\n穂波: 弟もいるから\n一歌: すごいな", encoding="utf-8")
    (d / "05_y.md.en").write_text(
        "# 5. t\n\nHonami: I have a younger brother too\nIchika: That's amazing", encoding="utf-8"
    )
    mapping = load_official_en(tmp_path)
    assert mapping["穂波: 弟もいるから"] == "Honami: I have a younger brother too"
    assert mapping["一歌: すごいな"] == "Ichika: That's amazing"


def test_load_official_en_skips_drifted_pairs(tmp_path: Path):
    d = tmp_path / "u" / "event" / "0001-x"
    d.mkdir(parents=True)
    (d / "05_y.md").write_text("# t\n\na: 1\nb: 2", encoding="utf-8")
    (d / "05_y.md.en").write_text("# t\n\nA: one", encoding="utf-8")  # count drift
    assert load_official_en(tmp_path) == {}
