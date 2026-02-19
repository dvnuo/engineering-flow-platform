# github/ - GitHub Integration

## Overview

The GitHub module provides integration with GitHub for repository management, pull requests, issues, and CI/CD workflows. Supports both REST API and GitHub CLI (`gh`).

## Structure

```
github/
├── api.py      # GitHub REST API client
├── cli.py      # GitHub CLI wrapper
└── __init__.py # Module exports
```

## Components

### GitHub API (`api.py`)
- REST API integration with GitHub
- Pull request operations (create, review, merge, close)
- Issue management (create, update, close)
- Repository operations (create, fork, star)

### GitHub CLI (`cli.py`)
- Wrapper for `gh` CLI tool
- PR creation, review, merge workflows
- Issue workflows
- Release management

## Quick Start

```python
from src.github import GitHubClient

# Initialize with token from config
gh = GitHubClient()

# Get pull requests
prs = gh.get_pull_requests(repo="owner/repo", state="open")

# Create a PR
pr = gh.create_pull_request(
    repo="owner/repo",
    title="Feature: New functionality",
    body="Description of changes",
    head="feature-branch",
    base="master"
)
```

## Configuration

```yaml
# In config.yaml
github:
  token: "ghp_your-token"
  api_url: "https://api.github.com"
  default_org: "your-organization"
  default_repo: "your-repo"

# Optional: GitHub CLI path
github_cli:
  path: "/usr/bin/gh"
```

## Dependencies

- `requests` - HTTP library for REST API calls
- `PyGithub` - GitHub API wrapper (optional, for REST)
- Standard library: `json`, `logging`
- Optional: `gh` CLI tool installed

## Development Guide

### REST API Operations

| Operation | Method | Description |
|-----------|--------|-------------|
| List PRs | `get_pull_requests()` | Get all PRs |
| Create PR | `create_pull_request()` | Create new PR |
| Review PR | `create_review()` | Add PR review |
| Merge PR | `merge_pull_request()` | Squash/merge PR |
| Create Issue | `create_issue()` | Create new issue |

### CLI Operations

```python
from src.github.cli import GitHubCLI

gh_cli = GitHubCLI()

# Create PR using gh CLI
gh_cli.run("pr create --title 'PR Title' --body 'Description'")

# Review PR
gh_cli.run("pr review --approve --body 'LGTM'")
```

### Best Practices

- Use CLI for operations requiring authentication
- Handle rate limits with exponential backoff
- Use organizations for team management
- Enable branch protection rules
