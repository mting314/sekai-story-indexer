"""Focused event conclusions — the *keyless* read side.

Answers "what's the conclusion?" with the event's actual ending/resolution, NOT
the whole Overview (the old behaviour, which read as "just the summary").

Two sources, in priority order:

1. **Semantic (preferred):** a pre-built entry in ``conclusions_cache.json`` — an
   LLM read the event's summary *once, offline* (``sekai conclusions``) and picked
   the climax episode + wrote a short "how it ends". Served keyless here: no API
   call at query time.
2. **Heuristic fallback (keyless, approximate):** when there's no cache entry, we
   score the Episode Index beats and pick the resolution beat rather than the last
   one. Project Sekai event stories almost always end on an *epilogue* (a live, an
   afterparty, a Sekai reward scene), so "the last episode" is systematically the
   wrong pick — the climax is usually a beat or two earlier. This is a labelled
   guess, not narrative understanding; the cache is the real answer.

Kept dependency-free (stdlib + ``summary_sections``) so the web app can import it
without pulling the generation stack.
"""

from __future__ import annotations

import re

from ..indexer.summary_sections import extract_summary_sections

CONCLUSION_PROMPT_VERSION = "1"

# One "- Episode N: <prose>" line from the Episode Index section.
_EPISODE_BEAT_RE = re.compile(r"^[-*]?\s*Episode\s+(\d+)\s*[:.\-]\s*(.+)$", re.IGNORECASE)

# Resolution / climax signals — the dramatic turn where the central conflict pays
# off. Substrings (matched case-insensitively) so tenses/inflections all hit.
_RESOLUTION_MARKERS = (
    "apolog", "promis", "confront", "reconcil", "realiz", "resolv", "admit",
    "confess", "accept", "forgiv", "reunit", "overcom", "breakthrough", "vow",
    "determin", "reveal", "embrace", "encourage", "finally", "at last",
    "understand each other", "makes up", "make up", "opens up", "opens her heart",
    "opens his heart", "true feelings", "heartfelt", "tears", "cries",
)

# Epilogue / coda signals — the trailing wind-down after the conflict is settled.
# Present on the beat we want to AVOID selecting as the conclusion.
_EPILOGUE_MARKERS = (
    "later", "afterward", "next day", "days later", "celebrat", "relax",
    "look forward", "looks forward", "peaceful", "casually", "wraps up",
    "simulation", "reward", "happily enjoy", "enjoy the", "have fun", "day trip",
    "planetarium", "afterparty", "after-party", "encore",
)

# Last resort when there's no Episode Index to reason over: trailing sentences of
# the Overview (the resolution tail), NOT the whole thing and NOT Continuity Facts.
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?。！？])\s+")


def parse_episode_index(summary: str) -> list[tuple[int, str]]:
    """``[(episode_number, beat_prose), ...]`` from the Episode Index section,
    in document order; ``[]`` when the section is absent or unparseable."""
    section = extract_summary_sections(summary).get("Episode Index", "")
    beats: list[tuple[int, str]] = []
    for line in section.splitlines():
        m = _EPISODE_BEAT_RE.match(line.strip())
        if m:
            beats.append((int(m.group(1)), m.group(2).strip()))
    return beats


def _beat_score(text: str) -> int:
    """resolution - epilogue signal count for one beat (higher = more climactic)."""
    low = text.lower()
    res = sum(low.count(m) for m in _RESOLUTION_MARKERS)
    epi = sum(low.count(m) for m in _EPILOGUE_MARKERS)
    return res - epi


def heuristic_conclusion(summary: str) -> tuple[int | None, str]:
    """Keyless, approximate ``(climax_episode, conclusion_text)``.

    Picks the Episode Index beat with the strongest resolution signal (ties break
    toward the *later* episode, since the climax runs late — but not the epilogue).
    Falls back to the Overview's closing sentences when there's no usable index or
    no beat carries a resolution signal."""
    beats = parse_episode_index(summary)
    if beats:
        # argmax on score; on ties prefer the later beat (higher episode number),
        # so a genuine late climax wins but a flat epilogue never outranks it.
        best_num, best_text = max(beats, key=lambda b: (_beat_score(b[1]), b[0]))
        # Only trust a beat with a POSITIVE resolution signal. With no signal the
        # tie-break would hand back the last episode — the epilogue we're avoiding —
        # so fall through to the Overview's resolution tail instead.
        if _beat_score(best_text) > 0:
            return best_num, best_text

    overview = extract_summary_sections(summary).get("Overview", "").strip()
    if overview:
        sentences = [s.strip() for s in _SENTENCE_SPLIT_RE.split(overview) if s.strip()]
        tail = " ".join(sentences[-2:]) if sentences else overview
        return None, tail
    return None, summary.strip()


def _cache_entry_conclusion(entry: dict) -> tuple[int | None, str] | None:
    """Pull ``(climax_episode, conclusion)`` from a conclusions-cache entry, or
    ``None`` if it doesn't carry a usable conclusion."""
    if not isinstance(entry, dict):
        return None
    text = entry.get("conclusion")
    if not isinstance(text, str) or not text.strip():
        return None
    ep = entry.get("climax_episode")
    return (ep if isinstance(ep, int) else None), text.strip()


def format_conclusion(name: str, climax_episode: int | None, conclusion: str) -> str:
    """Display body for the conclusion intent."""
    head = f"How {name} concludes:"
    if climax_episode is not None:
        head = f"How {name} concludes (climax in Episode {climax_episode}):"
    return f"{head}\n\n{conclusion}"


def derive_conclusion(
    summary: str, *, name: str, cache_entry: dict | None = None
) -> str:
    """Focused conclusion body for ``name``'s event.

    Prefers the pre-built semantic ``cache_entry`` (keyless serve of an offline LLM
    read); otherwise falls back to the keyless heuristic over the summary itself.
    Never returns the whole Overview + Continuity Facts (the old "just the
    summary" behaviour)."""
    derived = _cache_entry_conclusion(cache_entry) if cache_entry else None
    if derived is None:
        derived = heuristic_conclusion(summary)
    climax_episode, conclusion = derived
    return format_conclusion(name, climax_episode, conclusion)
