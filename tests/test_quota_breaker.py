"""Quota/spend-cap circuit breaker: once generation hits a 429/quota, skip further
generation (fall back to extractive) for a cooldown instead of paying doomed calls.
Keyless — the breaker is pure time + string matching."""

import pytest

from sekai_story_indexer.query import generate


@pytest.fixture(autouse=True)
def _reset_breaker():
    generate._clear_quota_breaker()
    yield
    generate._clear_quota_breaker()


def test_is_quota_error_matches_cap_signals_only():
    assert generate._is_quota_error(Exception("status_code: 429, RESOURCE_EXHAUSTED"))
    assert generate._is_quota_error(Exception("exceeded its monthly spending cap"))
    assert generate._is_quota_error(Exception("prepayment credits are depleted; quota"))
    assert not generate._is_quota_error(Exception("connection reset by peer"))
    assert not generate._is_quota_error(Exception("400 INVALID_ARGUMENT thinking_level"))


def test_breaker_gates_generation_available(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    assert generate.generation_available() is True
    assert generate.quota_paused() is False

    generate._trip_quota_breaker()
    assert generate.quota_paused() is True
    assert generate.generation_available() is False  # skip the doomed round-trip

    generate._clear_quota_breaker()
    assert generate.generation_available() is True  # a success clears it


def test_generate_answer_short_circuits_while_paused(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    generate._trip_quota_breaker()
    # citations present, but paused -> returns None without importing/calling genai
    assert generate.generate_answer("q", [{"ref": 1, "excerpt": "x"}]) is None


def test_generate_stream_yields_nothing_while_paused(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    generate._trip_quota_breaker()
    assert list(generate.generate_answer_stream("q", [{"ref": 1, "excerpt": "x"}])) == []
