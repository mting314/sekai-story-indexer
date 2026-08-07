import json
from pathlib import Path

from sekai_story_indexer.query.metadata import resolve_event_by_title, resolve_focus_reference


def test_fuzzy_event_title_matching():
    events_path = Path("events_index.json")
    assert events_path.exists()
    events = json.loads(events_path.read_text(encoding="utf-8"))

    # Exact English title
    e1 = resolve_event_by_title("Resonate with you", events)
    assert e1 is not None
    assert e1["event_id"] == 20

    # Fuzzy English title (typo: "reonat with you")
    e2 = resolve_event_by_title("reonat with you", events)
    assert e2 is not None
    assert e2["event_id"] == 20

    # Question with verb + title
    e3 = resolve_focus_reference("Summarize Resonate with you", events, {})
    assert e3 is not None
    assert e3["event_id"] == 20

    # Question with verb + typo
    e4 = resolve_focus_reference("Summarize reonat with you", events, {})
    assert e4 is not None
    assert e4["event_id"] == 20

    # Nickname still resolves
    e5 = resolve_event_by_title("saki7", events)
    assert e5 is not None
    assert e5["event_id"] == 188
