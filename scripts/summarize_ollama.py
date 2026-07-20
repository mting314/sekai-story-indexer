#!/usr/bin/env python3
"""Local event summarizer via Ollama — runs anywhere Ollama runs (Windows/Mac/Linux).

No pip dependencies (Python standard library only). Reads the story tree and
meta.json sitting next to this script, asks a local Ollama model for a 1-2
paragraph English summary PLUS the roster of characters that appear, and writes a
resumable cache to event_summaries.json:

    {"<arc_slug>": {"summary": "...", "characters": [<gameCharacterId>, ...]}}

Old string entries (from the earlier Google run) are read fine by the web app;
this upgrades broken/missing ones and adds character tagging.

Setup (Windows):
  1. Install Ollama:  https://ollama.com/download   (auto-uses your GPU)
  2. Pull a model:    ollama pull qwen2.5:7b     (or qwen2.5:3b if CPU-only)
  3. Run:             python summarize_ollama.py --only broken

Then copy event_summaries.json back next to the web app on the Mac.

Options:
  --only {broken,missing,all}   what to (re)generate (default broken = truncated + missing)
  --model NAME                  Ollama model tag (default qwen2.5:7b)
  --limit N                     cap number of events (0 = no cap)
  --arc SLUG                    just one event, e.g. --arc 0009
  --host URL                    Ollama base URL (default http://localhost:11434)
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
    """Directory that holds story/ — works for BOTH the zip bundle (everything
    next to this script) and a git clone (story/ at the repo root, one level up)."""
    for d in (_HERE, _HERE.parent, Path.cwd()):
        if (d / "story").exists():
            return d
    return _HERE


BASE = _base()
STORY = BASE / "story"
# meta.json lives next to the script in the zip, or under webapp/static in the repo
META = next(
    (p for p in (BASE / "meta.json", BASE / "webapp" / "static" / "meta.json", _HERE / "meta.json") if p.exists()),
    BASE / "meta.json",
)
CACHE = BASE / "event_summaries.json"
BODY_CHARS = 24000  # keep the JA source within the model's context window

_meta = json.loads(META.read_text(encoding="utf-8"))
NAMES = [c["en"] for c in _meta["characters"].values()]
NAME_TO_ID = {c["en"]: int(cid) for cid, c in _meta["characters"].items()}
ID_TO_EN = {int(cid): c["en"] for cid, c in _meta["characters"].items()}
UNIT_NAMES = {slug: u["name"] for slug, u in _meta.get("units", {}).items()}

# Ground-truth rosters so the model NEVER guesses group membership. Order matters
# only for readability. Virtual Singers appear across all groups.
_UNIT_ORDER = [
    "leo_need", "more_more_jump", "vivid_bad_squad",
    "wonderlands_showtime", "nightcord", "virtual_singer",
]
_roster: dict[str, list[str]] = {}
for _cid, _c in _meta["characters"].items():
    _roster.setdefault(_c.get("unit", "other"), []).append(_c["en"])
ROSTER_TEXT = "\n".join(
    f"- {UNIT_NAMES.get(slug, slug)}: {', '.join(_roster[slug])}"
    for slug in _UNIT_ORDER
    if slug in _roster
)

# Optional glossary (JP -> EN mandatory translations), matching the Google path.
# Bundled as glossary.json next to this script, or found in the repo root.
_glossary = {}
for _gp in (BASE / "glossary.json", _HERE / "glossary.json"):
    if _gp.exists():
        try:
            _glossary = json.loads(_gp.read_text(encoding="utf-8"))
        except Exception:
            _glossary = {}
        break
_gloss_lines: list[str] = []
for _cat, _terms in _glossary.items():
    if isinstance(_terms, dict) and _terms:
        _gloss_lines.append(f"{_cat.replace('_', ' ').upper()}:")
        _gloss_lines += [f" - {jp} -> {en}" for jp, en in _terms.items()]
GLOSSARY_TEXT = "\n".join(_gloss_lines)

# Optional per-event enrichment (focus character, song) if events_index.json is
# bundled alongside. Purely additive context; absent -> just the unit.
_EVENT_META: dict[str, dict] = {}
for _p in (BASE / "events_index.json", _HERE / "events_index.json"):
    if _p.exists():
        try:
            _EVENT_META = {r.get("arc_slug"): r for r in json.loads(_p.read_text(encoding="utf-8"))}
        except Exception:
            _EVENT_META = {}
        break

# Kept in sync with src/sekai_story_indexer/source/summary_prompt.py (canonical).
SYSTEM = (
    "You are an expert archivist and translator indexing Japanese Project Sekai "
    "(Hatsune Miku: Colorful Stage!) event stories. You write all summaries in "
    "clear, concise ENGLISH — never Japanese. You use only the character names and "
    "group memberships given to you; you never invent characters or move a "
    "character to a different group."
)
if GLOSSARY_TEXT:
    SYSTEM += (
        "\n\n--- OFFICIAL GLOSSARY (MANDATORY TRANSLATIONS) ---\n"
        "When translating or referencing names and terms, you MUST use these "
        "English equivalents:\n" + GLOSSARY_TEXT
    )

PROMPT = """Character groups (ground truth — use these EXACT English names and do NOT reassign anyone to a different group):
{roster}

{context}

Summarize the Japanese event story below in 1-2 short paragraphs of clear ENGLISH prose. Focus on plot progression and character development. Do not invent anything that is not in the text. Write the summary in English only.

Write clean prose only: no headings, numbered steps, bullet points, outlines, draft notes, or labels like "Paragraph 1" or "Refine". Do not describe your process.

After the summary, list which of the characters above actually appear in this story (exact English names).

Return ONLY a JSON object and nothing else:
{{"summary": "<the English summary>", "characters": ["<name>", "<name>"]}}

Story (Japanese):
{body}"""


def _context_for(arc: str, unit: str) -> str:
    """One-line ground-truth context for this event: unit, focus char, song."""
    bits = [f"This is a {UNIT_NAMES.get(unit, unit)} event story."]
    rec = _EVENT_META.get(arc, {})
    fid = rec.get("focus_character_id")
    if fid and int(fid) in ID_TO_EN:
        bits.append(f"Its focus character is {ID_TO_EN[int(fid)]}.")
    song = rec.get("song_title")
    if song:
        bits.append(f'The featured song is "{song}".')
    return " ".join(bits)


def read_arcs() -> dict[str, tuple[str, str]]:
    """arc_slug -> (unit_slug, concatenated scene text)."""
    files: dict[str, list[Path]] = {}
    unit: dict[str, str] = {}
    for md in STORY.glob("*/event/*/*.md"):
        arc = md.parent.name
        files.setdefault(arc, []).append(md)
        unit[arc] = md.parts[md.parts.index("story") + 1] if "story" in md.parts else "mixed"
    return {
        arc: (unit.get(arc, "mixed"), "\n\n".join(p.read_text(encoding="utf-8") for p in sorted(fs)))
        for arc, fs in files.items()
    }


def parse(out: str) -> dict:
    m = re.search(r"\{.*\}", out, re.S)
    if m:
        try:
            obj = json.loads(m.group(0))
            summ = (obj.get("summary") or "").strip()
            chars = sorted({NAME_TO_ID[n] for n in obj.get("characters", []) if n in NAME_TO_ID})
            if summ:
                return {"summary": summ, "characters": chars}
        except Exception:
            pass
    return {"summary": out.strip(), "characters": []}


def is_broken(entry) -> bool:
    text = entry if isinstance(entry, str) else (entry or {}).get("summary", "")
    text = (text or "").strip()
    if not text:
        return True
    if re.search(r"Refine and Polish|Paragraph \d|Step \d|Draft:|Outline:", text, re.I):
        return True
    return text[-1] not in ".!?」）)…\""


def ollama_chat(host: str, model: str, prompt: str, system: str = "") -> str:
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    body = json.dumps(
        {
            "model": model,
            "messages": messages,
            "stream": False,
            # low temp for consistency; big context so full JA events fit
            "options": {"temperature": 0.2, "num_ctx": 32768},
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        f"{host}/api/chat", data=body, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=600) as resp:
        data = json.loads(resp.read())
    return (data.get("message", {}).get("content") or "").strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=["broken", "missing", "all"], default="broken")
    ap.add_argument("--model", default="qwen2.5:7b")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--arc", default="")
    ap.add_argument("--host", default="http://localhost:11434")
    args = ap.parse_args()

    if not STORY.exists():
        print(f"ERROR: no story/ folder next to this script ({STORY}).", file=sys.stderr)
        return 2

    cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
    arcs = read_arcs()

    if args.arc:
        todo = [args.arc]
    elif args.only == "missing":
        todo = [a for a in arcs if a not in cache]
    elif args.only == "all":
        todo = list(arcs)
    else:
        todo = [a for a in arcs if a not in cache or is_broken(cache.get(a))]
    todo = sorted(a for a in todo if a in arcs)
    if args.limit:
        todo = todo[: args.limit]

    # fail fast if Ollama isn't reachable
    try:
        ollama_chat(args.host, args.model, "Reply with OK.")
    except Exception as exc:
        print(f"ERROR: can't reach Ollama at {args.host} with model '{args.model}': {exc}", file=sys.stderr)
        print("Install from https://ollama.com/download and run:  ollama pull " + args.model, file=sys.stderr)
        return 2

    print(f"model={args.model}  events to generate: {len(todo)}", flush=True)
    for i, arc in enumerate(todo, 1):
        unit, body = arcs[arc]
        prompt = PROMPT.format(
            roster=ROSTER_TEXT, context=_context_for(arc, unit), body=body[:BODY_CHARS]
        )
        t0 = time.time()
        try:
            out = ollama_chat(args.host, args.model, prompt, system=SYSTEM)
        except Exception as exc:
            print(f"[{i}/{len(todo)}] {arc}: FAILED ({exc}) — skipping", flush=True)
            continue
        entry = parse(out)
        cache[arc] = entry
        CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
        print(
            f"[{i}/{len(todo)}] {arc}: {len(entry['summary'])} chars, "
            f"{len(entry['characters'])} chars-tagged, {time.time() - t0:.0f}s",
            flush=True,
        )
    print("done", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
