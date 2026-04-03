# PHP Review Heuristics

- Watch weak typing surprises and implicit coercions.
- Check null/false/empty-string confusion in conditionals.
- Validate input sanitization/validation at request boundaries.
- Review SQL/XSS/file handling for injection and traversal risks.
- Ensure framework conventions are followed for security and maintainability.
- Flag hidden global state/session coupling that complicates behavior.
