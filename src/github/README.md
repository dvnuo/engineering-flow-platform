# github/ - GitHub Integration

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
- Pull request operations
- Issue management
- Repository operations

### GitHub CLI (`cli.py`)
- Wrapper for `gh` CLI tool
- PR creation, review, merge
- Issue workflows

## Usage

```python
from src.github import GitHubClient

gh = GitHubClient()
prs = gh.get_pull_requests()
```
