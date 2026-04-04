# C / C++ Review Heuristics

- Review memory safety: ownership, allocation/free symmetry, leaks, double-free.
- Check lifetime/aliasing confusion (dangling pointers/references).
- Identify undefined behavior risks (overflow, invalid casts, uninitialized reads).
- Protect ABI/API compatibility for exported interfaces.
- Validate concurrency correctness (data races, lock ordering, atomics usage).
- Ensure bounds checking on arrays/buffers and safe string handling.
- Confirm error handling paths release resources consistently.
