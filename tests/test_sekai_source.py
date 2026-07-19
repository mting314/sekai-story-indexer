from pathlib import Path

from sekai_story_indexer.indexer.processor import StoryProcessor
from sekai_story_indexer.source.fetcher import plan_event, story_order_doc
from sekai_story_indexer.source.transform import (
    arc_slug,
    episode_filename,
    is_key_event_story,
    render_episode_markdown,
    resolve_unit,
    resolve_unit_from_story_units,
    scenario_to_lines,
    slugify,
    tree_relpath,
)


def test_slugify_ascii_and_japanese():
    assert slugify("Grow, Glorious!") == "grow-glorious"
    assert slugify("咲きたい！") == ""  # non-ascii dropped
    assert slugify("VBS Live 2024") == "vbs-live-2024"


def test_arc_and_episode_naming_is_zero_padded():
    assert arc_slug(151, "Grow Glorious") == "0151-grow-glorious"
    assert arc_slug(7, "") == "0007"
    assert episode_filename(5, "The Title") == "05_the-title.md"
    assert episode_filename(12, "咲") == "12.md"


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
