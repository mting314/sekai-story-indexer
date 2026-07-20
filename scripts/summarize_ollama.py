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

ROOT = Path(__file__).resolve().parent
STORY = ROOT / "story"
META = ROOT / "meta.json"
CACHE = ROOT / "event_summaries.json"
BODY_CHARS = 40000  # Ollama models handle long context; keep a sane cap

_meta = json.loads(META.read_text(encoding="utf-8"))
NAMES = [c["en"] for c in _meta["characters"].values()]
NAME_TO_ID = {c["en"]: int(cid) for cid, c in _meta["characters"].items()}

PROMPT = """You are an expert at summarizing Project Sekai (Hatsune Miku: Colorful Stage!) event stories.

Summarize the event story below in 1-2 short English paragraphs. Focus on plot progression and character development; refer to characters by their English names. Do not invent anything that is not in the text.

Write clean prose only. Do NOT include headings, numbered steps, bullet points, outlines, draft notes, or labels such as "Paragraph 1" or "Refine". Do NOT describe your process.

After the summary, list which of these characters actually appear (use these exact names): {names}

Return ONLY a JSON object and nothing else:
{{"summary": "<the finished summary>", "characters": ["<name>", "<name>"]}}

Story:
{body}"""


def read_arcs() -> dict[str, str]:
    arcs: dict[str, list[Path]] = {}
    for md in STORY.glob("*/event/*/*.md"):
        arcs.setdefault(md.parent.name, []).append(md)
    return {
        arc: "\n\n".join(p.read_text(encoding="utf-8") for p in sorted(files))
        for arc, files in arcs.items()
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
    if not text:
        return True
    if re.search(r"Refine and Polish|Paragraph \d|Step \d|Draft:|Outline:", text, re.I):
        return True
    return text.rstrip()[-1] not in ".!?」）)…\""


def ollama_chat(host: str, model: str, prompt: str) -> str:
    body = json.dumps(
        {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "options": {"temperature": 0.3, "num_ctx": 16384},
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
        prompt = PROMPT.format(names=", ".join(NAMES), body=arcs[arc][:BODY_CHARS])
        t0 = time.time()
        try:
            out = ollama_chat(args.host, args.model, prompt)
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
