import json
import os
from collections import defaultdict
from typing import Any

from ..console import safe_print
from ..database import create_generation_text_agent
from ..models.story import StoryNode
from ..story_order import StoryOrder, default_story_order
from .manifest import (
    SUMMARY_CACHE_SCHEMA_VERSION,
    SummaryCacheContext,
    hash_text,
    stable_hash,
)
from .processor import episode_number_from_names

# Section constants + the pure extractor live in a dependency-free module so the
# reader/web app can parse summaries without the generation stack; re-exported
# here for back-compat.
from .summary_sections import (
    EPISODE_SUMMARY_SECTIONS,
    EVENT_SUMMARY_SECTIONS,
    KNOWN_SUMMARY_SECTIONS,
    PART_SUMMARY_SECTIONS,
    SUMMARY_SECTIONS_BY_LEVEL,
    extract_summary_sections,
)

SUMMARIZATION_PROMPT_VERSION = "3"

__all__ = [
    "PART_SUMMARY_SECTIONS",
    "EPISODE_SUMMARY_SECTIONS",
    "EVENT_SUMMARY_SECTIONS",
    "SUMMARY_SECTIONS_BY_LEVEL",
    "KNOWN_SUMMARY_SECTIONS",
    "extract_summary_sections",
    "SUMMARIZATION_PROMPT_VERSION",
    "HierarchicalSummarizer",
    "trim_previous_summary_context",
]


def trim_previous_summary_context(prev_summary: str | None) -> str | None:
    """Keep only previous sections useful for resolving current references."""
    if not prev_summary:
        return None

    sections = extract_summary_sections(prev_summary)
    trimmed_parts = []
    for label in ("Overview", "Continuity Facts"):
        content = sections.get(label)
        if content:
            trimmed_parts.append(f"{label}:\n{content}")

    if not trimmed_parts:
        return None

    return "\n\n".join(trimmed_parts)


def _summary_input_label(level_name: str) -> str:
    if level_name == "Part":
        return "CURRENT PART TEXT (RAW PARSED STORY TEXT)"
    if level_name == "Episode":
        return "CURRENT EPISODE INPUT (STRUCTURED PART SUMMARIES)"
    if level_name == "Event":
        return "CURRENT EVENT TEXT (RAW STORY SCENES, ONE PER EPISODE)"
    return f"CURRENT {level_name.upper()} INPUT"


def _summary_input_instructions(level_name: str) -> str:
    if level_name == "Part":
        return (
            "The current Part input is raw parsed story text. Summarize only the current "
            "part's source text, using previous context only when needed to understand references."
        )
    if level_name == "Episode":
        return (
            "The current Episode input is multiple structured Part summaries. Synthesize across "
            "the child Part summaries into one episode-level summary. Do not concatenate, copy, "
            "or preserve child section structures verbatim."
        )
    if level_name == "Event":
        return (
            "The current Event input is the event's raw story scenes in reading order, each "
            "marked '## Episode N'. Summarize the whole event into an event-level narrative "
            "plus a per-episode index. Do not copy scene text verbatim."
        )
    return f"The current input is a {level_name}."


def _global_summary_format_rules() -> str:
    return """Global formatting rules:
- Write all summaries in clear, concise English.
- Use official glossary translations whenever available.
- Use exactly the required section labels for the tier.
- Always emit every required section.
- If a bullet-list section has no applicable entries, write exactly `- None`.
- Do not use Markdown headings, bold text, numbered lists, tables, or extra sections.
- Use bullet lists only under list sections.
- Important Terms should include glossary-mapped terms when relevant, plus salient new entities encountered in the text. It should not dump the full glossary."""


def _summary_format_instructions(level_name: str) -> str:
    if level_name == "Part":
        return """Required Part summary format:

Overview:
[Current-style detailed prose summary, usually 4-8 paragraphs for substantive parts; shorter only if the source is very short. Focus on concrete scene progression, character actions, locations, and immediate outcomes. Do not compress for brevity.]

Key Events:
- Chronological event.
- Chronological event.
- Chronological event.

Character Developments:
- Character Name: concrete emotional, relational, or goal-state change.
- Character Name: concrete emotional, relational, or goal-state change.

Continuity Facts:
- Stable fact, promise, conflict, reveal, relationship change, location, event result, or setup for later.
- Stable fact, promise, conflict, reveal, relationship change, location, event result, or setup for later.

Important Terms:
- Characters, unit names, locations, events, songs, apps, competitions, notable Japanese/English aliases."""

    if level_name == "Episode":
        return """Required Episode summary format:

Overview:
[A detailed prose recap of the whole episode. Preserve the current useful length. Focus on the episode's central conflict, progression, turning points, and resolution. Do not compress into a short abstract.]

Part Index:
- Part 1: central part event, conflict, or outcome in one line.
- Part 2: central part event, conflict, or outcome in one line.
- Interlude: brief recap of the interlude beat in one line.

Episode Arc:
- Setup: ...
- Escalation: ...
- Turning Point: ...
- Resolution: ...
- Aftermath / Setup: ...

Character Developments:
- Character Name: what changes for them across the episode.
- Character Name: what changes for them across the episode.

Relationship / Unit Developments:
- Pair, group, or unit: how the relationship or dynamic changes.

Continuity Facts:
- Durable facts established by this episode.
- Promises, conflicts, decisions, outcomes, new goals, event results.

Important Terms:
- Characters, units, songs, events, locations, apps, competitions, aliases central to this episode.

Episode Part Index label rules:
- Use `Part N:` for numbered parts.
- Use stable English labels for non-numbered parts or interludes, such as `Interlude:` or `Ending:`.
- Do not use raw Japanese part titles as Part Index bullet labels when a generic label is available."""

    if level_name == "Event":
        return """Required Event summary format:

Overview:
[A detailed prose summary of the event's overall narrative movement, club status changes, competitions, graduations/transitions, and recurring themes. Keep episode boundaries clear; do not force unrelated episodes into larger arcs.]

Episode Index:
- Episode 1: central conflict and outcome in one line, about 20-30 words.
- Episode 2: central conflict and outcome in one line, about 20-30 words.

Character Trajectories:
- Character Name: growth across the event, setbacks, role changes, relationships, goals.
- Character Name: growth across the event, setbacks, role changes, relationships, goals.

Unit / Club State:
- Unit or club: membership, creative direction, conflicts, achievements, public status.

Continuity Facts:
- Event-level or cross-episode facts only: final states, competition results, graduations/transitions, promises, unresolved threads, and rare genuine multi-episode arcs.

Important Terms:
- Up to 15 major recurring or event-routing terms.

Event Episode Index label rules:
- Use `Episode N:` for numbered main episodes.
- Use stable English labels for non-numbered special entries, such as `Interlude:` or `Special:`.
- Do not use raw Japanese episode titles as Episode Index bullet labels.
- Japanese or official episode titles and aliases may still be preserved in prose or Important Terms when retrieval-useful."""

    raise ValueError(f"Unsupported summary level: {level_name}")


def episode_sort_key(ep_key_tuple: tuple) -> tuple:
    arc_id, story_type, episode_name = ep_key_tuple
    return default_story_order().chronological_episode_key(arc_id, story_type, episode_name)


def _load_cache(cache_file: str) -> dict[str, Any]:
    if not os.path.exists(cache_file):
        return {}
    with open(cache_file, encoding="utf-8") as file:
        loaded = json.load(file)
    if not isinstance(loaded, dict):
        return {}
    return loaded


def _save_cache(cache_file: str, cache: dict[str, Any]) -> None:
    with open(cache_file, "w", encoding="utf-8") as file:
        json.dump(cache, file, ensure_ascii=False, indent=2)


def _cached_summary(cache: dict[str, Any], cache_key: str, fingerprint: str) -> str | None:
    entry = cache.get(cache_key)
    if not isinstance(entry, dict):
        return None
    if entry.get("fingerprint") != fingerprint:
        return None
    summary = entry.get("summary")
    if not isinstance(summary, str):
        return None
    return summary


def _store_cached_summary(
    cache: dict[str, Any],
    cache_key: str,
    *,
    summary: str,
    fingerprint: str,
    inputs: dict[str, Any],
) -> None:
    cache[cache_key] = {
        "schema_version": SUMMARY_CACHE_SCHEMA_VERSION,
        "fingerprint": fingerprint,
        "summary": summary,
        "inputs": inputs,
    }


class HierarchicalSummarizer:
    """Generates rolling summaries for stories to build the RAG hierarchy."""

    def __init__(
        self,
        glossary: dict | None = None,
        story_order: StoryOrder | None = None,
        cache_context: SummaryCacheContext | None = None,
    ):
        self.glossary = glossary
        self.story_order = story_order or default_story_order()
        self.cache_context = cache_context

    def _build_summary_prompt(
        self,
        current_text: str,
        prev_summary: str | None = None,
        level_name: str = "Part",
    ) -> tuple[str, str]:
        """Build the system instructions and prompt for a tier-specific summary."""
        system_content = (
            "You are an expert archivist and translator indexing a Japanese narrative story. "
            "You must write all summaries in clear, concise ENGLISH.\n"
            f"Summarize the following {level_name}. Focus on plot progression, character actions, locations, continuity, and retrieval-useful named entities."
        )

        if self.glossary:
            system_content += "\n\n--- OFFICIAL GLOSSARY (MANDATORY TRANSLATIONS) ---\n"
            system_content += "When translating or referencing names and terms, you MUST use the following English equivalents:\n"
            for category, terms in self.glossary.items():
                system_content += f"\n{category.replace('_', ' ').upper()}:\n"
                for jp, en in terms.items():
                    system_content += f" - {jp} -> {en}\n"

        prev_context = trim_previous_summary_context(prev_summary)
        prompt_parts = []
        if prev_context:
            prompt_parts.append(f"--- PREVIOUS CONTEXT (For Continuity) ---\n{prev_context}")

        prompt_parts.extend(
            [
                f"--- {_summary_input_label(level_name)} ---\n{current_text}",
                _summary_input_instructions(level_name),
            ]
        )

        if prev_context:
            prompt_parts.append(
                "Use previous context only to resolve references, pronouns, chronology, and ongoing situations needed to understand the current input. Do not summarize previous events again, do not copy previous sections, and do not include prior events unless the current input directly depends on them."
            )

        prompt_parts.extend(
            [
                _global_summary_format_rules(),
                _summary_format_instructions(level_name),
                f"Write the {level_name} summary now using exactly the required format.",
            ]
        )

        return system_content, "\n\n".join(prompt_parts)

    def _generate_rolling_summary(
        self,
        current_text: str,
        prev_summary: str | None = None,
        level_name: str = "Part",
    ) -> str:
        """Calls the LLM to generate a summary using previous context to prevent drift."""
        system_content, prompt = self._build_summary_prompt(current_text, prev_summary, level_name)

        result = create_generation_text_agent(system_content).run_sync(prompt)
        return result.output.strip()

    def _base_fingerprint_inputs(self, level: str) -> dict[str, Any]:
        if self.cache_context is None:
            return {
                "level": level,
                "summary_cache_schema_version": SUMMARY_CACHE_SCHEMA_VERSION,
                "summarization_prompt_version": SUMMARIZATION_PROMPT_VERSION,
                "glossary_hash": stable_hash(self.glossary),
                "chat_model": "unconfigured",
                "embedding_model": "unconfigured",
                "parser_version": "unconfigured",
            }
        inputs = {
            "level": level,
            "summary_cache_schema_version": self.cache_context.summary_cache_schema_version,
            "summarization_prompt_version": self.cache_context.summarization_prompt_version,
            "glossary_hash": self.cache_context.glossary_hash,
            "chat_model": self.cache_context.chat_model,
            "embedding_model": self.cache_context.embedding_model,
            "parser_version": self.cache_context.parser_version,
        }
        if (
            self.cache_context.generation_provider != "google"
            or self.cache_context.generation_model != self.cache_context.chat_model
        ):
            inputs["generation_provider"] = self.cache_context.generation_provider
            inputs["generation_model"] = self.cache_context.generation_model
        return inputs

    def _source_file_hashes_for_nodes(self, nodes: list[StoryNode]) -> dict[str, str]:
        grouped_text: dict[str, list[str]] = defaultdict(list)
        for node in nodes:
            grouped_text[node.metadata.file_path].append(node.text)

        hashes = {}
        for file_path, texts in sorted(grouped_text.items()):
            if (
                self.cache_context is not None
                and file_path in self.cache_context.source_file_hashes
            ):
                hashes[file_path] = self.cache_context.source_file_hashes[file_path]
            else:
                hashes[file_path] = hash_text("\n\n---\n\n".join(texts))
        return hashes

    def _raw_cache_inputs(
        self,
        *,
        level: str,
        scenes: list[StoryNode],
        text: str,
        prev_summary: str | None,
    ) -> dict[str, Any]:
        """Fingerprint for a summary generated directly from raw scenes (tracks the
        source files so a story edit invalidates the cached summary)."""
        return {
            **self._base_fingerprint_inputs(level),
            "source_file_hashes": self._source_file_hashes_for_nodes(scenes),
            "source_text_hash": hash_text(text),
            "previous_summary_hash": hash_text(prev_summary) if prev_summary else "",
        }

    def summarize_hierarchy(
        self, raw_nodes: list[StoryNode], cache_file: str = "summaries_cache.json", *, limit: int = 0
    ) -> list[StoryNode]:
        """Build the event-tier summaries. Sekai stores one scene per episode, so the
        Part/Episode sub-tiers are redundant — each event is summarized once, directly
        from its raw scenes. Returns the Tier-1 (Event) summary nodes."""
        safe_print("\n--- Generating Tier 1 (Event) Summaries ---")
        return self.summarize_events(raw_nodes, cache_file, limit=limit)

    def summarize_events(
        self, raw_nodes: list[StoryNode], cache_file: str = "summaries_cache.json", *, limit: int = 0
    ) -> list[StoryNode]:
        """One Tier-1 (Event) summary per event, generated in a single call from the
        event's raw scenes in reading order. Scenes are grouped by arc and labelled
        '## Episode N' so the model can still emit a per-episode index. A rolling
        previous-event summary is threaded for cross-event continuity.

        ``limit`` (>0) stops after generating that many *new* summaries — cached
        predecessors are still loaded (free) so continuity threading is preserved,
        making it a resumable, cost-bounded knob for a partial build."""
        cache = _load_cache(cache_file)
        generated = 0

        events: dict[str, list[StoryNode]] = defaultdict(list)
        for node in raw_nodes:
            events[node.metadata.arc_id].append(node)

        summary_nodes: list[StoryNode] = []
        sorted_arcs = sorted(
            events.keys(),
            key=lambda arc_id: min(
                self.story_order.chronological_node_key(node) for node in events[arc_id]
            ),
        )

        prev_summary = None
        for arc_id in sorted_arcs:
            cache_key = f"EVENT|{arc_id}"
            scenes = sorted(events[arc_id], key=self.story_order.chronological_node_key)

            base_meta = scenes[0].metadata.model_copy(deep=True)
            base_meta.episode_name = "ALL_EPISODES"
            base_meta.part_name = "ALL_PARTS"
            base_meta.scene_index = -1

            # Numbered episode markers (never the raw slug) so the single event call
            # can still emit a per-episode index.
            blocks = []
            for node in scenes:
                num = episode_number_from_names(
                    node.metadata.episode_name, node.metadata.part_name
                )
                blocks.append(f"## Episode {num}\n{node.text}" if num else node.text)
            combined_text = "\n\n---\n\n".join(blocks)

            prev_context = trim_previous_summary_context(prev_summary)
            cache_inputs = self._raw_cache_inputs(
                level="event",
                scenes=scenes,
                text=combined_text,
                prev_summary=prev_context,
            )
            fingerprint = stable_hash(cache_inputs)
            cached = _cached_summary(cache, cache_key, fingerprint)

            if cached is not None:
                safe_print(f"Loading cached event summary for {cache_key}...")
                current_summary = cached
            else:
                if limit and generated >= limit:
                    safe_print(f"Reached --limit {limit}; stopping before {cache_key}.")
                    break
                safe_print(f"Summarizing Event: {cache_key}...")
                current_summary = self._generate_rolling_summary(
                    current_text=combined_text,
                    prev_summary=prev_context,
                    level_name="Event",
                )
                generated += 1
                _store_cached_summary(
                    cache,
                    cache_key,
                    summary=current_summary,
                    fingerprint=fingerprint,
                    inputs=cache_inputs,
                )
                _save_cache(cache_file, cache)

            summary_nodes.append(
                StoryNode(text=current_summary, metadata=base_meta, summary_level=1)
            )
            prev_summary = current_summary

        return summary_nodes
