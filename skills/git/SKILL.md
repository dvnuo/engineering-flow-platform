# Git Skill - Local Git Management

Use this skill to manage local git repositories. The AI has access to shell commands via `exec` tool, but this skill provides structured git operations.

## Available Tools

| Tool | Description |
|------|-------------|
| `git_status` | Show working tree status |
| `git_commit` | Commit changes |
| `git_push` | Push to remote |
| `git_pull` | Pull from remote |
| `git_branch` | List/create/delete branches |
| `git_log` | Show commit history |
| `git_diff` | Show changes |
| `git_checkout` | Switch branches |

## Usage Examples

```
User: Check git status
AI: → git_status()

User: Commit my changes
AI: → git_commit(message="Update README")

User: Push to main
AI: → git_push(branch="main")

User: Create a new branch
AI: → git_branch(name="feature/new-feature")

User: Show recent commits
AI: → git_log(limit=10)
```

## Best Practices

1. **Always check status first** before committing
2. **Use meaningful commit messages**
3. **Pull before pushing** to avoid conflicts
4. **Create feature branches** for new work

## Configuration

No configuration required - uses local git installed on the system.
