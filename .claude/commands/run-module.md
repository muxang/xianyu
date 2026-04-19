---
description: Implement a module end-to-end following the spec.
---

Implement module `$1` (e.g. "module_02") end-to-end.

Steps:
1. Read `docs/modules/$1.md` fully.
2. Plan the implementation: files to create, order, risks. Present the plan and wait for my confirmation.
3. Implement step by step, writing code AND tests together.
4. Run `uv run ruff check .` and `uv run mypy app/` after each major chunk.
5. Run `uv run pytest tests/$1/ -v --cov=app.$1` after completion.
6. Invoke `code-reviewer` subagent to audit.
7. Report: what was built, test coverage, review results, known gaps.

Module ID: $1
