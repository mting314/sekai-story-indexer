import json
from pathlib import Path

from sekai_story_indexer.query.intent import classify
from sekai_story_indexer.query.local import build_local_engine

REPO = Path(__file__).resolve().parent.parent
SAMPLE_STORY = REPO / "sample" / "story"
SAMPLE_INDEX = json.loads((REPO / "sample" / "events_index.json").read_text(encoding="utf-8"))


def test_classify_intents():
    assert classify("Summarize koha1") == "summarize"
    assert classify("what happens in mafu1?") == "summarize"
    assert classify("give me a recap of VBS") == "summarize"
    assert classify("How many lines does Kohane speak?") == "count"
    assert classify("number of times An says something") == "count"
    assert classify("Why did Mafuyu disappear?") == "general"


def test_summarize_pulls_whole_scope():
    eng = build_local_engine(SAMPLE_STORY, SAMPLE_INDEX)
    r = eng.summarize("Summarize koha1")
    assert r["intent"] == "summarize"
    assert r["scope"]["arc_id"] == "0006-lyric"
    # deterministic: all scenes of the event, in order (not lexical top-k)
    assert all(c["arc_id"] == "0006-lyric" for c in r["citations"])
    assert len(r["citations"]) >= 3  # both episodes' scenes


def test_count_dialogue_is_exact():
    eng = build_local_engine(SAMPLE_STORY, SAMPLE_INDEX)
    r = eng.count_dialogue("How many lines does Kohane have in koha1?")
    assert r["intent"] == "count"
    assert isinstance(r["count"], int) and r["count"] >= 1
    assert "Kohane" in r["answer"]


def test_count_without_target_asks():
    eng = build_local_engine(SAMPLE_STORY, SAMPLE_INDEX)
    r = eng.count_dialogue("how many lines are there?")
    assert r["count"] if "count" in r else True  # no target -> prompt for one
    assert "which character" in r["answer"].lower()


def test_multi_character_count_by_unit():
    eng = build_local_engine(SAMPLE_STORY, SAMPLE_INDEX)
    r = eng.count_dialogue("How many lines does each nightcord character have?")
    assert r["intent"] == "count"
    assert isinstance(r.get("counts"), dict)
    # all four N25 members targeted
    names = " ".join(r["counts"].keys())
    assert "Kanade" in names and "Mafuyu" in names and "Ena" in names and "Mizuki" in names
    assert "Dialogue lines" in r["answer"]  # multi-target breakdown format


def test_multiple_named_characters_count():
    eng = build_local_engine(SAMPLE_STORY, SAMPLE_INDEX)
    r = eng.count_dialogue("how many lines do Kanade and Mafuyu have?")
    assert set(k for k in r["counts"]) >= {"Kanade Yoisaki", "Mafuyu Asahina"}


def test_condense_noop_without_history_or_key(monkeypatch):
    import sekai_story_indexer.query.condense as cond
    assert cond.condense("hi", None) == "hi"
    assert cond.condense("hi", []) == "hi"
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    assert cond.condense("how about ena?", [{"role": "user", "text": "count mafuyu"}]) == "how about ena?"
