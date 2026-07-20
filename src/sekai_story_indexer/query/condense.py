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
    "You rewrite a user's latest chat message into a STANDALONE question for a "
    "story search system. Follow these rules exactly:\n"
    "1. If the latest message is ALREADY self-contained — it names its own subject "
    "and does NOT depend on earlier turns through words like 'that', 'it', 'she', "
    "'he', 'they', 'there', 'the event', or an implied continuation — return it "
    "EXACTLY as written, unchanged. A change of topic must pass through untouched.\n"
    "2. Only if it depends on prior context (pronouns, references, or ellipsis), "
    "rewrite it to name the specific character / unit / event / metric it refers to.\n"
    "3. Never answer the question. Output only the final question, nothing else.\n\n"
    "Examples:\n"
    "History: user: Summarize mafu1\nLatest message: what's the conclusion of that "
    "event\nStandalone question: what's the conclusion of mafu1\n\n"
    "History: user: Summarize mafu1\nLatest message: when is Honami's brother "
    "mentioned\nStandalone question: when is Honami's brother mentioned\n\n"
    "Conversation:\n{convo}\n\nLatest message: {latest}\n\nStandalone question:"
)


def window_history(
    history: list[dict] | None, *, max_turns: int = 6, max_chars: int = 4000
) -> list[dict]:
    """Cap conversation history to the most recent turns within a char budget.

    Keeps context clean and bounded: at most ``max_turns`` recent turns, and drops
    older ones once the running character budget is exceeded (server-side focus
    state carries the durable entity memory, so raw history can stay short)."""
    if not history:
        return []
    windowed: list[dict] = []
    used = 0
    for turn in reversed(history[-max_turns:]):
        text = str(turn.get("text", ""))
        if windowed and used + len(text) > max_chars:
            break
        windowed.append(turn)
        used += len(text)
    windowed.reverse()
    return windowed


def condense(question: str, history: list[dict] | None, *, model: str | None = None) -> str:
    """Return a standalone version of ``question`` given prior turns, or the
    question unchanged if there's no history / no API key / on error."""
    history = window_history(history)
    if not history or not os.getenv("GOOGLE_API_KEY"):
        return question
    try:
        from google import genai
        from google.genai import types

        convo = "\n".join(
            f"{h.get('role', 'user')}: {h.get('text', '')}" for h in history
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
