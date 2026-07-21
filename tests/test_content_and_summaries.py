
from sekai_story_indexer.query.summaries import build_unit_overviews
from sekai_story_indexer.source import fetcher


def test_build_unit_overviews_groups_by_unit():
    idx = [
        {"event_id": 1, "unit": "leo_need", "name": "E1", "outline": "Saki forms a band.",
         "plot_weight": "high", "started_at": 1000, "nickname": "saki1"},
        {"event_id": 2, "unit": "leo_need", "name": "E2", "outline": "filler beach trip",
         "plot_weight": "filler", "started_at": 2000},
        {"event_id": 3, "unit": "nightcord", "name": "N1", "outline": "Mafuyu vanishes.",
         "plot_weight": "high", "started_at": 1500},
    ]
    overviews = {n.metadata.unit: n for n in build_unit_overviews(idx)}
    assert set(overviews) == {"leo_need", "nightcord"}
    leo = overviews["leo_need"]
    assert leo.summary_level == 1
    assert leo.metadata.content_type == "unit_overview"
    assert "Saki forms a band" in leo.text
    assert "filler beach trip" not in leo.text  # filler excluded from overview


def test_fetch_unit_stories_writes_tree(tmp_path, monkeypatch):
    fixture = [{
        "unit": "light_sound",  # -> leo_need
        "chapters": [{
            "chapterNo": 1, "title": "はじまり", "assetbundleName": "ln-chapter",
            "episodes": [
                {"episodeNo": 2, "title": "B", "scenarioId": "ln_01_02"},
                {"episodeNo": 1, "title": "A", "scenarioId": "ln_01_01"},
            ],
        }],
    }]
    monkeypatch.setattr(fetcher.client, "unit_stories", lambda: fixture)
    scenarios = {"ln_01_01": {"TalkData": [{"WindowDisplayName": "一歌", "Body": "はじめまして"}]},
                 "ln_01_02": {"TalkData": [{"WindowDisplayName": "咲希", "Body": "またね"}]}}
    n = fetcher.fetch_unit_stories(
        tmp_path, scenario_fetch=lambda ab, sid: scenarios[sid], log=lambda m: None
    )
    assert n == 2
    ep1 = next((tmp_path / "leo_need" / "unit").rglob("01_a.md"))
    assert ep1.exists() and "一歌: はじめまして" in ep1.read_text(encoding="utf-8")


def test_load_local_summary_nodes(tmp_path):
    """Local *_summaries.json caches -> embeddable StoryNodes at 3 tiers."""
    import json

    from sekai_story_indexer.indexer.local_summary_nodes import load_local_summary_nodes

    (tmp_path / "story" / "vivid_bad_squad" / "event" / "0097-x").mkdir(parents=True)
    (tmp_path / "story" / "vivid_bad_squad" / "event" / "0097-x" / "01.md").write_text("# e", encoding="utf-8")
    (tmp_path / "events_index.json").write_text(
        json.dumps([{"arc_slug": "0097-x", "unit": "vivid_bad_squad", "event_id": 97, "started_at": 1}]),
        encoding="utf-8",
    )
    (tmp_path / "episode_summaries.json").write_text(
        json.dumps({"0097-x": {"01": {"summary": "## Overview\nKohane{char_id=13} sings.", "characters": [13]}}}),
        encoding="utf-8",
    )
    (tmp_path / "unit_summaries.json").write_text(
        json.dumps({"vivid_bad_squad": {"summary": "## Overview\nVBS forms.", "characters": [13]}}),
        encoding="utf-8",
    )

    import os
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        nodes = load_local_summary_nodes(tmp_path / "story")
    finally:
        os.chdir(cwd)

    by_level = {n.summary_level: n for n in nodes}
    # event tier (level 2) retired with event_summaries.json -> unit + episode only
    assert set(by_level) == {1, 3}                          # unit, episode
    assert all(n.metadata.unit == "vivid_bad_squad" for n in nodes)
    assert all("char_id" not in n.text for n in nodes)      # inline tags stripped
    assert by_level[3].metadata.parent_part_id == "0097-x:01"
