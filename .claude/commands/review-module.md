---
description: Run code-reviewer subagent on a specific module.
---

Invoke the `code-reviewer` subagent to audit module `$1`.

Read:
- `docs/modules/$1.md` (the spec)
- All files under `backend/app/$1/`
- Tests under `backend/tests/$1/`

Produce a full review report per the code-reviewer's template.
