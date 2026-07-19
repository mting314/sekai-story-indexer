"""Lightweight intent routing for common query shapes.

Mirrors the original repo's routing idea (query/router.py + typed tools), but as
a cheap deterministic classifier for the local backend. The highest-value case —
"summarize event X" — takes a deterministic path (resolve the entity, pull its
whole scope) instead of lexical top-k search; "count" questions get an exact
count, never an LLM estimate.
"""

from __future__ import annotations

import re

Intent = str  # "summarize" | "count" | "general"

_SUMMARIZE_RE = re.compile(
    r"\b(summar(y|ize|ise)|recap|overview|tl;?dr|synops(is|e)|"
    r"what happen(s|ed)? (in|during)|tell me about)\b",
    re.IGNORECASE,
)
_COUNT_RE = re.compile(
    r"\b(how many|number of|count (of|the)?)\b.*\b"
    r"(lines?|times?|dialogue|turns?|appearances?|speak|say|said)\b",
    re.IGNORECASE,
)


def classify(question: str) -> Intent:
    if _COUNT_RE.search(question):
        return "count"
    if _SUMMARIZE_RE.search(question):
        return "summarize"
    return "general"
