"""Server-side conversation focus state + history windowing."""

from sekai_story_indexer.query.condense import window_history
from webapp.sessions import Focus, SessionStore, is_followup, resolve_turn


def test_is_followup_detects_pronouns_and_connectives():
    assert is_followup("what's the climax of that story")
    assert is_followup("and the conclusion?")
    assert is_followup("what about her sister")
    assert not is_followup("Summarize airi1")
    assert not is_followup("How did MORE MORE JUMP form?")


def test_resolve_turn_switches_on_named_arcs():
    prev = Focus(arcs=("0005-x",), character_id=7)
    focus, scope = resolve_turn(
        prev, referenced_arcs=("0002-y",), character_id=18, label="E"
    )
    assert focus.arcs == ("0002-y",) and scope == ("0002-y",)  # reset to the new topic


def test_resolve_turn_carries_focus_on_pronoun_followup():
    prev = Focus(arcs=("0005-x",), character_id=7)
    focus, scope = resolve_turn(
        prev, referenced_arcs=(), character_id=None, label=None
    )
    assert scope == ("0005-x",)  # follow-up stays on the remembered event
    assert focus is prev


def test_resolve_turn_arc_focus_is_sticky_for_bare_question():
    # "When did Honami ask Kanade for help?" right after summarizing an event names
    # no new event -> it must stay scoped to that event, not go global.
    prev = Focus(arcs=("0076-echo-my-melody",))
    focus, scope = resolve_turn(
        prev, referenced_arcs=(), character_id=None, label=None
    )
    assert scope == ("0076-echo-my-melody",)  # sticky: soft-scope fallback guards it


def test_resolve_turn_naming_a_character_keeps_the_event():
    # Naming a character while focused on an event updates the in-focus character
    # but stays scoped to the event (the soft-scope fallback handles the case where
    # that character isn't actually in it).
    prev = Focus(arcs=("0005-x",), character_id=7)
    focus, scope = resolve_turn(
        prev, referenced_arcs=(), character_id=9, label="Kohane"
    )
    assert focus.character_id == 9 and focus.arcs == ("0005-x",) and scope == ("0005-x",)


def test_resolve_turn_character_focus_without_prior_event():
    # With no arc focus yet, a named character just sets character focus (no scope).
    focus, scope = resolve_turn(
        Focus(), referenced_arcs=(), character_id=9, label="Kohane"
    )
    assert focus.character_id == 9 and focus.arcs == () and scope == ()


def test_session_store_evicts_oldest():
    store = SessionStore(max_sessions=2)
    store.set("a", Focus(arcs=("1",), updated_at=1.0))
    store.set("b", Focus(arcs=("2",), updated_at=2.0))
    store.set("c", Focus(arcs=("3",), updated_at=3.0))  # evicts "a" (oldest)
    assert store.get("a") is None
    assert store.get("b") and store.get("c")


def test_window_history_caps_turns_and_chars():
    hist = [{"role": "user", "text": f"turn {i}"} for i in range(20)]
    assert len(window_history(hist, max_turns=6)) == 6
    big = [{"role": "user", "text": "x" * 3000} for _ in range(5)]
    # first kept, next would exceed 4000 -> stop
    assert len(window_history(big, max_turns=6, max_chars=4000)) == 1
    assert window_history([]) == []
