# git/ - Git Operations

## Overview

The Git module provides core repository operations (status/commit/clone/push/pull) through the Git CLI.

## Structure

```
git/
├── api.py      # Git operations API and HTTPS auth helpers
└── __init__.py # Module exports and tool schemas
```

## Authentication Model

- Remote Git transport uses **HTTPS**.
- `github.api_token` is the single token source for:
  - GitHub REST API tools (GitHub module)
  - Git-over-HTTPS auth for clone/push/pull (Git module)
- `git.user.name` and `git.user.email` are only for commit identity (`git config --global`).

## Quick Start

```python
from src.git import GitClient

git = GitClient()

await git.clone("git@github.com/owner/repo.git")  # SSH-style input accepted, normalized to HTTPS
await git.status()
await git.commit("Initial commit")
await git.push()
```

## Configuration

```yaml
github:
  enabled: true
  api_token: "${GITHUB_TOKEN}"

git:
  user:
    name: "Engineering Flow Platform Bot"
    email: "bot@company.com"
```

## Best Practices

- Keep `github.api_token` scoped minimally (least privilege).
- Avoid embedding credentials in repository URLs.
- Keep `git.user.*` configured for clear commit attribution.
