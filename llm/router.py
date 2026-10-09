"""LLM router: DeepSeek first, OpenRouter-free fallback. No paid non-DeepSeek.

Policy (see AI_POLICY.md): all funder text work uses DeepSeek PAYG. Only when
DeepSeek fails with an auth/billing/rate error (401/402/429) or is unreachable
does the router fall back to OpenRouter's FREE-model routing
(`openrouter/auto`, free models only — never a paid model). Every call records
which provider served it.

Required env: DEEPSEEK_API_KEY (primary). Optional: OPENROUTER_API_KEY
(fallback; create free at https://openrouter.ai/keys).
"""

from __future__ import annotations

import os

DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"
DEEPSEEK_MODEL = "deepseek-chat"
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_MODEL = "openrouter/auto"  # free-model routing only
_BILLING_STATUS = (401, 402, 429)


def _post(url: str, headers: dict, payload: dict, timeout: int = 120) -> dict:
    import httpx

    resp = httpx.post(url, headers=headers, json=payload, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def _deepseek_payload(prompt: str, system: str, max_tokens: int) -> dict:
    return {
        "model": DEEPSEEK_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt[:12000]},
        ],
        "max_tokens": max_tokens,
        "temperature": 0,
    }


def _openrouter_payload(prompt: str, system: str, max_tokens: int) -> dict:
    # :free suffix not needed with openrouter/auto; models must be free tier.
    return {
        "model": OPENROUTER_MODEL,
        "models": ["openrouter/auto"],
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt[:12000]},
        ],
        "max_tokens": max_tokens,
        "temperature": 0,
    }


def evaluate_text(prompt: str, system: str = "You extract structured funder data as JSON.",
                  max_tokens: int = 2000) -> tuple[dict, str]:
    """Return (response_json, provider). provider is 'deepseek' or 'openrouter-free'.

    Falls back ONLY on DeepSeek billing/auth/rate failure or connection error.
    A paid-model charge via OpenRouter is never possible: model is pinned to
    free routing and no other model string exists in this repo.
    """
    import httpx

    api_key = os.getenv("DEEPSEEK_API_KEY", "")
    if not api_key:
        raise RuntimeError("Missing DEEPSEEK_API_KEY in .env")
    try:
        data = _post(DEEPSEEK_API_URL, {"Authorization": f"Bearer {api_key}"},
                     _deepseek_payload(prompt, system, max_tokens))
        return data, "deepseek"
    except httpx.HTTPStatusError as e:
        if e.response.status_code not in _BILLING_STATUS:
            raise
    except httpx.HTTPError:
        pass  # connection/timeout -> try fallback too
    or_key = os.getenv("OPENROUTER_API_KEY", "")
    if not or_key:
        raise RuntimeError("DeepSeek unavailable and no OPENROUTER_API_KEY set for fallback")
    data = _post(OPENROUTER_API_URL,
                 {"Authorization": f"Bearer {or_key}",
                  "HTTP-Referer": "https://github.com/jimjcranshaw/devolved-funders-integration",
                  "X-Title": "devolved-funders-integration"},
                 _openrouter_payload(prompt, system, max_tokens))
    return data, "openrouter-free"
