"""Shared query scoping: resolve a question + hints into (unit, arc_id).

Used by both backends so nickname/unit/event scoping behaves identically:
  * local engine — filters candidate scene nodes;
  * full engine — becomes a Chroma metadata ``where`` filter.

Resolution precedence: explicit event_id -> explicit unit -> a nickname token in
the question (e.g. ``kasa5``) -> nothing (global).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_NICK_TOKEN_RE = re.compile(r"[a-z]+\d+", re.IGNORECASE)


@dataclass(frozen=True)
class Scope:
    unit: str | None = None
    arc_id: str | None = None
    nickname: str | None = None

    def as_dict(self) -> dict:
        return {"unit": self.unit, "arc_id": self.arc_id, "nickname": self.nickname}


class ScopeIndex:
    """Lookups derived from the enriched events index."""

    def __init__(self, events_index: list[dict] | None):
        self.by_nick: dict[str, dict] = {}
        self.by_event: dict[int, dict] = {}
        for row in events_index or []:
            if not row.get("arc_slug"):
                continue
            self.by_event[row["event_id"]] = row
            if row.get("nickname"):
                self.by_nick[row["nickname"].lower()] = row

    def resolve(
        self,
        question: str,
        *,
        unit: str | None = None,
        event_id: int | None = None,
    ) -> Scope:
        if event_id and event_id in self.by_event:
            row = self.by_event[event_id]
            return Scope(unit=unit or row.get("unit"), arc_id=row.get("arc_slug"))
        if unit:
            return Scope(unit=unit)
        for tok in _NICK_TOKEN_RE.findall(question):
            row = self.by_nick.get(tok.lower())
            if row:
                return Scope(unit=row.get("unit"), arc_id=row.get("arc_slug"), nickname=tok.lower())
        return Scope()


def chroma_where(scope: Scope) -> dict | None:
    """Build a Chroma ``where`` metadata filter from a scope (for the full engine).
    Requires unit/arc_id to be present in stored vector metadata."""
    clauses = []
    if scope.arc_id:
        clauses.append({"arc_id": scope.arc_id})
    elif scope.unit:
        clauses.append({"unit": scope.unit})
    if not clauses:
        return None
    return clauses[0] if len(clauses) == 1 else {"$and": clauses}
