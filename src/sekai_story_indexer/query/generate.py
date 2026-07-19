"""Natural-language answer generation over retrieved evidence.

The local engine retrieves + quotes (deterministic, no API). This layer adds a
single Gemini call to synthesize a natural-language answer *grounded in the
retrieved excerpts* — giving real chat answers without waiting for the full
Chroma ingest. Falls back to None (caller keeps the extractive answer) when no
API key is configured or the call fails.

This is "RAG-lite": local lexical retrieval + LLM generation. The full engine
(engine.py) does the higher-fidelity embedding-based version.
"""

from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()  # pick up GOOGLE_API_KEY from the repo-root .env

_SYSTEM = (
    "You answer questions about the story of the mobile game Project Sekai "
    "(Hatsune Miku: Colorful Stage). Use ONLY the provided story excerpts. "
    "Write a concise, natural-language answer in the user's language. Refer to "
    "events and characters by name (given in each excerpt's header). If the "
    "excerpts don't contain the answer, say so plainly. Do not invent events, "
    "songs, or character details that aren't in the excerpts."
)


def generation_available() -> bool:
    return bool(os.getenv("GOOGLE_API_KEY"))


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
    model: str | None = None,
) -> str | None:
    """Synthesize a grounded NL answer, or None to fall back to extractive."""
    if not citations or not generation_available():
        return None
    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"))
        model = model or os.getenv("SEKAI_CHAT_MODEL") or "gemini-flash-latest"
        prompt = (
            f"{_SYSTEM}\n\nStory excerpts:\n{_context(citations)}\n\n"
            f"Question: {question}\n\nAnswer:"
        )
        resp = client.models.generate_content(
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(temperature=0.2, max_output_tokens=600),
        )
        text = (resp.text or "").strip()
        return text or None
    except Exception:
        return None  # any API/quota/network error -> caller keeps extractive answer
