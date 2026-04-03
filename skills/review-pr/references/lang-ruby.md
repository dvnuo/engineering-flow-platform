# Ruby Review Heuristics

- Check nil handling (`nil` propagation, `&.` overuse masking logic bugs).
- Flag opaque metaprogramming that hurts maintainability/debuggability.
- Review ActiveRecord query patterns for N+1 and implicit loading costs.
- Watch callback chains introducing surprising side effects.
- Identify monkey patches/implicit behavior changes with broad blast radius.
- Ensure external input is validated and escaped in persistence/rendering paths.
