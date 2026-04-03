# Kotlin Review Heuristics

- Check null-safety regressions (`!!`, platform type assumptions).
- Review coroutine scope, structured concurrency, and cancellation behavior.
- Validate Java interop edges (nullability, checked exceptions, mutability).
- Flag shared mutable state misuse across threads/flows.
- Review Flow/StateFlow usage for replay/backpressure/lifecycle pitfalls.
- Ensure API changes preserve binary/source compatibility where required.
