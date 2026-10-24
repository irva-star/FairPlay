# FairPlay Labs, Build North prototype

Spec: docs/SPEC.md. Read only the sections the task names.

## Hard rules (never break, never work around)
- The browser may reach only the two origins in walker/guard.py. Never add origins or weaken the guard.
- Never write code that fetches web pages. `anthropic` is imported only in walker/llm.py.
- The model sees only the schema-validated observation from walker/observe.py. Never send HTML, page text or screenshots.
- Agent tools are the fixed list in SPEC section 6. Never add navigation, typing, URL, file or shell tools.
- Model calls: claude-haiku-5-5, forced tool_choice, no temperature/top_p/top_k, no assistant prefill. Handle stop_reason "refusal" and "max_tokens".
- stats/ never reads oracle/. Only validation/ may.
- No real payments, accounts, credentials or personal data. No Person nodes in contextgraph.
- Never print or log ANTHROPIC_API_KEY. It lives only in .env on the laptop that runs batches,
  is loaded only by `walker.cli.load_env_file`, and is used only in walker/llm.py. The hosted
  game and the public dashboard never need it and must never receive it.
- Do not run walker batches, read data/ or oracle/, or browse the web. Ask the human instead.
- Ask before adding a dependency; pin its version in requirements.txt.

## Conventions
- Python 3.11, asyncio, type hints, pytest with pytest-asyncio. Modules match SPEC section 11.
- Local servers bind to 127.0.0.1; the game server always runs with one worker.
- Before finishing a task, run `pytest -q` and report only failures.
