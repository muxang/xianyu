---
description: Daily status report. What was done yesterday, what's next, blockers.
---

Produce a daily standup report by:

1. Running `git log --since=yesterday --oneline` to see recent commits.
2. Reading `docs/progress.md` (or create it if missing).
3. Checking which modules in `docs/modules/` have implementations vs. which don't.
4. Reporting:
   - Completed yesterday
   - In progress
   - Next 1-2 tasks
   - Blockers / open questions for the user

Update `docs/progress.md` with today's plan.
