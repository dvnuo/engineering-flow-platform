# Shell / Bash Review Heuristics

- Quote variable expansions to prevent glob/split bugs.
- Prefer `set -euo pipefail` unless script intentionally handles partial failures.
- Flag dangerous composition (`eval`, untrusted input in command construction).
- Check environment/secrets leakage via logs/exports.
- Validate exit code handling for piped/subshell commands.
- Ensure temp files/directories are cleaned (`trap` for cleanup).
- Confirm portability assumptions (bashisms vs sh) if scripts are cross-shell.
