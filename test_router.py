"""Tests for llm/router.py fallback discipline (no network)."""

import httpx

from llm import router


def _resp(status):
    return httpx.Response(status, request=httpx.Request("POST", "https://x"))


def test_billing_statuses_trigger_fallback():
    assert set(router._BILLING_STATUS) == {401, 402, 429}


def test_openrouter_pinned_to_free_routing():
    payload = router._openrouter_payload("p", "s", 10)
    assert payload["model"] == "openrouter/auto"
    assert "auto" in payload["models"][0]


def test_no_paid_model_strings_anywhere():
    import pathlib

    text = pathlib.Path("llm/router.py").read_text()
    for banned in ("gpt-", "claude-", "gemini", "text-embedding", ":paid"):
        assert banned not in text, banned


def test_fallback_only_on_billing_status(monkeypatch):
    calls = []

    def fake_post(url, headers, payload, timeout=120):
        calls.append(url)
        if "deepseek" in url:
            raise httpx.HTTPStatusError("x", request=httpx.Request("POST", url),
                                        response=_resp(500))
        return {"ok": True}

    monkeypatch.setattr(router, "_post", fake_post)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    try:
        router.evaluate_text("hi")
    except httpx.HTTPStatusError:
        pass
    else:
        raise AssertionError("500 must not fall back")
    assert len(calls) == 1 and "deepseek" in calls[0]


def test_fallback_fires_on_402(monkeypatch):
    calls = []

    def fake_post(url, headers, payload, timeout=120):
        calls.append(url)
        if "deepseek" in url:
            raise httpx.HTTPStatusError("x", request=httpx.Request("POST", url),
                                        response=_resp(402))
        return {"choices": []}

    monkeypatch.setattr(router, "_post", fake_post)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    data, provider = router.evaluate_text("hi")
    assert provider == "openrouter-free" and data == {"choices": []}
    assert len(calls) == 2
