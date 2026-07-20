"""Full-engine contextual-retrieval injection (teed up; takes effect on re-ingest).

These assert the embedding/lexical text-building logic without needing Chroma or a
re-embed — the actual re-embed is a costly, user-triggered `indexer ingest`.
"""

import pytest

pytest.importorskip("chromadb")  # cli imports the vector stack at module load

from sekai_story_indexer.cli import (  # noqa: E402
    _build_event_context,
    _embedding_document,
    _lexical_document,
)
from sekai_story_indexer.models.story import StoryMetadata, StoryNode  # noqa: E402

EVENTS = [{
    "arc_slug": "0005-x", "name": "RE:START", "nickname": "airi1",
    "focus_character_id": 7, "focus_index": 1, "unit": "more_more_jump",
    "song_title": "S",
}]
GLOSSARY = {"characters": {"桃井愛莉": "Airi Momoi"}}


def _node() -> StoryNode:
    meta = StoryMetadata(
        unit="more_more_jump", arc_id="0005-x", story_type="Event",
        episode_name="ep1", part_name="1", scene_index=0, content_type="event",
        file_path="story/x/1.md",
    )
    return StoryNode(node_id="x", text="raw scene text", metadata=meta, summary_level=4)


def test_build_event_context_resolves_english_focus_name():
    ctx = _build_event_context(EVENTS, GLOSSARY)
    line = ctx["0005-x"]
    assert "nickname airi1" in line
    assert "Airi Momoi" in line
    assert "1st focus event" in line


def test_embedding_document_prepends_context_but_keeps_raw():
    ctx = _build_event_context(EVENTS, GLOSSARY)
    doc = _embedding_document(_node(), GLOSSARY, ctx)
    assert "airi1" in doc.text and "focus event" in doc.text  # searchable context
    assert "raw scene text" in doc.text  # scene text preserved


def test_no_context_is_a_noop():
    # existing behavior is unchanged until a re-ingest supplies event_context
    assert "airi1" not in _embedding_document(_node(), GLOSSARY, None).text


def test_lexical_document_gets_same_context():
    ctx = _build_event_context(EVENTS, GLOSSARY)
    assert "airi1" in _lexical_document(_node(), GLOSSARY, ctx)
