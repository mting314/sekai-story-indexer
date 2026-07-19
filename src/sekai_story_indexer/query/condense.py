"""Conversation memory via query condensing.

A follow-up like "How about for each nightcord char?" is meaningless on its own —
it needs the prior turn ("how many lines does Mafuyu have in mafu1"). Standard RAG
pattern: rewrite the latest message into a STANDALONE question using the recent
history, then run intent/scope/retrieval on that. Needs an LLM; with no key it
returns the question unchanged (follow-ups degrade gracefully).
"""

from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()

_PROMPT = (
    "Rewrite the user's latest message into a single standalone question that "
    "includes any context implied by the conversation (character, unit, event, "
    "metric like 'dialogue lines', etc.). Keep it faithful — do not answer it. "
    "Output only the rewritten question.\n\n"
    "Conversation:\n{convo}\n\nLatest message: {latest}\n\nStandalone question:"
)


def condense(question: str, history: list[dict] | None, *, model: str | None = None) -> str:
    """Return a standalone version of ``question`` given prior turns, or the
    question unchanged if there's no history / no API key / on error."""
    if not history or not os.getenv("GOOGLE_API_KEY"):
        return question
    try:
        from google import genai
        from google.genai import types

        convo = "\n".join(
            f"{h.get('role', 'user')}: {h.get('text', '')}" for h in history[-6:]
        )
        client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"))
        model = model or os.getenv("SEKAI_CHAT_MODEL") or "gemini-flash-latest"
        resp = client.models.generate_content(
            model=model,
            contents=_PROMPT.format(convo=convo, latest=question),
            # ample budget: Gemini-3 flash spends output tokens on thinking, which
            # otherwise truncates the rewritten question mid-sentence.
            config=types.GenerateContentConfig(temperature=0.0, max_output_tokens=2048),
        )
        rewritten = (resp.text or "").strip().strip('"')
        return rewritten or question
    except Exception:
        return question
