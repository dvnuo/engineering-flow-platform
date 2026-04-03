# Rust Review Heuristics

- Flag unnecessary `unwrap`/`expect` in non-test code.
- Prefer explicit error propagation with context over panic paths.
- Scrutinize `unsafe` boundaries and invariants documentation.
- Watch ownership patterns that are technically correct but hard to maintain.
- Check `Send`/`Sync` assumptions in async/concurrent code.
- Validate borrow/lifetime workarounds do not hide logic flaws.
- Ensure fallible operations are surfaced to callers meaningfully.
