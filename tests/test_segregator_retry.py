"""A rate limit must delay a property decision, never discard it.

Opus assigning the property is the point of the segregation stage. Before this,
one 429 anywhere in a few thousand emails left that email with no decision at
all, and the run still reported success.
"""
from __future__ import annotations

import pytest

from mangotree.resolve.segregator import PropertySegregator


class _RateLimit(Exception):
    """Stands in for anthropic.RateLimitError, matched on class name."""

    def __init__(self):
        super().__init__("429 too many requests")
        self.status_code = 429


class _BadRequest(Exception):
    def __init__(self):
        super().__init__("400 invalid model")
        self.status_code = 400


class _Response:
    content = []


def _segregator(monkeypatch, side_effects):
    """A segregator whose API call replays a scripted list of outcomes."""
    seg = PropertySegregator.__new__(PropertySegregator)
    seg.model = "claude-opus-5"
    calls = {"n": 0}

    def fake_create(**kwargs):
        index = calls["n"]
        calls["n"] += 1
        outcome = side_effects[min(index, len(side_effects) - 1)]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    class _Messages:
        create = staticmethod(fake_create)

    class _Client:
        messages = _Messages()

    seg.client = _Client()
    monkeypatch.setattr("mangotree.resolve.segregator.time.sleep", lambda _s: None)
    return seg, calls


def test_a_rate_limit_is_retried_and_then_succeeds(monkeypatch):
    response = _Response()
    seg, calls = _segregator(monkeypatch, [_RateLimit(), _RateLimit(), response])

    assert seg._create("prompt") is response
    assert calls["n"] == 3, "should have retried twice before succeeding"


def test_sustained_rate_limiting_eventually_raises(monkeypatch):
    seg, calls = _segregator(monkeypatch, [_RateLimit()])

    with pytest.raises(_RateLimit):
        seg._create("prompt", attempts=3)
    assert calls["n"] == 3


def test_a_permanent_error_is_not_retried(monkeypatch):
    """Retrying a 400 burns minutes to fail the same way."""
    seg, calls = _segregator(monkeypatch, [_BadRequest()])

    with pytest.raises(_BadRequest):
        seg._create("prompt")
    assert calls["n"] == 1


def test_a_first_attempt_success_makes_one_call(monkeypatch):
    response = _Response()
    seg, calls = _segregator(monkeypatch, [response])

    assert seg._create("prompt") is response
    assert calls["n"] == 1
