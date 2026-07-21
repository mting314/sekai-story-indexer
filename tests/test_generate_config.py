"""Regression guard for the mid-sentence answer truncation bug.

A flash "thinking" model bills its internal reasoning against ``max_output_tokens``.
With the old flat 4096 cap, a detailed event summary spent ~3.8k tokens thinking and
had only ~300 left for the visible answer, so ``finish_reason`` came back ``MAX_TOKENS``
and the prose cut off mid-word ("...ask Kanade Yoisaki for help [3]. Visiting").

We can't call the real API keylessly, but the fix lives entirely in the shared
generation config: a generous output ceiling so the answer never truncates, plus
``thinking_level="low"`` to keep reasoning (and cost) small. These tests pin that
invariant via a fake ``types`` module.
"""

from sekai_story_indexer.query.generate import (
    _MAX_OUTPUT_TOKENS,
    _THINKING_LEVEL,
    _generation_config,
)


class _FakeThinkingConfig:
    def __init__(self, thinking_level):
        self.thinking_level = thinking_level


class _FakeConfig:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class _FakeTypes:
    ThinkingConfig = _FakeThinkingConfig
    GenerateContentConfig = _FakeConfig


class _OldTypes:
    """A google-genai build predating ThinkingConfig."""

    GenerateContentConfig = _FakeConfig


def test_answer_has_room_and_thinking_is_cheap() -> None:
    cfg = _generation_config(_FakeTypes, "sys")

    # generous output ceiling so the answer can't truncate mid-sentence...
    assert cfg.kwargs["max_output_tokens"] == _MAX_OUTPUT_TOKENS >= 8192
    # ...and thinking held to the cheap tier so cost/latency stay low.
    assert cfg.kwargs["thinking_config"].thinking_level == _THINKING_LEVEL == "low"


def test_generation_config_degrades_without_thinking_support() -> None:
    cfg = _generation_config(_OldTypes, "sys")

    # older builds still get the big output budget; thinking_config simply omitted
    assert "thinking_config" not in cfg.kwargs
    assert cfg.kwargs["max_output_tokens"] >= 8192


def test_generation_config_passes_system_instruction() -> None:
    cfg = _generation_config(_FakeTypes, "the-system-prompt")

    assert cfg.kwargs["system_instruction"] == "the-system-prompt"
    assert cfg.kwargs["temperature"] == 0.2
