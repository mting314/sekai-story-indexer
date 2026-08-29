"""Shared lexical scoring for the local and derived backends.

Both backends rank the same corpus with the same intent, but they used to carry
separate copies of the scoring maths — and they drifted: the local engine gained
concept scoring while the public (derived) deploy silently kept ranking on raw
term frequency. Everything here is pure and dependency-free (tokens, counts, IDF)
so `local.py` and `derived_index.py` can share one implementation.

The core idea is that a query is a set of *concepts*, not a bag of tokens. A
character name expands to up to eight lexical surfaces (``honami``, ``穂波``,
``穂``, ``波``, ``望月`` …); summing those and multiplying by raw term frequency
turns ranking into a name-frequency sort, so a long scene that never mentions the
topic beats the one that answers the question. Scoring each concept by its best
surface, with sublinear term frequency and a topic-coverage factor, removes that.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Sequence

WORD_RE = re.compile(r"[a-z0-9]+")
CJK_RE = re.compile(r"[぀-ヿ㐀-鿿]")

# Unit references in questions (substring, lowercased) -> unit slug.
UNIT_KEYWORDS = {
    "leo/need": "leo_need", "leoneed": "leo_need", "leo need": "leo_need",
    "more more jump": "more_more_jump", "moremorejump": "more_more_jump",
    "mmj": "more_more_jump", "momojan": "more_more_jump",
    "vivid bad squad": "vivid_bad_squad", "vbs": "vivid_bad_squad", "vivid": "vivid_bad_squad",
    "wonderlands": "wonderlands_showtime", "wxs": "wonderlands_showtime",
    "wonder show": "wonderlands_showtime",
    "nightcord": "nightcord", "25-ji": "nightcord", "niigo": "nightcord", "n25": "nightcord",
    "virtual singer": "virtual_singer", "vocaloid": "virtual_singer",
}

# Unit references ("mmj", "vivid", "wonderlands") scope a question; they are not
# what it is *about*. Left in the topic set they behave as a topic the real answer
# can "miss" purely by spelling the group out in full — haru4 says "MORE MORE
# JUMP!", never "MMJ", and would lose coverage to a scene that used the acronym.
UNIT_TOKENS = frozenset(tok for kw in UNIT_KEYWORDS for tok in WORD_RE.findall(kw.lower()))

# Question-side function words. Dropped when working out what a question is
# *about*, so "when does Honami mention her brother" reduces to {brother}. Index-
# side tokens are untouched — this only shapes concept extraction.
QUESTION_STOPWORDS = frozenset("""
a an the and or but if then than that this these those there here as by
when where what which who whom whose how why whether
do does did done is are was were be been being am has have had
to of in on at for with from about into over under after before during
i me my mine you your yours he him his she her hers it its they them their theirs
we us our ours one someone anyone everyone everybody everything something anything
say says said tell tells told talk talks talked speak speaks spoke
mention mentions mentioned mentioning bring brings brought
first last ever any some all much many more most not never
going go goes went come comes came get gets got
become becomes became becoming turn turns turned
""".split())

# Keep only topics within this factor of the sharpest one, so a question's real
# subject isn't diluted by incidental words that survived the stopword list. This
# is relative on purpose: an absolute document-frequency cut behaves differently
# on a 12-node fixture than on a 7k-node corpus, and would drop real topics from
# the former.
TOPIC_IDF_FLOOR = 0.6


def tokenize(text: str) -> list[str]:
    """ASCII words + CJK unigrams AND bigrams — a language-agnostic lexical key set
    that needs no tokenizer dependency (works for JP and EN). Unigrams let a short,
    standalone kanji word (弟, 兄) be found even when it fuses with a following
    particle into a bigram (弟も); bigrams keep multi-char phrase precision."""
    text = text.lower()
    tokens = WORD_RE.findall(text)
    cjk = CJK_RE.findall(text)
    tokens += cjk  # unigrams (single CJK chars)
    if len(cjk) > 1:
        tokens += ["".join(pair) for pair in zip(cjk, cjk[1:])]  # adjacent bigrams
    return tokens


def named_characters(
    question: str, characters: Sequence[tuple[str, str]]
) -> list[tuple[str, str]]:
    """All characters explicitly named in the question (JP fragment or EN token)."""
    q_tokens = set(tokenize(question))
    jp_runs = CJK_RE.findall(question)
    jp_bigrams = {"".join(pair) for pair in zip(jp_runs, jp_runs[1:])}
    out = []
    for jp, en in characters:
        if (
            jp in question
            or any(bg in jp for bg in jp_bigrams)
            or any(len(t) >= 3 and t in q_tokens for t in tokenize(en))
        ):
            out.append((jp, en))
    return out


def name_surfaces(
    question: str, characters: Sequence[tuple[str, str]], idf: dict[str, float]
) -> list[str]:
    """Every lexical surface of the characters the question names, restricted to
    tokens the index knows. These are one *concept*, not N independent terms."""
    toks: set[str] = set()
    for jp, en in named_characters(question, characters):
        toks.update(tokenize(f"{jp} {en}"))
    return sorted(t for t in toks if t in idf)


def topic_terms(
    question: str, characters: Sequence[tuple[str, str]], idf: dict[str, float]
) -> list[str]:
    """Question terms that carry topic, sharpest first: not function words, not
    unit references, not the character names, and present in the corpus (a term the
    index has never seen cannot discriminate). No relevance floor — see
    ``scoring_groups``, which applies one."""
    name_tokens = {
        t for jp, en in named_characters(question, characters) for t in tokenize(f"{jp} {en}")
    }
    terms = [
        t
        for t in dict.fromkeys(tokenize(question))  # dedupe, keep order
        if t.isascii()
        and len(t) > 2
        and t not in QUESTION_STOPWORDS
        and t not in UNIT_TOKENS
        and t not in name_tokens
    ]
    return sorted((t for t in terms if t in idf), key=lambda t: -idf[t])


def scoring_groups(
    question: str,
    characters: Sequence[tuple[str, str]],
    idf: dict[str, float],
    *,
    aux_query: str = "",
    floor: bool = False,
) -> tuple[list[str], list[list[str]]]:
    """``(name surfaces, topic concepts)`` for ``concept_score``.

    ``floor=True`` keeps only topics within ``TOPIC_IDF_FLOOR`` of the sharpest —
    turn retrieval wants that tight set because it matches single utterances, while
    scene ranking keeps every topic and lets coverage weigh them.
    """
    names = name_surfaces(question, characters, idf)
    terms = topic_terms(question, characters, idf)
    if floor and terms:
        ceiling = idf[terms[0]]
        terms = [terms[0]] + [t for t in terms[1:] if idf[t] >= ceiling * TOPIC_IDF_FLOOR]
    topics: list[list[str]] = [[t] for t in terms]
    # A single-topic question can also match through the translated query, which
    # reaches scenes whose EN sidecar is missing.
    if aux_query and len(topics) == 1:
        topics[0] += [t for t in dict.fromkeys(tokenize(aux_query)) if not t.isascii()]
    return names, topics


def concept_score(
    tf: dict[str, int] | Iterable,
    idf: dict[str, float],
    name_group: Sequence[str],
    topics: Sequence[Sequence[str]],
) -> float:
    """Concept-wise relevance, scaled by how much of the question's topic the scene
    actually covers.

    Each concept contributes its best surface with sublinear term frequency —
    max-within-concept stops one repeated surface (a name) standing in for topical
    relevance, and sublinear tf stops sheer scene length doing the same. The
    coverage factor then scales by the share of topics present, so a scene matching
    the name and nothing else scores **zero** instead of winning on name mass.
    Coverage is deliberately proportional rather than all-or-nothing: questions
    paraphrase, and demanding every term would drop the answer whenever the wording
    differs from the script.
    """

    def best_of(surfaces: Sequence[str]) -> float:
        out = 0.0
        for term in surfaces:
            count = tf.get(term, 0)
            if count:
                out = max(out, (1 + math.log(count)) * idf[term])
        return out

    total = best_of(name_group)
    if not topics:
        return total
    matched = 0
    for concept in topics:
        hit = best_of(concept)
        if hit:
            matched += 1
            total += hit
    return total * (matched / len(topics))
