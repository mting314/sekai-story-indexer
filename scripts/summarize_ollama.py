#!/usr/bin/env python3
"""Hierarchical local summarizer via Ollama — runs anywhere Ollama runs.

Builds the summary tree bottom-up, mirroring the original linkura summarizer:

    Episode summary (per NN.md, rolling context within the event)
      -> Event summary  (synthesizes the event's episode summaries)
        -> Unit summary (synthesizes a group's event summaries)

Each summary is inline-tagged markdown: every character mention is written as
`Name{char_id=ID}`, so the reader colors names by exact span + id. Output files
(all resumable, written after each item):

    episode_summaries.json  {arc: {ep_key: {summary, characters}}}
    event_summaries.json    {arc: {summary, characters}}
    unit_summaries.json     {unit: {summary, characters}}

No pip dependencies (standard library only). Setup on Windows:
  1. Install Ollama:  https://ollama.com/download   (auto-uses your GPU)
  2. Pull a model:    ollama pull qwen2.5:7b     (or :14b for better quality)
  3. Run:             python summarize_ollama.py --only broken

Options:
  --tier {all,episode,event,unit}   which tiers to (re)build (default all)
  --only {broken,missing,all}       which items within a tier (default broken)
  --model NAME                      Ollama model tag (default qwen2.5:7b)
  --arc SLUG                        just one event (its episodes + event)
  --limit N                         cap events processed (0 = no cap)
  --host URL                        Ollama base URL (default http://localhost:11434)

Prompt strings are kept in sync with
src/sekai_story_indexer/source/summary_prompt.py (canonical).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

_HERE = Path(__file__).resolve().parent


def _base() -> Path:
    for d in (_HERE, _HERE.parent, Path.cwd()):
        if (d / "story").exists():
            return d
    return _HERE


BASE = _base()
STORY = BASE / "story"


def _find(name: str) -> Path:
    for p in (BASE / name, BASE / "webapp" / "static" / name, _HERE / name):
        if p.exists():
            return p
    return BASE / name


META = _find("meta.json")
EPISODE_CACHE = BASE / "episode_summaries.json"
EVENT_CACHE = BASE / "event_summaries.json"
UNIT_CACHE = BASE / "unit_summaries.json"
BODY_CHARS = 24000

_meta = json.loads(META.read_text(encoding="utf-8"))
ID_TO_EN = {int(cid): c["en"] for cid, c in _meta["characters"].items()}
VALID_IDS = set(ID_TO_EN)
UNIT_NAMES = {slug: u["name"] for slug, u in _meta.get("units", {}).items()}

_UNIT_ORDER = [
    "leo_need", "more_more_jump", "vivid_bad_squad",
    "wonderlands_showtime", "nightcord", "virtual_singer",
]
_roster: dict[str, list[tuple[int, str]]] = {}
for _cid, _c in _meta["characters"].items():
    _roster.setdefault(_c.get("unit", "other"), []).append((int(_cid), _c["en"]))
ROSTER_TEXT = "\n".join(
    f"- {UNIT_NAMES.get(slug, slug)}: "
    + ", ".join(f"{en}{{char_id={cid}}}" for cid, en in _roster[slug])
    for slug in _UNIT_ORDER
    if slug in _roster
)

_glossary = {}
for _gp in (BASE / "glossary.json", _HERE / "glossary.json"):
    if _gp.exists():
        try:
            _glossary = json.loads(_gp.read_text(encoding="utf-8"))
        except Exception:
            _glossary = {}
        break
_gl = []
for _cat, _terms in _glossary.items():
    if isinstance(_terms, dict) and _terms:
        _gl.append(f"{_cat.replace('_', ' ').upper()}:")
        _gl += [f" - {jp} -> {en}" for jp, en in _terms.items()]
GLOSSARY_TEXT = "\n".join(_gl)

_EVENT_META: dict[str, dict] = {}
for _p in (BASE / "events_index.json", _HERE / "events_index.json"):
    if _p.exists():
        try:
            _EVENT_META = {r.get("arc_slug"): r for r in json.loads(_p.read_text(encoding="utf-8"))}
        except Exception:
            _EVENT_META = {}
        break

SYSTEM = (
    "You are an expert archivist and translator indexing Japanese Project Sekai "
    "(Hatsune Miku: Colorful Stage!) event stories. You write all summaries in "
    "clear, concise ENGLISH — never Japanese.\n\n"
    "MAIN CAST: the roster lists the 26 main characters with their ids and groups. "
    "For a listed character, use their exact English name, never move them to a "
    "different group, and tag each mention with their id, e.g. "
    "`Kohane Azusawa{char_id=13}` (and `Kohane{char_id=13}` later). Never tag a "
    "character with another character's id.\n"
    "SIDE CHARACTERS: minor, guest, and one-off characters who are NOT on the "
    "roster also appear — refer to them faithfully by the name the story uses "
    "(romanized), do NOT add a {char_id} tag to them (they have no id), and NEVER "
    "substitute a main character for a side character. If the story's focus is a "
    "side character (e.g. someone who is ill, a mentor, a rival), name that side "
    "character — do not misattribute their role to a roster member.\n"
    "Do not fabricate events or people that are not in the text."
)
if GLOSSARY_TEXT:
    SYSTEM += (
        "\n\n--- OFFICIAL GLOSSARY (MANDATORY TRANSLATIONS) ---\n"
        "When translating or referencing names and terms, you MUST use these "
        "English equivalents:\n" + GLOSSARY_TEXT
    )

_GLOBAL_RULES = (
    "Global formatting rules:\n"
    "- Write in clear, concise English.\n"
    "- Use exactly the required `## ` section headings for the tier, and emit every one.\n"
    "- If a bullet-list section has no entries, write exactly `- None`.\n"
    "- Tag each mention of a ROSTER character as Name{char_id=ID}; name side "
    "characters plainly (no tag).\n"
    "- Do not add extra sections, tables, or bold text."
)
_FORMATS = {
    "episode": (
        "Required Episode summary format:\n\n## Overview\n[2-4 sentences of concrete "
        "scene progression for THIS episode, inline-tagged.]\n\n## Key Events\n- "
        "[chronological beat, inline-tagged]\n\n## Character Developments\n- "
        "Name{char_id=ID}: [concrete emotional/relational/goal change]"
    ),
    "event": (
        "Required Event summary format:\n\n## Overview\n[1-2 short paragraphs "
        "synthesizing the whole event arc, inline-tagged. Do not copy the episode "
        "sections verbatim.]\n\n## Key Events\n- [major beat across the event, "
        "inline-tagged]\n\n## Character Developments\n- Name{char_id=ID}: [change "
        "across the event]"
    ),
    "unit": (
        "Required Unit summary format:\n\n## Overview\n[1-2 short paragraphs on this "
        "group's overall story so far, inline-tagged.]\n\n## Arc Progression\n- "
        "[event name]: [one line of what it advances, inline-tagged]\n\n## Character "
        "Arcs\n- Name{char_id=ID}: [their through-line across events]"
    ),
}
_INPUT_LABEL = {
    "episode": "CURRENT EPISODE TEXT (RAW JAPANESE STORY)",
    "event": "CURRENT EVENT INPUT (THIS EVENT'S EPISODE SUMMARIES)",
    "unit": "CURRENT UNIT INPUT (THIS UNIT'S EVENT SUMMARIES)",
}
_INPUT_INSTR = {
    "episode": "Summarize only this episode's source text; use previous context only to resolve references.",
    "event": "Synthesize across the child episode summaries into one event-level summary. Do not concatenate or copy child sections verbatim.",
    "unit": "Synthesize across the event summaries into a unit-level through-line. Do not concatenate or copy child sections verbatim.",
}
_TAG_RE = re.compile(r"\{char_id=(\d+)\}")
_FORMAT_SCHEMA = {
    "type": "object",
    "properties": {"summary": {"type": "string"}},
    "required": ["summary"],
}


def build_context(unit: str, focus_id=None, song=None) -> str:
    bits = [f"This is a {UNIT_NAMES.get(unit, unit)} event story."]
    if focus_id and int(focus_id) in ID_TO_EN:
        bits.append(f"Its focus character is {ID_TO_EN[int(focus_id)]}{{char_id={int(focus_id)}}}.")
    if song:
        bits.append(f'The featured song is "{song}".')
    return " ".join(bits)


def build_prompt(tier, unit, current_text, *, prev_summary=None, focus_id=None, song=None) -> str:
    parts = [
        "MAIN CAST (use these EXACT names + ids; never reassign anyone to another "
        "group). Side/guest characters not listed here may also appear — name them "
        "faithfully from the text, without an id tag:",
        ROSTER_TEXT,
        build_context(unit, focus_id, song),
    ]
    if prev_summary:
        parts += [
            "--- PREVIOUS CONTEXT (for continuity only) ---",
            prev_summary.strip(),
            "Use previous context only to resolve references, pronouns, chronology, and "
            "ongoing situations. Do not re-summarize prior events or copy prior sections.",
        ]
    parts += [
        f"--- {_INPUT_LABEL[tier]} ---",
        current_text,
        _INPUT_INSTR[tier],
        _GLOBAL_RULES,
        _FORMATS[tier],
        f"Write the {tier} summary now using exactly the required format, in English, with inline character tags.",
    ]
    return "\n\n".join(p for p in parts if p)


def clean_markdown(md: str) -> str:
    return _TAG_RE.sub(lambda m: "" if int(m.group(1)) not in VALID_IDS else m.group(0), md or "")


def parse_summary(out: str) -> dict:
    md = None
    m = re.search(r"\{.*\}", out or "", re.S)
    if m:
        try:
            obj = json.loads(m.group(0))
            if isinstance(obj, dict) and obj.get("summary"):
                md = obj["summary"]
        except Exception:
            md = None
    if md is None:
        md = (out or "").strip()
    md = clean_markdown(md).strip()
    ids = sorted({int(x) for x in _TAG_RE.findall(md)} & VALID_IDS)
    return {"summary": md, "characters": ids}


def is_broken(entry) -> bool:
    text = entry if isinstance(entry, str) else (entry or {}).get("summary", "")
    text = (text or "").strip()
    if not text:
        return True
    if re.search(r"Refine and Polish|Paragraph \d|Step \d|Draft:|Outline:", text, re.I):
        return True
    # a valid tiered summary should have at least the Overview heading
    return "## Overview" not in text and "Overview" not in text


def ollama_chat(host, model, prompt, system="") -> str:
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    body = json.dumps(
        {
            "model": model,
            "messages": messages,
            "stream": False,
            "format": _FORMAT_SCHEMA,
            "options": {"temperature": 0.2, "num_ctx": 32768},
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        f"{host}/api/chat", data=body, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=900) as resp:
        return (json.loads(resp.read()).get("message", {}).get("content") or "").strip()


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _save(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def read_arcs() -> dict[str, dict]:
    """arc_slug -> {unit, episodes: [(ep_key, text), ... in reading order]}."""
    tmp: dict[str, dict] = {}
    for md in STORY.glob("*/event/*/*.md"):
        arc = md.parent.name
        unit = md.parts[md.parts.index("story") + 1] if "story" in md.parts else "mixed"
        tmp.setdefault(arc, {"unit": unit, "files": []})["files"].append(md)
    out = {}
    for arc, d in tmp.items():
        def epnum(p: Path) -> int:
            m = re.match(r"(\d+)", p.stem)
            return int(m.group(1)) if m else 0
        files = sorted(d["files"], key=epnum)
        out[arc] = {
            "unit": d["unit"],
            "episodes": [(p.stem, p.read_text(encoding="utf-8")) for p in files],
        }
    return out


def _arc_order(arcs: dict) -> list[str]:
    """Chronological arc order (by release date if events_index is present)."""
    def key(a):
        return (_EVENT_META.get(a, {}).get("started_at", 0), a)
    return sorted(arcs, key=key)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tier", choices=["all", "episode", "event", "unit"], default="all")
    ap.add_argument("--only", choices=["broken", "missing", "all"], default="broken")
    ap.add_argument("--model", default="qwen2.5:7b")
    ap.add_argument("--arc", default="")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--host", default="http://localhost:11434")
    args = ap.parse_args()

    if not STORY.exists():
        print(f"ERROR: no story/ folder found ({STORY}).", file=sys.stderr)
        return 2
    try:
        ollama_chat(args.host, args.model, '{"summary":"ok"} reply with OK')
    except Exception as exc:
        print(f"ERROR: can't reach Ollama at {args.host} with '{args.model}': {exc}", file=sys.stderr)
        print("Install https://ollama.com/download then:  ollama pull " + args.model, file=sys.stderr)
        return 2

    arcs = read_arcs()
    ep_cache, ev_cache, un_cache = _load(EPISODE_CACHE), _load(EVENT_CACHE), _load(UNIT_CACHE)
    order = [args.arc] if args.arc else _arc_order(arcs)
    order = [a for a in order if a in arcs]
    if args.limit:
        order = order[: args.limit]

    def want(entry) -> bool:  # should we (re)generate this cached entry?
        if args.only == "all":
            return True
        if entry is None:
            return True
        return args.only == "broken" and is_broken(entry)

    run_ep = args.tier in ("all", "episode")
    run_ev = args.tier in ("all", "event")
    run_un = args.tier in ("all", "unit")

    # ---- Tier 1 + 2: episodes then event, per arc, chronological ----
    last_event_by_unit: dict[str, str] = {}
    for i, arc in enumerate(order, 1):
        unit = arcs[arc]["unit"]
        rec = _EVENT_META.get(arc, {})
        focus, song = rec.get("focus_character_id"), rec.get("song_title")
        ep_cache.setdefault(arc, {})

        # Tier 1: episodes with rolling within-event context
        prev = None
        for ep_key, text in arcs[arc]["episodes"]:
            existing = ep_cache[arc].get(ep_key)
            if run_ep and want(existing):
                t0 = time.time()
                prompt = build_prompt("episode", unit, text[:BODY_CHARS], prev_summary=prev, focus_id=focus, song=song)
                try:
                    entry = parse_summary(ollama_chat(args.host, args.model, prompt, SYSTEM))
                    ep_cache[arc][ep_key] = entry
                    _save(EPISODE_CACHE, ep_cache)
                    print(f"[{i}/{len(order)}] {arc}/{ep_key} episode: {len(entry['summary'])}c "
                          f"{len(entry['characters'])}tags {time.time()-t0:.0f}s", flush=True)
                    existing = entry
                except Exception as exc:
                    print(f"[{i}/{len(order)}] {arc}/{ep_key} FAILED: {exc}", flush=True)
            if existing:
                prev = existing["summary"] if isinstance(existing, dict) else existing

        # Tier 2: event from its episode summaries (rolling prev event in unit)
        if run_ev and want(ev_cache.get(arc)):
            child = "\n\n".join(
                f"### Episode {k}\n{(ep_cache[arc].get(k) or {}).get('summary','')}"
                for k, _ in arcs[arc]["episodes"] if ep_cache[arc].get(k)
            )
            if child.strip():
                t0 = time.time()
                prompt = build_prompt("event", unit, child, prev_summary=last_event_by_unit.get(unit), focus_id=focus, song=song)
                try:
                    entry = parse_summary(ollama_chat(args.host, args.model, prompt, SYSTEM))
                    ev_cache[arc] = entry
                    _save(EVENT_CACHE, ev_cache)
                    print(f"[{i}/{len(order)}] {arc} EVENT: {len(entry['summary'])}c "
                          f"{len(entry['characters'])}tags {time.time()-t0:.0f}s", flush=True)
                except Exception as exc:
                    print(f"[{i}/{len(order)}] {arc} EVENT FAILED: {exc}", flush=True)
        if ev_cache.get(arc):
            last_event_by_unit[unit] = ev_cache[arc]["summary"]

    # ---- Tier 3: unit from its event summaries ----
    if run_un and not args.arc:
        units: dict[str, list[str]] = {}
        for arc in _arc_order(arcs):
            units.setdefault(arcs[arc]["unit"], []).append(arc)
        for unit, unit_arcs in units.items():
            if not want(un_cache.get(unit)):
                continue
            child = "\n\n".join(
                f"### {_EVENT_META.get(a, {}).get('name', a)}\n{ev_cache[a]['summary']}"
                for a in unit_arcs if ev_cache.get(a)
            )
            if not child.strip():
                continue
            t0 = time.time()
            prompt = build_prompt("unit", unit, child[:60000])
            try:
                entry = parse_summary(ollama_chat(args.host, args.model, prompt, SYSTEM))
                un_cache[unit] = entry
                _save(UNIT_CACHE, un_cache)
                print(f"UNIT {unit}: {len(entry['summary'])}c {len(entry['characters'])}tags {time.time()-t0:.0f}s", flush=True)
            except Exception as exc:
                print(f"UNIT {unit} FAILED: {exc}", flush=True)

    print("done", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
