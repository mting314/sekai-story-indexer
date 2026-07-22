"""Auto-derive a JP->EN side-character glossary from the aligned transcript pairs.

Each ``foo.md`` (JP) has a 1:1-aligned ``foo.md.en`` (official EN) sidecar, so a
line ``真堂: ...`` in JP maps to ``Shindo: ...`` in EN at the same index. This
harvests those speaker-name pairs across the whole corpus, picks the majority EN
rendering per JP name, drops main-cast names already in ``glossary.json``, and
prints the new ``side_characters`` entries.

Usage:
  uv run python scripts/derive_side_glossary.py            # dry-run (preview)
  uv run python scripts/derive_side_glossary.py --write     # merge into glossary.json
"""

import glob
import json
import os
import re
import sys
from collections import defaultdict

SPEAKER = re.compile(r"^([^:：\n]{1,20})[:：]\s*\S")
# Non-name speaker labels to skip (unknown / VS narration markers / generic roles).
SKIP = {"???", "？？？", "…", "", "Voice", "Crew Member", "Staff", "Announcement"}


def name_pairs() -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for jp_path in glob.glob("story/**/*.md", recursive=True):
        en_path = jp_path + ".en"
        if not os.path.exists(en_path):
            continue
        jp_lines = open(jp_path, encoding="utf-8").read().splitlines()
        en_lines = open(en_path, encoding="utf-8").read().splitlines()
        if len(jp_lines) != len(en_lines):
            continue  # only trust strictly aligned files
        for jl, el in zip(jp_lines, en_lines):
            mj, me = SPEAKER.match(jl), SPEAKER.match(el)
            if not (mj and me):
                continue
            jp, en = mj.group(1).strip(), me.group(1).strip()
            if jp in SKIP or en in SKIP or not re.search(r"[一-龠ぁ-んァ-ヴ]", jp):
                continue  # JP speaker must contain kana/kanji (skip already-latin like MEIKO)
            counts[jp][en] += 1
    return counts


# Only genuine personal/proper names belong in the glossary — role labels, combo
# speakers, and relational/voice variants are noise (and would pollute the local
# retrieval bridge with common-word tokens).
_JP_NOISE = ("・", "、", "＆", "達", "たち", "ら", "の声", "のメッセージ", "の歌",
             "の父", "の母", "の姉", "の妹", "クラスメイト", "幼い", "中学生", "先輩",
             "後輩", "友達", "友人", "ファン", "生徒", "メンバー", "スタッフ", "さん達")
_EN_NOISE_SUB = (" & ", "'s Voice", "'s Message", "'s Father", "'s Mother",
                 "'s Sister", "'s Classmate", "'s Fan", " Singing", " Member",
                 " Members", "Little ", "Young ", "Junior High ", "Older ")
# Whole-word generic English roles (case-insensitive) that are never proper names.
_EN_ROLE = {
    "student", "students", "teacher", "woman", "women", "man", "boy", "boys",
    "girl", "child", "children", "fan", "fans", "guest", "guests", "audience",
    "everyone", "narrator", "staff", "crowd", "customer", "regular", "regulars",
    "passerby", "classmate", "classmates", "producer", "director", "manager",
    "principal", "judge", "nurse", "doctor", "owner", "leader", "announcer",
    "commentator", "referee", "clerk", "shopper", "tourist", "tourists",
    "local", "locals", "musician", "musicians", "plushie", "plushies", "robot",
    "zombie", "zombies", "spectator", "monster", "demon", "cast", "member",
    "message", "email", "comment", "narration", "voice", "employee",
}


def _is_generic(jp: str, en: str) -> bool:
    if any(s in jp for s in _JP_NOISE):
        return True
    if any(s in en for s in _EN_NOISE_SUB):
        return True
    words = [w.lower() for w in re.findall(r"[A-Za-z]+", en)]
    # trailing enumerations ("... A"/"... B") or all-generic-role phrases
    if words and words[-1] in {"a", "b", "c", "d", "e", "f"}:
        return True
    if words and all(w in _EN_ROLE for w in words):
        return True
    return False


def main() -> None:
    g = json.load(open("glossary.json", encoding="utf-8"))
    known_jp = set()
    for sec in ("characters", "side_characters"):
        known_jp |= set(g.get(sec) or {})
    known_en = {en for sec in g.values() if isinstance(sec, dict) for en in sec.values()}
    # also the individual tokens of MAIN-cast names, so given-name-only speaker
    # forms (愛莉 -> "Airi", 彰人 -> "Akito") are treated as known, not "new".
    for en in (g.get("characters") or {}).values():
        for t in re.findall(r"[A-Za-z']+", en):
            known_en.add(t)

    derived: dict[str, str] = {}
    for jp, ens in name_pairs().items():
        if jp in known_jp:
            continue
        en, n = max(ens.items(), key=lambda kv: kv[1])
        # require a confident, consistent, translated (non-identical) rendering
        if n < 3 or en == jp or en in known_en:
            continue
        if _is_generic(jp, en):
            continue
        derived[jp] = en

    for jp, en in sorted(derived.items(), key=lambda kv: kv[1]):
        print(f'    "{jp}": "{en}",')
    print(f"\n# {len(derived)} new side-character names derived from aligned EN transcripts")

    if "--write" in sys.argv and derived:
        g.setdefault("side_characters", {}).update(derived)
        json.dump(g, open("glossary.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print(f"merged into glossary.json (side_characters now {len(g['side_characters'])})")


if __name__ == "__main__":
    main()
