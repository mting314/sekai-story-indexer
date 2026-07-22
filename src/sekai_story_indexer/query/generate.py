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
import re
import time
from pathlib import Path

from dotenv import load_dotenv

from ..prompts import render_system_prompt, render_user_prompt

load_dotenv()  # pick up GOOGLE_API_KEY from the repo-root .env


# Quota/spend-cap circuit breaker. Once a generation call comes back with a
# quota/429, further calls are pointless until the window resets — so we trip a
# breaker and skip generation (callers fall back to the extractive answer) for a
# cooldown, instead of paying a doomed round-trip on every query. Reset on the
# next success. Cooldown is best-effort (we don't know the real reset time).
_QUOTA_COOLDOWN_S = int(os.getenv("SEKAI_QUOTA_COOLDOWN", "900"))  # 15 min
_quota_tripped_until = 0.0
_QUOTA_MARKERS = ("429", "resource_exhausted", "quota", "spend", "exceeded")


def _is_quota_error(exc: Exception) -> bool:
    s = str(exc).lower()
    return any(m in s for m in _QUOTA_MARKERS)


def _trip_quota_breaker() -> None:
    global _quota_tripped_until
    _quota_tripped_until = time.time() + _QUOTA_COOLDOWN_S


def _clear_quota_breaker() -> None:
    global _quota_tripped_until
    _quota_tripped_until = 0.0


def quota_paused() -> bool:
    """True while the quota breaker is tripped — generation is being skipped in
    favor of extractive retrieval. Lets callers surface a 'quota' notice."""
    return time.time() < _quota_tripped_until


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


# Per-citation grounding: after the prose, the model emits the exact verbatim JP
# source line it grounded each [n] on, so the UI can highlight that line in the
# transcript. Kept out of the visible answer (parsed off / held back from stream).
_SOURCES_MARK = "@@SOURCES@@"
_GROUNDING = (
    "\n\nAfter the answer, output a line containing exactly `@@SOURCES@@`, then for "
    "each citation number [n] you used, a line formatted `[n] <line>` where <line> is "
    "the EXACT verbatim source line from the evidence above that you grounded that "
    "citation on — copied character-for-character in its original Japanese (no "
    "translation, no paraphrase, a single physical line). Skip any [n] you did not "
    "ground on one specific line."
)
_GROUNDING_LINE_RE = re.compile(r"^\s*\[(\d+)\]\s*(.+?)\s*$")


def _parse_grounding(text: str) -> tuple[str, dict[int, str]]:
    """Split a generated answer into (visible prose, {ref -> verbatim source line})."""
    idx = text.find(_SOURCES_MARK)
    if idx < 0:
        return text.strip(), {}
    prose = text[:idx].strip()
    grounding: dict[int, str] = {}
    for line in text[idx + len(_SOURCES_MARK):].splitlines():
        m = _GROUNDING_LINE_RE.match(line)
        if m:
            grounding.setdefault(int(m.group(1)), m.group(2).strip())
    return prose, grounding


def generation_available() -> bool:
    """Whether to attempt LLM generation: a key is set AND the quota breaker isn't
    tripped. When it returns False mid-cooldown, callers keep the extractive answer
    without a doomed API round-trip."""
    return bool(os.getenv("GOOGLE_API_KEY")) and not quota_paused()


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
    user += _GROUNDING  # ask for the verbatim per-citation source lines
    return system, user


def _resolved_model(model: str | None) -> str:
    return model or os.getenv("SEKAI_CHAT_MODEL") or "gemini-flash-latest"


# Flash "thinking" models bill internal reasoning as output tokens, and left
# unbounded a detailed summary spent ~3.8k tokens *thinking* — both slow and the
# dominant cost. `thinking_level="low"` cuts that to ~0.7k with no loss in answer
# quality (grounded synthesis over supplied citations needs little deliberation),
# and — unlike `thinking_budget`, which this model overshoots — it's actually
# honored. `max_output_tokens` is a generous ceiling (only *emitted* tokens bill,
# so a high cap costs nothing) that keeps the answer from truncating mid-sentence.
_MAX_OUTPUT_TOKENS = 8192
_THINKING_LEVEL = "low"


def _generation_config(types, system: str, *, thinking: bool = True):
    """Shared GenerateContentConfig for the batch + streaming answer calls. Pass
    ``thinking=False`` to omit ``thinking_config`` (retry path for models that
    reject ``thinking_level``)."""
    kwargs = dict(
        system_instruction=system, temperature=0.2, max_output_tokens=_MAX_OUTPUT_TOKENS
    )
    # thinking_config is newer; degrade gracefully on older google-genai builds.
    tc = getattr(types, "ThinkingConfig", None)
    if thinking and tc is not None:
        try:
            kwargs["thinking_config"] = tc(thinking_level=_THINKING_LEVEL)
        except Exception:
            pass
    return types.GenerateContentConfig(**kwargs)


def generate_answer(
    question: str,
    citations: list[dict],
    *,
    glossary: dict | None = None,
    model: str | None = None,
) -> tuple[str, dict[int, str]] | None:
    """Synthesize a grounded NL answer as ``(prose, {ref -> source line})``, or None
    to fall back to extractive."""
    if not citations or not generation_available():
        return None
    try:
        from google import genai
        from google.genai import types

        system, user = _build_prompts(question, citations, glossary)
        client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"))
        try:
            resp = client.models.generate_content(
                model=_resolved_model(model),
                contents=user,
                config=_generation_config(types, system),
            )
        except Exception as first:
            if _is_quota_error(first):  # cap hit -> trip breaker, don't retry
                _trip_quota_breaker()
                return None
            # a model that rejects thinking_level still generates without it
            resp = client.models.generate_content(
                model=_resolved_model(model),
                contents=user,
                config=_generation_config(types, system, thinking=False),
            )
        text = (resp.text or "").strip()
        if not text:
            return None
        _clear_quota_breaker()  # a success means the cap has cleared
        return _parse_grounding(text)
    except Exception as exc:
        if _is_quota_error(exc):
            _trip_quota_breaker()
        return None  # any API/quota/network error -> caller keeps extractive answer


def generate_answer_stream(
    question: str,
    citations: list[dict],
    *,
    glossary: dict | None = None,
    model: str | None = None,
    grounding_out: dict[int, str] | None = None,
):
    """Yield the grounded NL answer as text deltas (real token streaming), holding
    back the trailing ``@@SOURCES@@`` grounding block so it never reaches the user;
    the parsed ``{ref -> source line}`` map is written into ``grounding_out``. Yields
    nothing when generation is unavailable, so the caller falls back to chunking the
    extractive answer."""
    if not citations or not generation_available():
        return
    try:
        from google import genai
        from google.genai import types

        system, user = _build_prompts(question, citations, glossary)
        client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"))
        full = ""
        emitted = 0
        cut: int | None = None  # index where @@SOURCES@@ begins (stop emitting there)
        hold = len(_SOURCES_MARK) - 1  # never emit a partial marker prefix
        # Try with thinking; if a model rejects thinking_level it fails before any
        # token, so retry once without it (only while nothing has been emitted).
        for with_thinking in (True, False):
            try:
                for chunk in client.models.generate_content_stream(
                    model=_resolved_model(model),
                    contents=user,
                    config=_generation_config(types, system, thinking=with_thinking),
                ):
                    piece = getattr(chunk, "text", None)
                    if not piece:
                        continue
                    full += piece
                    if cut is None:
                        idx = full.find(_SOURCES_MARK)
                        if idx >= 0:
                            cut = idx
                    limit = cut if cut is not None else max(emitted, len(full) - hold)
                    if limit > emitted:
                        yield full[emitted:limit]
                        emitted = limit
                break  # stream completed
            except Exception as exc:
                if _is_quota_error(exc):  # cap hit -> trip breaker, don't retry
                    _trip_quota_breaker()
                    raise
                if with_thinking and emitted == 0:
                    full, cut = "", None  # nothing streamed yet -> safe to retry
                    continue
                raise  # mid-stream failure or retry also failed
        if cut is None and len(full) > emitted:  # no marker -> flush the held-back tail
            yield full[emitted:]
        _clear_quota_breaker()  # a completed stream means the cap has cleared
        _, grounding = _parse_grounding(full)
        if grounding_out is not None:
            grounding_out.update(grounding)
    except Exception as exc:
        if _is_quota_error(exc):
            _trip_quota_breaker()
        return  # caller falls back to the extractive answer
