"""Server-side conversation focus state.

Condensation (query/condense.py) already rewrites follow-ups using history, but it
needs an API key and can misfire. This adds a deterministic, server-side memory of
the *current* event/character per chat session, so a pronoun follow-up ("what's
the climax?") stays on the last-discussed story even with no key — and a genuine
topic switch (naming a new entity) resets it. It also feeds the UI context chip
and the conversational case of the clarify gate.

Pure logic (`resolve_turn`) is separated from the tiny in-memory store so it
unit-tests without any web/session plumbing.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass

# A follow-up leans on prior context: a leading connective, a pronoun, or "the
# <story-part>". Deliberately conservative — we only *apply* remembered focus when
# the turn names no entity of its own, so a false positive just reuses scope.
_FOLLOWUP_RE = re.compile(
    r"^(and\b|but\b|what about\b|how about\b|then\b|also\b|what's\b|whats\b)"
    r"|\b(that|this|it|its|she|her|hers|he|him|his|they|them|their|there)\b"
    r"|\bthe\s+(event|story|arc|episode|conclusion|climax|ending|beginning|rest)\b",
    re.IGNORECASE,
)


def is_followup(question: str) -> bool:
    return bool(_FOLLOWUP_RE.search(question.strip()))


@dataclass
class Focus:
    """What the conversation is currently 'about'."""

    arcs: tuple[str, ...] = ()
    character_id: int | None = None
    label: str | None = None
    updated_at: float = 0.0

    def as_dict(self) -> dict:
        return {"arcs": list(self.arcs), "character_id": self.character_id, "label": self.label}


def resolve_turn(
    prev: Focus | None,
    *,
    referenced_arcs: tuple[str, ...],
    character_id: int | None,
    label: str | None,
    followup: bool,
) -> tuple[Focus, tuple[str, ...]]:
    """Given the prior focus and what THIS turn names, return
    ``(new_focus, scope_arcs)``.

    * Turn names arcs -> switch focus to them (reset), scope to them.
    * No arcs, names a *different* character -> shift focus to that character,
      drop the stale arc scope (a topic switch to that character).
    * Otherwise (no new arc, no different character) -> STAY on the prior event and
      carry its scope, so a follow-up about the current event ("who is Iori?") isn't
      lost to a global search even when it names no pronoun/connective. The absence
      of a new topic is the signal; ``followup`` is accepted for back-compat but no
      longer gates the carry.
    """
    if referenced_arcs:
        return (
            Focus(arcs=referenced_arcs, character_id=character_id, label=label),
            referenced_arcs,
        )
    if character_id is not None and (prev is None or character_id != prev.character_id):
        return Focus(character_id=character_id, label=label), ()
    if prev and prev.arcs:
        return prev, prev.arcs
    return (prev or Focus()), ()


class SessionStore:
    """Tiny capped in-memory focus store keyed by session id (single-process app)."""

    def __init__(self, max_sessions: int = 1000) -> None:
        self._focus: dict[str, Focus] = {}
        self.max_sessions = max_sessions
        # sync endpoints run in FastAPI's threadpool -> guard the check-then-evict
        self._lock = threading.Lock()

    def get(self, session_id: str | None) -> Focus | None:
        if not session_id:
            return None
        with self._lock:
            return self._focus.get(session_id)

    def set(self, session_id: str | None, focus: Focus) -> None:
        if not session_id:
            return
        with self._lock:
            # evict the oldest by update time when over capacity
            if session_id not in self._focus and len(self._focus) >= self.max_sessions:
                oldest = min(self._focus, key=lambda k: self._focus[k].updated_at)
                self._focus.pop(oldest, None)
            self._focus[session_id] = focus

    def clear(self, session_id: str | None) -> None:
        if session_id:
            with self._lock:
                self._focus.pop(session_id, None)
