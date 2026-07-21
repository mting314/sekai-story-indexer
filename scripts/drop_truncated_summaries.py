"""One-time cleanup: drop truncated / thinking-leaked entries from the legacy
``event_summaries.json``.

The retired event-level summarizer left ~36 summaries cut mid-sentence (e.g.
"…Saku bluntly") or with leaked model thinking ("Ready to output.Nene…").
Serving those verbatim shows a cut-off summary. Rather than a fragile runtime
filter, drop the bad entries from the file once; events with no clean summary
then generate one on demand (dev/full) or fall back to scenes (keyless public).

Usage:  python scripts/drop_truncated_summaries.py [path=event_summaries.json]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_TERMINAL = '.!?"”』」）)…。！？'


def is_bad(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return True
    if "ready to output" in t.lower() or "```" in t:  # thinking / code-fence leak
        return True
    return t[-1] not in _TERMINAL  # doesn't end a sentence -> cut off mid-thought


def main(path: str = "event_summaries.json") -> int:
    p = Path(path)
    data = json.loads(p.read_text(encoding="utf-8"))

    def text(v: object) -> str:
        return v if isinstance(v, str) else (v or {}).get("summary", "")  # type: ignore[union-attr]

    bad = [arc for arc, v in data.items() if is_bad(text(v))]
    for arc in bad:
        del data[arc]
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"dropped {len(bad)} truncated/leaky summaries; {len(data)} remain")
    for arc in bad:
        print(f"  - {arc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(*sys.argv[1:]))
