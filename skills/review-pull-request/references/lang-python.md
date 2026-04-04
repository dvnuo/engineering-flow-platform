# Python Review Heuristics

- Avoid broad `except Exception`/bare `except` unless re-raising with context.
- Detect mutable default arguments and shared state leaks.
- Ensure resource cleanup (`with`, close/finally) for files/network/db handles.
- Check return contracts: implicit `None` where caller expects value.
- Watch import-time side effects (network calls, env mutation, heavy initialization).
- Validate dynamic typing boundaries with explicit checks at external input edges.
- Ensure logging preserves actionable diagnostics without leaking secrets.
