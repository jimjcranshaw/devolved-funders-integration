"""DeepSeek-only LLM helper. No other provider lives in this repo."""

from __future__ import annotations

import os

DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"
DEEPSEEK_MODEL = "deepseek-chat"


def evaluate_text(prompt: str, system: str = "You extract structured funder data as JSON.", max_tokens: int = 2000) -> dict:
    """Send one bounded chat completion to DeepSeek. Lazy-imports httpx."""
    import httpx

    api_key = os.getenv("DEEPSEEK_API_KEY", "")
    if not api_key:
        raise RuntimeError("Missing DEEPSEEK_API_KEY in .env")
    resp = httpx.post(
        DEEPSEEK_API_URL,
        headers={"Authorization": f"Bearer {api_key}"},
        json={
            "model": DEEPSEEK_MODEL,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt[:12000]},
            ],
            "max_tokens": max_tokens,
            "temperature": 0,
        },
        timeout=120,
    )
    resp.raise_for_status()
    return resp.json()
