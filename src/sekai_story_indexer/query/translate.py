"""Optional EN→JP query translation for cross-lingual lexical retrieval.

The local engine is lexical (bag-of-words over Japanese scenes), so an English
query only matches after its terms are bridged to Japanese. Rather than
hand-maintain an EN↔JP dictionary per vocabulary category (kinship, occupations,
emotions, …), translate the whole query once with a cheap Gemini call and let the
existing tokenizer match the JP corpus — this generalizes to *any* vocabulary.

Returns ``""`` when unavailable (no key / disabled / error) so callers fall back
to lexical-only, keeping the no-API "runs anywhere" contract intact. Results are
cached per query string (chat asks the same follow-ups repeatedly).
"""

from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()  # pick up GOOGLE_API_KEY from the repo-root .env

_CACHE: dict[str, str] = {}
_CACHE_CAP = 512


def translation_enabled() -> bool:
    """True when a key is present and translation isn't explicitly disabled."""
    return bool(os.getenv("GOOGLE_API_KEY")) and os.getenv("SEKAI_TRANSLATE_QUERY", "1") != "0"


def translate_to_japanese(text: str, *, model: str | None = None) -> str:
    """Best-effort Japanese translation of a query for retrieval; ``""`` when
    unavailable so the caller keeps lexical-only behavior (and evals stay
    deterministic — no key in CI → no translation)."""
    text = (text or "").strip()
    if not text or not translation_enabled():
        return ""
    if text in _CACHE:
        return _CACHE[text]
    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"))
        model_name = model or os.getenv("SEKAI_CHAT_MODEL") or "gemini-flash-latest"
        resp = client.models.generate_content(
            model=model_name,
            contents=(
                "Translate this Project Sekai story question into natural Japanese "
                "for keyword search over Japanese story transcripts. Output ONLY the "
                "Japanese translation — no romaji, no notes, no quotes.\n\n" + text
            ),
            # Ample budget: flash models spend output tokens on internal "thinking",
            # so a tight cap can truncate the (short) translation to nothing.
            config=types.GenerateContentConfig(temperature=0.0, max_output_tokens=1024),
        )
        jp = (resp.text or "").strip()
        if len(_CACHE) >= _CACHE_CAP:
            _CACHE.clear()
        _CACHE[text] = jp
        return jp
    except Exception:
        return ""  # any API/quota/network error -> lexical-only fallback
