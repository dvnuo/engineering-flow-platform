---
name: github
description: Interact with GitHub using the GitHub API
version: 1.0.0
owner: devops-team
triggers:
  - github issue
  - github pr
  - github search
  - github comment
  - github repo
tools:
  - github_get_issue
  - github_search_issues
  - github_add_comment
strategy:
  - "1. Parse the GitHub request to determine the appropriate action"
  - "2. Execute the GitHub API call using the appropriate tool"
  - "3. Return the result in a clear format"
output_format: markdown
---

# GitHub Skill

**Capability**: Interact with GitHub using the GitHub API.

## Available Tools

Your LLM has access to these GitHub functions:

### github_get_issue
Get details of a GitHub issue or PR.
```python
github_get_issue(owner="owner", repo="repo", issue_number=123)
```

### github_search_issues
Search GitHub issues and PRs.
```python
github_search_issues(query="is:pr state:open", max_results=10)
```

### github_add_comment
Add a comment to an issue or PR.
```python
github_add_comment(owner="owner", repo="repo", issue_number=123, comment="Your comment")
```

## Usage Examples

When user asks about GitHub operations, use the appropriate tool:

- **"github issue #123"** → Use `github_get_issue()`
- **"search for PRs"** → Use `github_search_issues()`
- **"add a comment"** → Use `github_add_comment()`

## Notes

- Requires GitHub API authentication
- Uses environment variable `GITHUB_TOKEN` or config for authentication
