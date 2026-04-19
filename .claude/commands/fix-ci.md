---
description: Fix all CI / lint / test failures.
---

There are CI / lint / test failures. Your job is to fix them all.

1. Run `uv run ruff check . --fix` (auto-fix what's fixable)
2. Run `uv run ruff check .` (see remaining issues)
3. Run `uv run mypy app/` (type errors)
4. Run `uv run pytest` (failing tests)

Fix everything. Don't disable tests or add `# type: ignore` without explanation.

Report a summary of fixes.
