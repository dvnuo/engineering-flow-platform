---
name: git
description: Execute git commands and manage version control
version: 1.0.0
owner: devops-team
triggers:
  - git status
  - git commit
  - git push
  - git clone
  - git branch
  - git checkout
tools:
  - git_status
  - git_commit
  - git_push
  - git_clone
strategy:
  - "1. Parse the user's git request to determine the appropriate action"
  - "2. Execute the git command using the appropriate tool"
  - "3. Return the result in a clear format"
output_format: markdown
---

# Git Skill

**Capability**: Execute git commands using the built-in `git` tool.

## Available Tools

Your LLM has access to these git functions:

### git_status
Get git status of a repository.
```python
git_status(workspace=".")
```

### git_commit
Create a git commit with a message.
```python
git_commit(message="your commit message", workspace=".")
```

### git_push
Push changes to remote.
```python
git_push(workspace=".")
```

### git_clone
Clone a repository from URL.
```python
git_clone(repo_url="https://github.com/owner/repo.git", workspace=".")
```

## Usage Examples

When user asks about git operations, use the appropriate tool:

- **"git status"** → Use `git_status()`
- **"git commit -m 'msg'"** → Use `git_commit(message="msg")`
- **"git push"** → Use `git_push()`
- **"git clone url"** → Use `git_clone(repo_url="url")`

## Notes

- The workspace defaults to current directory (".")
- Git commands run in `~/.efp/workspace` by default
- SSH keys are automatically configured from config.yaml
