# SQL Review Heuristics

- Validate join keys/cardinality to avoid duplicate/missing rows.
- Check transaction and isolation behavior for correctness under concurrency.
- Assess schema compatibility: nullable/constraint/default changes and migration safety.
- Identify N+1 patterns, missing index usage, full-table scan regressions.
- Verify null semantics (`NULL` comparisons, `COALESCE` behavior).
- Ensure parameterized queries to prevent injection.
- Watch destructive DDL/DML without safeguards or phased rollout.
