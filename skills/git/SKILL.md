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
