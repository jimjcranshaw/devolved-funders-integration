"""Guard: no non-DeepSeek AI provider may ever be imported here."""

from pathlib import Path

BANNED = ("from openai", "import openai", "from anthropic", "import anthropic",
          "import google.generativeai", "from google.generativeai")


def test_no_banned_ai_imports():
    root = Path(__file__).parent
    offenders = []
    for py in list(root.rglob("*.py")):
        if ".git" in py.parts:
            continue
        if py.name == "test_no_openai.py":
            continue  # ban-list lives here as string literals, not imports
        text = py.read_text(errors="ignore")
        if any(b in text for b in BANNED):
            offenders.append(str(py))
    assert offenders == [], f"Banned AI imports in: {offenders}"


def test_deepseek_helper_model():
    from llm.deepseek import DEEPSEEK_API_URL, DEEPSEEK_MODEL

    assert "api.deepseek.com" in DEEPSEEK_API_URL
    assert DEEPSEEK_MODEL == "deepseek-chat"
