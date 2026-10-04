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
