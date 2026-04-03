# Go Review Heuristics

- Ensure errors are handled; avoid silent `_` discards on critical calls.
- Check goroutine lifecycle and cancellation to prevent leaks.
- Verify `context.Context` propagation and timeout/deadline usage.
- Validate nil handling for pointers/interfaces/maps/slices.
- Flag concurrent access to maps/slices without synchronization.
- Review interface boundaries: minimal interfaces, clear ownership of behavior.
- Confirm deferred cleanup executes on all return/error paths.
