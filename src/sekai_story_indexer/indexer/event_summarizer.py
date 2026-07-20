"""Event-level summarization — one summary per event story (not per episode).

Per-episode/part summaries are near-redundant on the Sekai corpus (one scene per
episode file collapses the Part/Episode tiers). The useful unit of summary is the
WHOLE event story, so this produces a single Tier-2 (event) summary per event by
summarizing all of its scenes in one LLM call — ~209 calls instead of ~5000.

A Tier-1 (unit) rollup over event summaries can be layered on top later.
"""

from __future__ import annotations

import json
import os
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path

from ..models.story import StoryMetadata, StoryNode
from ..source import summary_prompt


def _load_event_meta() -> dict[str, dict]:
    """arc_slug -> event record (for focus character + song context). Optional."""
    for p in (Path("events_index.json"), Path(__file__).resolve().parents[3] / "events_index.json"):
        if p.exists():
            try:
                return {r.get("arc_slug"): r for r in json.loads(p.read_text(encoding="utf-8"))}
            except Exception:
                return {}
    return {}


_EVENT_META = _load_event_meta()


def _summarize_text(name: str, unit: str, body: str, model: str) -> str:
    """Summarize one event with the SHARED prompt (identical to the local/Ollama
    path): rosters + glossary + per-event context + English-pin. ``name`` is the
    arc_slug, used to look up focus character + song."""
    from google import genai
    from google.genai import types

    rec = _EVENT_META.get(name, {})
    prompt = summary_prompt.build_prompt(
        "event_raw",
        unit,
        body[:200_000],
        focus_id=rec.get("focus_character_id"),
        song=rec.get("song_title"),
    )
    client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"))
    # Disable "thinking" so the whole output budget goes to the answer (Gemini-3/2.5
    # flash otherwise burns it on planning and truncates mid-sentence).
    kwargs: dict = {
        "temperature": 0.2,
        "max_output_tokens": 2048,
        "system_instruction": summary_prompt.SYSTEM,
    }
    try:
        kwargs["thinking_config"] = types.ThinkingConfig(thinking_budget=0)
    except Exception:  # older google-genai without ThinkingConfig
        pass
    resp = client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(**kwargs),
    )
    # Shared prompt asks for JSON {summary, characters}; store just the prose.
    return summary_prompt.parse_summary(resp.text or "")["summary"]


def summarize_events(
    raw_nodes: list[StoryNode],
    *,
    story_order=None,
    model: str | None = None,
    summarize: Callable[[str, str, str, str], str] | None = None,
    cache_path: str | Path | None = None,
    log: Callable[[str], None] = print,
) -> list[StoryNode]:
    """One Tier-2 summary StoryNode per event (grouped by parent_year_id).

    ``cache_path`` (e.g. event_summaries.json) makes this a readable, resumable
    text cache keyed by arc_id: cached events are reused (no re-spend), new ones
    appended and written incrementally. The web app's Summaries reader displays it.
    """
    model = model or os.getenv("SEKAI_INGEST_MODEL") or os.getenv("SEKAI_CHAT_MODEL") or "gemini-flash-latest"
    do = summarize or (lambda n, u, b, m: _summarize_text(n, u, b, m))

    cache: dict[str, str] = {}
    cache_file = Path(cache_path) if cache_path else None
    if cache_file and cache_file.exists():
        try:
            cache = json.loads(cache_file.read_text(encoding="utf-8"))
        except Exception:
            cache = {}

    groups: dict[str, list[StoryNode]] = defaultdict(list)
    for node in raw_nodes:
        groups[node.metadata.parent_year_id].append(node)

    summaries: list[StoryNode] = []
    for year_id, nodes in groups.items():
        key = story_order.chronological_node_key if story_order else None
        nodes.sort(key=key) if key else nodes.sort(
            key=lambda n: (n.metadata.episode_number, n.metadata.scene_index)
        )
        base = nodes[0].metadata
        if base.arc_id in cache:  # resumable: reuse cached summary text
            text = cache[base.arc_id]
        else:
            body = "\n".join(n.text for n in nodes)
            try:
                text = do(base.arc_id, base.unit, body, model)
            except Exception as exc:  # one event's failure must not abort the run
                log(f"  skip summary {base.unit}/{base.arc_id}: {exc}")
                continue
            if text and cache_file:
                cache[base.arc_id] = text
                cache_file.write_text(
                    json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8"
                )
        if not text:
            continue
        meta = StoryMetadata(
            unit=base.unit,
            content_type=base.content_type,
            arc_id=base.arc_id,
            story_type=base.story_type,
            episode_name="(event summary)",
            part_name="(event summary)",
            file_path=f"<summary:{year_id}>",
            is_prose=True,
            canonical_story_order=base.canonical_story_order,
            story_order=base.story_order,
            parent_year_id=year_id,
            parent_episode_id=year_id,
            parent_part_id=year_id,
            chunk_id=f"summary:event:{year_id}",
        )
        summaries.append(StoryNode(text=text, metadata=meta, summary_level=2))
        log(f"event summary: {base.unit}/{base.arc_id} ({len(nodes)} scenes)")
    return summaries
