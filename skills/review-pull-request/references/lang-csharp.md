# C# Review Heuristics

- Keep nullable reference annotations and runtime null checks consistent.
- Check async/await misuse (`async void`, blocking `.Result`, missed cancellation tokens).
- Ensure `IDisposable` resources are wrapped in `using`/`await using`.
- Review LINQ for readability and hidden perf regressions.
- For EF/ORM code: query translation, client-side evaluation risks, N+1 patterns.
- Validate DI lifetimes (singleton/scoped/transient) for thread-safety and stale state.
- Verify exception handling preserves stack/context and does not hide failures.
