"""Focused event conclusions — the *keyed* build side (LLM 'refine' pass).

Reads each event's pre-built summary from ``summaries_cache.json`` and, with a
single cheap LLM call over just the **Overview + Episode Index** (~a dozen lines,
NOT the full scenes), identifies the climax episode and writes a short "how it
ends". Results land in ``conclusions_cache.json`` and are then served *keyless*
(no API call at query time) by ``query/conclusion.py``.

Why a second pass instead of baking this into the summary: the input is tiny, so
it's far cheaper than regenerating the summaries (which are spend-cap-gated), and
it can run incrementally over the summaries we already have. Fingerprinted on the
summary content, so a resummarised event re-derives its conclusion automatically.

Needs the generation deps (pydantic-ai + a provider); the CLI wraps this and
treats a 429/spend-cap stop as resumable, exactly like ``sekai summarize``.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

from ..console import safe_print
from ..database import create_generation_text_agent
from ..indexer.summary_sections import extract_summary_sections
from ..query.conclusion import CONCLUSION_PROMPT_VERSION
from .manifest import stable_hash

CONCLUSIONS_CACHE_SCHEMA_VERSION = "1"

_SYSTEM_INSTRUCTIONS = (
    "You summarize how a Project Sekai event story ENDS. You are given an event's "
    "Overview and its per-episode Episode Index. Project Sekai events almost always "
    "close on an epilogue (a live performance, an afterparty, a relaxed Sekai reward "
    "scene) AFTER the real climax — so the LAST episode is usually NOT the "
    "conclusion. Identify the episode where the central conflict actually resolves "
    "(the climax / emotional turn), then write a focused 'how it ends'.\n\n"
    "Respond in EXACTLY this format, nothing else:\n"
    "Climax: Episode <number>\n"
    "<2-4 sentences: the climactic turn and how things resolve. No preamble, no "
    "'In this event'. Do not restate the whole plot.>"
)

_CLIMAX_LINE_RE = re.compile(r"^\s*Climax:\s*Episode\s*(\d+)\s*$", re.IGNORECASE)


def _load_cache(cache_file: str) -> dict[str, Any]:
    if not os.path.exists(cache_file):
        return {}
    with open(cache_file, encoding="utf-8") as file:
        loaded = json.load(file)
    return loaded if isinstance(loaded, dict) else {}


def _save_cache(cache_file: str, cache: dict[str, Any]) -> None:
    with open(cache_file, "w", encoding="utf-8") as file:
        json.dump(cache, file, ensure_ascii=False, indent=2)


def _summary_text(entry: Any) -> str:
    """A summaries-cache value is either the summary string or ``{summary: ...}``."""
    if isinstance(entry, dict):
        return entry.get("summary") or ""
    return entry if isinstance(entry, str) else ""


def _conclusion_input(summary: str) -> str:
    """The tiny prompt input: Overview + Episode Index only."""
    sections = extract_summary_sections(summary)
    parts = []
    if sections.get("Overview"):
        parts.append("Overview:\n" + sections["Overview"])
    if sections.get("Episode Index"):
        parts.append("Episode Index:\n" + sections["Episode Index"])
    return "\n\n".join(parts) or summary


def _parse_output(text: str) -> tuple[int | None, str]:
    """``(climax_episode, conclusion)`` from the model's two-part response."""
    lines = text.strip().splitlines()
    climax: int | None = None
    body_start = 0
    if lines:
        m = _CLIMAX_LINE_RE.match(lines[0])
        if m:
            climax = int(m.group(1))
            body_start = 1
    conclusion = "\n".join(lines[body_start:]).strip() or text.strip()
    return climax, conclusion


def _fingerprint(summary: str, model: str, provider: str) -> str:
    return stable_hash(
        {
            "schema_version": CONCLUSIONS_CACHE_SCHEMA_VERSION,
            "prompt_version": CONCLUSION_PROMPT_VERSION,
            "summary": summary,
            "generation_model": model,
            "generation_provider": provider,
        }
    )


class ConclusionExtractor:
    """Derives focused conclusions from pre-built event summaries (one LLM call each)."""

    def __init__(self, *, generation_model: str = "", generation_provider: str = ""):
        self.generation_model = generation_model
        self.generation_provider = generation_provider

    def _generate(self, summary: str) -> tuple[int | None, str]:
        result = create_generation_text_agent(_SYSTEM_INSTRUCTIONS).run_sync(
            _conclusion_input(summary)
        )
        return _parse_output(result.output)

    def extract(
        self,
        summaries_cache: dict[str, Any],
        cache_file: str = "conclusions_cache.json",
        *,
        limit: int = 0,
        skip_existing: bool = False,
    ) -> dict[str, Any]:
        """One conclusion per ``EVENT|<arc>`` summary. Fingerprint-cached + resumable;
        ``limit`` (>0) stops after that many NEW conclusions; ``skip_existing`` keeps
        an already-present entry even if its fingerprint changed."""
        cache = _load_cache(cache_file)
        generated = 0
        event_keys = sorted(k for k in summaries_cache if k.startswith("EVENT|"))

        for cache_key in event_keys:
            summary = _summary_text(summaries_cache[cache_key])
            if not summary.strip():
                continue
            fingerprint = _fingerprint(
                summary, self.generation_model, self.generation_provider
            )
            existing = cache.get(cache_key)
            if isinstance(existing, dict) and existing.get("fingerprint") == fingerprint:
                safe_print(f"Loading cached conclusion for {cache_key}...")
                continue
            if isinstance(existing, dict) and skip_existing:
                safe_print(f"Keeping existing {cache_key} (--skip-existing).")
                continue
            if limit and generated >= limit:
                safe_print(f"Reached --limit {limit}; stopping before {cache_key}.")
                break

            safe_print(f"Concluding Event: {cache_key}...")
            climax_episode, conclusion = self._generate(summary)
            cache[cache_key] = {
                "schema_version": CONCLUSIONS_CACHE_SCHEMA_VERSION,
                "fingerprint": fingerprint,
                "climax_episode": climax_episode,
                "conclusion": conclusion,
            }
            generated += 1
            _save_cache(cache_file, cache)

        return cache
