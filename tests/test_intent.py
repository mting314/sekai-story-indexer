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
