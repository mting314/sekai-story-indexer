
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
    ep1 = tmp_path / "leo_need" / "unit" / "01-chapter" / "01_a.md"
    assert ep1.exists() and "一歌: はじめまして" in ep1.read_text(encoding="utf-8")
