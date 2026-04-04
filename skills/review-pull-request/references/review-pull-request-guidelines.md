# Pull Request Review Guidelines

Focus on reviewer value, not volume.

- Confirm intent before nitpicks: what behavior changed, what could regress, what users/operators notice.
- Prefer concrete findings over speculative concerns.
- Anchor comments to exact file/line only when the issue is specific and fixable.
- Skip cosmetic-only feedback unless readability impacts correctness or maintenance risk.
- Check changed tests and identify meaningful gaps (missing edge cases, error paths, migration paths).
- Watch for backward compatibility breaks in API contracts, schema/data formats, config behavior, and automation interfaces.
- Validate security-sensitive paths (authz/authn, secrets handling, injection risks, unsafe deserialization, privilege escalation).
- Avoid duplicate feedback when the same issue is already present in pull request comments/reviews.
- If no material issues exist, summarize reviewed risk areas and residual validation gaps.
