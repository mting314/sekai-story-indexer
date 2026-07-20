"""Natural-language answer generation over retrieved evidence.

The local engine retrieves + quotes (deterministic, no API). This layer adds a
single Gemini call to synthesize a natural-language answer *grounded in the
retrieved excerpts* — giving real chat answers without waiting for the full
Chroma ingest. Falls back to None (caller keeps the extractive answer) when no
API key is configured or the call fails.

Faithfulness to the original repo: this reuses linkura's packaged answer prompts
(``prompts/answer_system.md`` + glossary injection), so the answer *policy*
matches the full engine. It differs from the full engine only in retrieval
(lexical here vs embeddings) and in not injecting a State Ledger / year summaries
— those belong to the full ``engine.py`` path (``--backend full``). This is
"RAG-lite": local retrieval + the same Gemini generation the original uses.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from dotenv import load_dotenv

from ..prompts import render_system_prompt, render_user_prompt

load_dotenv()  # pick up GOOGLE_API_KEY from the repo-root .env


# Chat-appropriate length control — the original answer prompt has none.
_STYLE = (
    "\n\n# Response style\n"
    "Answer concisely: lead with a direct 2–4 sentence answer to exactly what was "
    "asked. Do NOT give an episode-by-episode recap unless the user explicitly asks "
    "to 'summarize in detail', 'episode by episode', or 'everything'. Prefer plain "
    "prose over headings and bullet lists for short answers.\n"
    "Cite every claim with the bracketed source NUMBER(S) shown in the evidence "
    "(e.g. [1], or [2][3]). Use only those numbers — never invent a citation or "
    "cite an episode title. Cite only the sources you actually draw from — do not "
    "list sources you didn't use.\n"
    "When you state something a character said, felt, or decided, ground it with a "
    "SHORT quote in double quotes, translated into English (do NOT include the "
    "original Japanese in the answer), then the citation — e.g. she resolves to "
    'keep it secret ("I\'ll never tell anyone how I feel") [3]. Keep quotes to one '
    "line; the citation still points to the exact source line."
)


def generation_available() -> bool:
    return bool(os.getenv("GOOGLE_API_KEY"))


def _glossary_str(glossary: dict | None) -> str:
    if not glossary:
        return "None provided."
    lines: list[str] = []
    for section in glossary.values():
        if isinstance(section, dict):
            lines += [f"{jp} = {en}" for jp, en in section.items()]
    return "\n".join(lines) or "None provided."


def _load_glossary() -> dict | None:
    for candidate in (Path("glossary.json"), Path(__file__).resolve().parents[3] / "glossary.json"):
        if candidate.exists():
            try:
                return json.loads(candidate.read_text(encoding="utf-8"))
            except Exception:
                return None
    return None


def _state_ledger_str(arc_ids: set[str] | None = None, path: str = "world_state.json") -> str:
    """Load the State Ledger (built by `indexer extract-state`) and format facts
    relevant to the retrieved arcs. Dormant (returns 'None available') until the
    ledger exists — matching the full engine's grounding once it's built."""
    p = Path(path)
    if not p.exists():
        p = Path(__file__).resolve().parents[3] / path
    if not p.exists():
        return "None available (run `indexer extract-state` to build it)."
    try:
        facts = json.loads(p.read_text(encoding="utf-8")).get("facts", [])
    except Exception:
        return "None available."
    if arc_ids:
        scoped = [f for f in facts if f.get("arc") in arc_ids]
        facts = scoped or facts
    lines = []
    for f in facts[:80]:
        tgt = f.get("target")
        lines.append(
            f"- {f.get('subject','')} {f.get('predicate','')}"
            + (f" {tgt}" if tgt else "")
            + f": {f.get('object','')}"
        )
    return "\n".join(lines) or "None available."


def _context(citations: list[dict], max_chars: int = 120_000) -> str:
    # Large cap: Gemini handles ~1M tokens. 8k truncated a full event to its first
    # ~4 episodes, producing summaries that stopped mid-story. A whole event
    # (~8 episodes) is well under this.
    blocks, used = [], 0
    for c in citations:
        excerpt = c.get("excerpt") or c.get("quote") or ""
        # numbered so the model cites [n] -> maps directly to a source in the UI
        block = f"[{c.get('ref', '?')}] {c.get('label', c.get('arc_id', ''))}\n{excerpt}".strip()
        if used + len(block) > max_chars:
            break
        blocks.append(block)
        used += len(block)
    return "\n\n".join(blocks)


def _build_prompts(
    question: str, citations: list[dict], glossary: dict | None
) -> tuple[str, str]:
    """(system, user) prompts shared by the one-shot and streaming generators."""
    arc_ids = {c.get("arc_id") for c in citations if c.get("arc_id")}
    system = render_system_prompt(
        context_kind="raw",
        glossary=_glossary_str(glossary if glossary is not None else _load_glossary()),
        state_ledger=_state_ledger_str(arc_ids),
        event_summaries="None available (local retrieval mode).",
    )
    system += _STYLE  # the original prompt has no length control; add brevity
    user = render_user_prompt(
        context_kind="raw", question=question, context=_context(citations)
    )
    return system, user


def _resolved_model(model: str | None) -> str:
    return model or os.getenv("SEKAI_CHAT_MODEL") or "gemini-flash-latest"


def generate_answer(
    question: str,
    citations: list[dict],
    *,
    glossary: dict | None = None,
    model: str | None = None,
) -> str | None:
    """Synthesize a grounded NL answer, or None to fall back to extractive."""
    if not citations or not generation_available():
        return None
    try:
        from google import genai
        from google.genai import types

        system, user = _build_prompts(question, citations, glossary)
        client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"))
        # Gemini-3 flash models spend output tokens on internal "thinking", so a
        # small max_output_tokens truncates the visible answer mid-sentence. Give
        # ample budget so thinking + answer both fit.
        resp = client.models.generate_content(
            model=_resolved_model(model),
            contents=user,
            config=types.GenerateContentConfig(
                system_instruction=system, temperature=0.2, max_output_tokens=4096
            ),
        )
        text = (resp.text or "").strip()
        return text or None
    except Exception:
        return None  # any API/quota/network error -> caller keeps extractive answer


def generate_answer_stream(
    question: str,
    citations: list[dict],
    *,
    glossary: dict | None = None,
    model: str | None = None,
):
    """Yield the grounded NL answer as text deltas (real token streaming). Yields
    nothing when generation is unavailable, so the caller falls back to chunking
    the extractive answer."""
    if not citations or not generation_available():
        return
    try:
        from google import genai
        from google.genai import types

        system, user = _build_prompts(question, citations, glossary)
        client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"))
        for chunk in client.models.generate_content_stream(
            model=_resolved_model(model),
            contents=user,
            config=types.GenerateContentConfig(
                system_instruction=system, temperature=0.2, max_output_tokens=4096
            ),
        ):
            piece = getattr(chunk, "text", None)
            if piece:
                yield piece
    except Exception:
        return  # caller falls back to the extractive answer
