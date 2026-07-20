#!/usr/bin/env python3
"""Local, no-API event summarizer using MLX (Apple Silicon).

Reads the story tree (story/<unit>/event/<arc>/*.md), and for each event asks a
local instruct model for a 1-2 paragraph English summary PLUS the roster of
characters that appear (Option 3: model-derived entity tagging). Writes a
resumable cache to event_summaries.json in the object form:

    {"<arc_slug>": {"summary": "...", "characters": [<gameCharacterId>, ...]}}

Backward compatible: readers accept either a bare string (old Google cache) or
this object. Costs nothing but local compute — no Google, no credits.

Run:  ~/.mlx-sekai/bin/python scripts/summarize_local.py [--only broken|missing|all] [--limit N] [--arc 0009]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "event_summaries.json"
MODEL_ID = "mlx-community/Qwen2.5-7B-Instruct-4bit"
BODY_CHARS = 24000  # keep the JA source under the model's context window

_META = json.loads((ROOT / "webapp" / "static" / "meta.json").read_text(encoding="utf-8"))
NAMES = [c["en"] for c in _META["characters"].values()]
NAME_TO_ID = {c["en"]: int(cid) for cid, c in _META["characters"].items()}

PROMPT = """You are an expert at summarizing Project Sekai (Hatsune Miku: Colorful Stage!) event stories.

Summarize the event story below in 1-2 short English paragraphs. Focus on plot progression and character development; refer to characters by their English names. Do not invent anything that is not in the text.

Write clean prose only. Do NOT include headings, numbered steps, bullet points, outlines, draft notes, or labels such as "Paragraph 1" or "Refine". Do NOT describe your process.

After the summary, list which of these characters actually appear (use these exact names): {names}

Return ONLY a JSON object and nothing else:
{{"summary": "<the finished summary>", "characters": ["<name>", "<name>"]}}

Event: {name}
Story:
{body}"""


def read_arcs() -> dict[str, str]:
    """arc_slug -> concatenated scene text, across all units (event stories)."""
    arcs: dict[str, list[Path]] = {}
    for md in (ROOT / "story").glob("*/event/*/*.md"):
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
    """A cached entry that looks truncated or scaffolded (needs regen)."""
    text = entry if isinstance(entry, str) else (entry or {}).get("summary", "")
    text = (text or "").strip()
    if not text:
        return True
    if re.search(r"Refine and Polish|Paragraph \d|Step \d|Draft:|Outline:", text, re.I):
        return True
    return text[-1] not in ".!?」）)…\""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=["broken", "missing", "all"], default="broken")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--arc", default="")
    args = ap.parse_args()

    cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
    arcs = read_arcs()

    if args.arc:
        todo = [args.arc]
    elif args.only == "missing":
        todo = [a for a in arcs if a not in cache]
    elif args.only == "all":
        todo = list(arcs)
    else:  # broken (default): regen truncated/scaffolded + fill missing
        todo = [a for a in arcs if a not in cache or is_broken(cache.get(a))]
    todo = [a for a in todo if a in arcs]
    todo.sort()
    if args.limit:
        todo = todo[: args.limit]

    print(f"loading {MODEL_ID} ...", flush=True)
    from mlx_lm import generate, load  # pyrefly: ignore[missing-import]
    from mlx_lm.sample_utils import make_sampler  # pyrefly: ignore[missing-import]

    model, tok = load(MODEL_ID)
    sampler = make_sampler(temp=0.3)
    print(f"to generate: {len(todo)} events", flush=True)

    for i, arc in enumerate(todo, 1):
        body = arcs[arc]
        prompt = PROMPT.format(names=", ".join(NAMES), name=arc, body=body[:BODY_CHARS])
        chat = tok.apply_chat_template(
            [{"role": "user", "content": prompt}], add_generation_prompt=True, tokenize=False
        )
        t0 = time.time()
        out = generate(model, tok, prompt=chat, max_tokens=800, sampler=sampler, verbose=False)
        entry = parse(out)
        cache[arc] = entry
        CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
        dt = time.time() - t0
        print(
            f"[{i}/{len(todo)}] {arc}: {len(entry['summary'])} chars, "
            f"{len(entry['characters'])} chars-tagged, {dt:.0f}s",
            flush=True,
        )
    print("done", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
