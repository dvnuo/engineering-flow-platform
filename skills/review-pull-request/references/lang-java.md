# Java Review Heuristics

- Check null safety and `Optional` usage (avoid `Optional.get()` without guard).
- Detect swallowed exceptions or logging without propagation/handling.
- Validate transaction boundaries and rollback semantics.
- Review stream usage for clarity and side-effect safety.
- Flag shared mutable state in concurrent contexts.
- Protect API compatibility: signature changes, semantic behavior shifts, serialization changes.
- Ensure resource lifecycles (`try-with-resources`) are correctly scoped.
