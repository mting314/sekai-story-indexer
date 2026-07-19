"""Shared query scoping: resolve a question + hints into a Scope.

Used by both backends so nickname/unit/event/World-Link scoping behaves
identically:
  * local engine — filters candidate scene nodes;
  * full engine — becomes a Chroma metadata ``where`` filter.

Resolution precedence: explicit event_id -> explicit unit -> World Link series
("world link 3" -> all its parts) -> a nickname / wl-part token (e.g. ``kasa5``,
``wl3-1``) -> nothing (global).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_NICK_TOKEN_RE = re.compile(r"[a-z]+\d+(?:-\d+)?", re.IGNORECASE)
# "World Link 3", "world link 3 part 1", "WL3", "wl 3" (part optional)
_WL_RE = re.compile(r"\b(?:world\s*link|wl)\s*(\d+)(?:\s*(?:part|pt|-)\s*(\d+))?\b", re.IGNORECASE)


@dataclass(frozen=True)
class Scope:
    unit: str | None = None
    arc_id: str | None = None
    nickname: str | None = None
    arc_ids: tuple[str, ...] = field(default_factory=tuple)  # multi-event scope
    label: str | None = None  # human label for the scope, e.g. "World Link 3"

    def as_dict(self) -> dict:
        return {
            "unit": self.unit,
            "arc_id": self.arc_id,
            "nickname": self.nickname,
            "arc_ids": list(self.arc_ids),
            "label": self.label,
        }


class ScopeIndex:
    """Lookups derived from the enriched events index."""

    def __init__(self, events_index: list[dict] | None):
        self.by_nick: dict[str, dict] = {}
        self.by_event: dict[int, dict] = {}
        self.wl_series: dict[int, list[dict]] = {}  # series number -> rows (parts)
        for row in events_index or []:
            if not row.get("arc_slug"):
                continue
            self.by_event[row["event_id"]] = row
            if row.get("nickname"):
                self.by_nick[row["nickname"].lower()] = row
            if row.get("wl_alias"):  # World Link alias, e.g. "wl3-1"
                self.by_nick[row["wl_alias"].lower()] = row
            if row.get("world_link_series"):
                self.wl_series.setdefault(row["world_link_series"], []).append(row)
        for parts in self.wl_series.values():
            parts.sort(key=lambda r: r.get("world_link_part", 0))

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

        # World Link: "world link 3 [part 1]" / "wl3" / "wl3-1"
        wl = _WL_RE.search(question)
        if wl:
            series, part = int(wl.group(1)), wl.group(2)
            parts = self.wl_series.get(series, [])
            if parts:
                if part:
                    row = next((r for r in parts if r.get("world_link_part") == int(part)), None)
                    if row:
                        return Scope(unit=row.get("unit"), arc_id=row.get("arc_slug"),
                                     nickname=row.get("wl_alias"), label=row.get("world_link_label"))
                # whole series -> all parts
                return Scope(
                    arc_ids=tuple(r["arc_slug"] for r in parts),
                    label=f"World Link {series}",
                )

        for tok in _NICK_TOKEN_RE.findall(question):
            row = self.by_nick.get(tok.lower())
            if row:
                return Scope(unit=row.get("unit"), arc_id=row.get("arc_slug"), nickname=tok.lower())
        return Scope()


def chroma_where(scope: Scope) -> dict | None:
    """Build a Chroma ``where`` metadata filter from a scope (for the full engine).
    Requires unit/arc_id to be present in stored vector metadata."""
    if scope.arc_ids:
        return {"arc_id": {"$in": list(scope.arc_ids)}}
    if scope.arc_id:
        return {"arc_id": scope.arc_id}
    if scope.unit:
        return {"unit": scope.unit}
    return None
