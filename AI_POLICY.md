# AI Policy — DeepSeek only (PAYG)

All text parsing, understanding, and evaluation in this repo uses **DeepSeek only**
(`DEEPSEEK_API_KEY`, model `deepseek-chat` via `llm/deepseek.py`).

* No OpenAI / Anthropic / Gemini calls. The `openai` package must not even be
  installed here (see `requirements.txt` + `test_no_openai.py`).
* The older `oscr-integration` pipelines call OpenAI `text-embedding-3-small`
  for `chunk_embeddings`. That is **out of scope** for this repo: our backfill
  (#5) runs with embeddings **off** (`--no-embeddings`), storing programme rows
  without vectors. Vectors can be backfilled later if ever needed.
* Cost guards: DeepSeek calls are bounded per funder (cap pages, cap chars,
  one call per funder). Every script that calls DeepSeek takes `--dry-run`
  (prints what it *would* send) and `--max-funders N`.

## Fallback (user-authorised): OpenRouter free models only

`--llm router` (`llm/router.py`) tries DeepSeek first and falls back to
OpenRouter **free-model routing** (`openrouter/auto`) ONLY on DeepSeek
billing/auth/rate failure (401/402/429) or connection error. Rules:

* The OpenRouter model string is pinned to free routing; no paid model string
  exists anywhere in this repo (enforced by `test_router.py`).
* Every routed call records its provider (`deepseek` or `openrouter-free`)
  in outputs and heartbeat lines, so free-model rows are auditable.
* Requires `OPENROUTER_API_KEY` in `.env` (free key, no charge). Without it,
  DeepSeek failure raises instead of falling back.
