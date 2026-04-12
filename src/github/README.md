# github/ - GitHub Integration

## Overview

The GitHub module provides **GitHub REST API** integrations for repository and collaboration workflows (PRs, issues, repo metadata, etc.). REST API is the primary runtime path.

## Runtime Authentication Model

- Active GitHub API authentication key: `github.api_token`.
- Git repository transport authentication (clone/push/pull) is handled by the **Git module** (`src/git/api.py`) using HTTPS + askpass with `github.api_token`.
- EFP startup/runtime does **not** depend on writing `gh` CLI auth config as the primary authentication path.

## Structure

```
github/
├── api.py      # GitHub REST API client/channel
├── cli.py      # Optional GitHub CLI helper wrapper
└── __init__.py # Module exports
```

## Notes on `cli.py`

`src/github/cli.py` may still be used as an optional helper wrapper, but it is not the active runtime authentication path.

## Configuration

```yaml
github:
  enabled: true
  api_token: "${GITHUB_TOKEN}"
  base_url: ""          # Optional GitHub Enterprise hostname/root configuration
```

## Best Practices

- Keep `github.api_token` scoped with least privilege.
- Prefer REST API operations via `src/github/api.py` for runtime GitHub actions.
- Keep Git transport concerns in `src/git/api.py` (HTTPS remote + askpass), not in GitHub module docs/config.
