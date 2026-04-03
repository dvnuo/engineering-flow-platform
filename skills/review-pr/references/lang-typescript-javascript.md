# TypeScript / JavaScript Review Heuristics

- Validate async flow: missing `await`, unhandled promise rejections, fire-and-forget without error/reporting.
- Check error boundaries around async calls and retries/timeouts.
- Watch null/undefined handling at API boundaries and optional fields.
- For React/UI code: stale closures, incorrect hook dependencies, state updates from stale snapshots.
- Check contract/type regressions: narrowed/widened types that break callers.
- Flag overuse of `any`/unsafe `unknown` casting that bypasses safety.
- Verify runtime validation where static typing cannot protect external input.
