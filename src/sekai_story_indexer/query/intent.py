"""Lightweight hybrid intent routing for query shapes and quick actions.

Combines high-precision regex matching with character/word N-gram TF-IDF exemplar
cosine similarity for keyless semantic intent classification across English,
Japanese, and Chinese paraphrases (e.g., "how did everything turn out?",
"what was the resolution?", "結末はどうなった？", "最後結果如何？").
"""

from __future__ import annotations

import math
import re
from typing import Literal

Intent = Literal["summarize", "conclusion", "focus_character", "resonance", "count", "general"]

# High-precision keyword regexes for instant fast-path matching
_COUNT_RE = re.compile(
    r"\b(how many|number of|count (of|the)?)\b.*\b"
    r"(lines?|times?|dialogue|turns?|appearances?|speak|say|said)\b",
    re.IGNORECASE,
)

_SUMMARIZE_RE = re.compile(
    r"\b(summar(?:y|ize|ise)|recap|overview|tl;?dr|synops(is|e)?|what happen(s|ed)? (in|during)|tell me about)\b|"
    r"あらすじ|要約|概要|梗概|簡介|劇情總結",
    re.IGNORECASE,
)

_FOCUS_CHAR_RE = re.compile(
    r"\bfocus character\b|who(?:'s| is| are) the (?:main|focus|central|lead)\b|"
    r"whose (?:event|story|arc) is this|誰が主役|バナーキャラ|焦點角色|誰的主場",
    re.IGNORECASE,
)

_CONCLUSION_RE = re.compile(
    r"\bconclusion\b|\bfinale\b|what happens (?:at|in|by) the end|"
    r"how (?:does|did) (?:it|this|the (?:event|story|arc)) (?:end|conclude|wrap up|resolve)|"
    r"\bend(?:ing)? of (?:this|the) (?:event|story|arc)\b|結末|オチ|最終結果|結局",
    re.IGNORECASE,
)

_RESONANCE_RE = re.compile(
    r"\bresonan(?:ce|t|tes?)\b|"
    r"\b(?:theme\s+)?song(?:'s)?\b[^.?!]*\b(?:mean|meaning|message|significan|"
    r"relate|relation|connect|reflect|represent|resonate|tie|about the (?:story|event))|"
    r"\b(?:mean|meaning|message|significance|point) of the (?:theme\s+)?song\b|"
    r"\blyrics?\b[^.?!]*\b(?:mean|meaning|message|relate|connect|reflect|"
    r"about the (?:story|event)|and the (?:story|event))|"
    r"\b(?:theme\s+)?song\s+and\s+(?:the\s+)?story\b|\bstory\s+and\s+(?:the\s+)?song\b|"
    r"書き下ろし|楽曲.*意味|歌詞.*関係|主題曲.*關係",
    re.IGNORECASE,
)

# Exemplars for semantic prototype cosine matching
_EXEMPLARS: dict[Intent, list[str]] = {
    "conclusion": [
        "how did everything turn out",
        "what is the outcome",
        "where do they end up",
        "how does it conclude",
        "what was the resolution",
        "how does the story resolve",
        "ending recap",
        "what is the payoff",
        "does it have a happy ending",
        "climax and resolution",
        "how was the conflict resolved",
        "what happens at the end",
        "how does it end",
        "finale of the event",
        "what is the final result",
        "how did things end up",
        "結末はどうなった",
        "最後はどうなる",
        "どんな結末",
        "ストーリーの終わり",
        "オチはどうなった",
        "最終的にどうなった",
        "最後怎麼了",
        "結局是什麼",
        "最後結果如何",
        "故事怎麼結尾",
        "最終結局",
    ],
    "summarize": [
        "give me the main takeaway",
        "explain the plot",
        "walk me through this event",
        "gist of the story",
        "overview of the arc",
        "summarize what happened",
        "give me a summary",
        "tell me the story plot",
        "event synopsis",
        "brief recap of the event",
        "what is this story about",
        "recap the main points",
        "あらすじを教えて",
        "ストーリーの要約",
        "概要まとめ",
        "全体の内容",
        "イベントのまとめ",
        "故事梗概",
        "劇情總結",
        "這章講了什麼",
        "大意摘要",
        "活動劇情簡介",
    ],
    "focus_character": [
        "who is the central figure",
        "who gets the spotlight",
        "whose story is this",
        "who stars in this event",
        "focus character of the event",
        "who is the main character",
        "who is the banner character",
        "who leads this event",
        "which character is this about",
        "who is featured in this arc",
        "バナーキャラは誰",
        "誰が主役",
        "フォーカスキャラ",
        "メインキャラは誰",
        "誰のイベント",
        "主角是誰",
        "這是誰的主場",
        "焦點角色是誰",
        "誰是中心人物",
    ],
    "resonance": [
        "how does the song relate to the story",
        "what does the theme song mean",
        "lyrics connection to event",
        "song and story resonance",
        "meaning of the event song",
        "how the lyrics reflect the story",
        "connection between song and plot",
        "what is the song message",
        "書き下ろし曲の意味",
        "楽曲とストーリーの関係",
        "曲の歌詞の解釈",
        "イベント曲の意味",
        "主題曲和故事的關係",
        "歌詞有什麼涵義",
        "活動歌曲的意思",
    ],
}


def _vectorize(text: str) -> dict[str, float]:
    """Converts a text string into an L2-normalized feature vector of word tokens
    and character 1-3 grams."""
    counts: dict[str, float] = {}

    # Extract word tokens (lowercased ASCII / alpha-numeric)
    words = re.findall(r"\b[a-z0-9]+\b", text.lower())
    for w in words:
        counts[f"w:{w}"] = counts.get(f"w:{w}", 0.0) + 1.5

    # Extract character n-grams (1, 2, 3) for non-space characters
    clean_chars = [c for c in text.lower() if not c.isspace()]
    n = len(clean_chars)
    for i in range(n):
        # 1-gram
        g1 = f"1:{clean_chars[i]}"
        counts[g1] = counts.get(g1, 0.0) + 0.5
        # 2-gram
        if i + 1 < n:
            g2 = f"2:{clean_chars[i]}{clean_chars[i+1]}"
            counts[g2] = counts.get(g2, 0.0) + 1.0
        # 3-gram
        if i + 2 < n:
            g3 = f"3:{clean_chars[i]}{clean_chars[i+1]}{clean_chars[i+2]}"
            counts[g3] = counts.get(g3, 0.0) + 1.5

    # L2 normalize
    norm = math.sqrt(sum(v * v for v in counts.values()))
    if norm > 0:
        for k in counts:
            counts[k] /= norm
    return counts


def _dot_product(vec1: dict[str, float], vec2: dict[str, float]) -> float:
    """Computes cosine similarity between two L2-normalized feature vectors."""
    if len(vec1) > len(vec2):
        vec1, vec2 = vec2, vec1
    return sum(val * vec2[k] for k, val in vec1.items() if k in vec2)


# Precompute normalized feature vectors for exemplar sets
_EXEMPLAR_VECS: dict[Intent, list[dict[str, float]]] = {
    intent: [_vectorize(e) for e in exemplars] for intent, exemplars in _EXEMPLARS.items()
}


def classify_with_score(question: str) -> tuple[Intent, float]:
    """Classifies a user question into an Intent along with a confidence score.

    Tiers:
    1. Fast-path keyword regexes -> score 1.0
    2. Character/Word N-Gram TF-IDF prototype similarity -> max similarity score
    3. Fallback -> ("general", 0.0)
    """
    if not question or not question.strip():
        return "general", 0.0

    # Tier 1: Fast-path regex checks
    if _COUNT_RE.search(question):
        return "count", 1.0
    if _SUMMARIZE_RE.search(question):
        return "summarize", 1.0
    if _CONCLUSION_RE.search(question):
        return "conclusion", 1.0
    if _FOCUS_CHAR_RE.search(question):
        return "focus_character", 1.0
    if _RESONANCE_RE.search(question):
        return "resonance", 1.0

    # Tier 2: Prototype exemplar similarity scoring
    q_vec = _vectorize(question)
    best_intent: Intent = "general"
    best_score = 0.0

    # Similarity threshold for accepting a semantic prototype hit
    SIMILARITY_THRESHOLD = 0.28

    for intent, vecs in _EXEMPLAR_VECS.items():
        for e_vec in vecs:
            score = _dot_product(q_vec, e_vec)
            if score > best_score:
                best_score = score
                best_intent = intent

    if best_score >= SIMILARITY_THRESHOLD:
        return best_intent, best_score

    return "general", 0.0


def classify(question: str) -> Intent:
    """Classifies a question string into an Intent ("summarize" | "conclusion" |
    "focus_character" | "resonance" | "count" | "general")."""
    intent, _ = classify_with_score(question)
    return intent
