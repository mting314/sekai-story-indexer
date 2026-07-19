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


def _context(citations: list[dict], max_chars: int = 8000) -> str:
    blocks, used = [], 0
    for c in citations:
        excerpt = c.get("excerpt") or c.get("quote") or ""
        block = f"### {c.get('label', c.get('arc_id', ''))}\n{excerpt}".strip()
        if used + len(block) > max_chars:
            break
        blocks.append(block)
        used += len(block)
    return "\n\n".join(blocks)


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

        system = render_system_prompt(
            context_kind="raw",
            glossary=_glossary_str(glossary if glossary is not None else _load_glossary()),
            state_ledger="None available (local retrieval mode).",
            year_summaries="None available (local retrieval mode).",
        )
        user = render_user_prompt(
            context_kind="raw", question=question, context=_context(citations)
        )
        client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"))
        model = model or os.getenv("SEKAI_CHAT_MODEL") or "gemini-flash-latest"
        resp = client.models.generate_content(
            model=model,
            contents=user,
            config=types.GenerateContentConfig(
                system_instruction=system, temperature=0.2, max_output_tokens=700
            ),
        )
        text = (resp.text or "").strip()
        return text or None
    except Exception:
        return None  # any API/quota/network error -> caller keeps extractive answer
