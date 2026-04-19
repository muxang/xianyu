---
description: Run benchmark tests for a module (e.g. retrieval accuracy).
---

Run the benchmark suite for module `$1`.

Execute: `uv run pytest tests/$1/benchmarks/ -v --tb=short`

Then:
1. Report the metrics (precision, recall, latency)
2. Compare against target in spec
3. If below target, suggest the top 3 things to improve (not implement, just suggest)
