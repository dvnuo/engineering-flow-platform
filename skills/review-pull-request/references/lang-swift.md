# Swift Review Heuristics

- Validate optional handling without unsafe force unwraps.
- Check retain cycle risks (closures capturing `self` strongly).
- Review concurrency correctness (`MainActor`, task cancellation, actor isolation).
- Ensure UI state updates occur on the correct execution context.
- Watch value/reference semantics confusion causing stale or unintended mutation.
- Verify error handling communicates recoverable vs terminal failures.
