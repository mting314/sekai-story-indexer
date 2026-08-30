"""Turn-level retrieval: speaker attribution and conversational windows.

The behaviour under test is precision on "what did X say about Y" questions. A
scene here is a whole episode (~48 turns in the real corpus), so scene-level
matching answers with lines other characters said. These cases pin the three
outcomes that matters: attribute correctly, follow a pronoun back to the topic,
and refuse rather than misattribute.
"""

import json
from pathlib import Path

from sekai_story_indexer.indexer.processor import StoryProcessor
from sekai_story_indexer.query.local import build_local_engine
from sekai_story_indexer.query.turns import (
    TIER_DIRECT,
    TIER_PRESENT,
    TIER_REPLY,
    find_turn_hits,
    turn_texts,
    window_lines,
)

REPO = Path(__file__).resolve().parent.parent
SAMPLE_STORY = REPO / "sample" / "story"
SAMPLE_INDEX = json.loads((REPO / "sample" / "events_index.json").read_text(encoding="utf-8"))
FIXTURE = SAMPLE_STORY / "leo_need" / "event" / "0001-firststar" / "01_after_the_rain.md"

BROTHER = [["brother"]]


def _nodes():
    return StoryProcessor.process_file(FIXTURE)


def _is_honami(speaker: str) -> bool:
    return speaker.lower() == "honami"


# -- the pure layer ---------------------------------------------------------


def test_turn_texts_falls_back_to_source_without_en_sidecar():
    node = _nodes()[1]
    texts = turn_texts(node, en_map=None)
    assert len(texts) == len(node.dialogue_turns)
    assert any("younger brother" in display for _, display in texts)


def test_turn_texts_prefers_official_en():
    node = _nodes()[1]
    jp_line = f"{node.dialogue_turns[0].speaker}: {node.dialogue_turns[0].text}"
    searchable, display = turn_texts(node, {jp_line: "Saki: OFFICIAL EN LINE"})[0]
    assert display == "Saki: OFFICIAL EN LINE"
    assert "official en line" in searchable  # EN is searchable too, lowercased


def test_direct_utterance_outranks_mere_presence():
    nodes = _nodes()
    hits = find_turn_hits(
        nodes, range(len(nodes)), content_concepts=BROTHER, is_speaker=_is_honami
    )
    assert hits, "expected Honami to be found near a brother mention"
    assert hits[0].tier == TIER_DIRECT
    assert "I have a younger brother" in hits[0].quote


def test_reply_with_pronoun_is_found_though_the_reply_omits_the_topic():
    """Saki raises the brother; Honami answers with "he". Matching whole turns
    against "brother" alone would drop this, and it is the better answer."""
    nodes = _nodes()
    hits = find_turn_hits(
        nodes, range(len(nodes)), content_concepts=BROTHER, is_speaker=_is_honami
    )
    replies = [h for h in hits if h.tier == TIER_REPLY]
    assert replies, "anaphoric reply should be retrieved"
    reply = replies[0]
    assert "brother" not in reply.quote.lower()  # the answer itself never says it
    assert "he's at an age" in reply.quote
    assert reply.anchor != reply.center  # topic and attribution are different turns


def test_co_presence_is_ranked_last():
    """Saki talks about Tsukasa while Honami is in the room and says something
    unrelated — the shape of the false positives scene retrieval produces."""
    nodes = _nodes()
    hits = find_turn_hits(
        nodes, range(len(nodes)), content_concepts=BROTHER, is_speaker=_is_honami
    )
    present = [h for h in hits if h.tier == TIER_PRESENT]
    assert present, "the trap scene should still be found, just demoted"
    assert all(h.tier >= present[0].tier for h in present)
    assert hits.index(present[0]) == len(hits) - len(present)  # all trail the rest


def test_no_hits_when_the_speaker_never_appears():
    nodes = _nodes()
    hits = find_turn_hits(
        nodes,
        range(len(nodes)),
        content_concepts=[["brother"]],
        is_speaker=lambda s: s.lower() == "kanade",
    )
    assert hits == []


def test_no_hits_when_the_topic_is_absent():
    nodes = _nodes()
    hits = find_turn_hits(
        nodes,
        range(len(nodes)),
        content_concepts=[["motorcycle"]],
        is_speaker=_is_honami,
    )
    assert hits == []


def test_window_lines_carry_the_surrounding_exchange():
    nodes = _nodes()
    hits = find_turn_hits(
        nodes, range(len(nodes)), content_concepts=BROTHER, is_speaker=_is_honami
    )
    reply = next(h for h in hits if h.tier == TIER_REPLY)
    lines = window_lines(nodes[reply.node_index], reply)
    assert any("little brother" in ln for ln in lines), "topic turn must be in the window"
    assert any("he's at an age" in ln for ln in lines), "the reply must be in the window"


# -- wired into the engine --------------------------------------------------


def test_query_attributes_the_line_to_the_right_speaker():
    eng = build_local_engine(SAMPLE_STORY, SAMPLE_INDEX)
    r = eng.query("When does Honami mention her brother?")
    answer = r["answer"]
    assert "I have a younger brother" in answer
    # Saki's line about Tsukasa is in the same arc and matches at scene level.
    assert "My brother walked me here" not in answer
    assert r["citations"][0]["arc_id"] == "0001-firststar"


def test_query_returns_the_conversational_window_as_evidence():
    eng = build_local_engine(SAMPLE_STORY, SAMPLE_INDEX)
    r = eng.query("What does Honami say about shopping with her brother?")
    window = r["citations"][0]["window"]
    assert window, "citations should carry the surrounding turns"
    assert any("he's at an age" in ln for ln in window)


def test_query_declines_rather_than_misattributing():
    eng = build_local_engine(SAMPLE_STORY, SAMPLE_INDEX)
    r = eng.query("What does Shiho say about her brother?")
    assert r["citations"] == []
    assert "Shiho" in r["answer"]
    assert "I have a younger brother" not in r["answer"]


def test_non_attribution_questions_keep_scene_retrieval():
    """The turn path must not hijack questions that aren't about who said what."""
    eng = build_local_engine(SAMPLE_STORY, SAMPLE_INDEX)
    r = eng.query("Why is everyone worried that Mafuyu has not been logging into Nightcord?")
    assert r["citations"], "scene retrieval should still answer this"
    assert any(w in r["answer"].lower() for w in ("week", "logged", "silence"))


def test_recipient_of_a_speech_verb_does_not_trigger_attribution():
    """'tell Kohane' makes Kohane the recipient, not the speaker."""
    eng = build_local_engine(SAMPLE_STORY, SAMPLE_INDEX)
    assert eng._attributed_speaker("Who is An and where does she tell Kohane to meet?") == []
    assert eng._attributed_speaker("When does Honami mention her brother?")


def test_filler_words_are_not_treated_as_topics():
    eng = build_local_engine(SAMPLE_STORY, SAMPLE_INDEX)
    concepts = eng._content_concepts("What does Honami say about going shopping with her brother?")
    flat = {t for c in concepts for t in c}
    assert "brother" in flat and "shopping" in flat
    assert "going" not in flat
