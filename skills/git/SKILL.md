# Git Skill - Local Git Management

Use this skill to manage local git repositories. The AI has access to shell commands via `exec` tool, but this skill provides structured git operations.

## Available Commands

Use the `git` tool with different commands:

```
git(command="status")
git(command="clone", path="https://github.com/owner/repo.git")
git(command="commit", message="Update")
git(command="push", branch="master")
git(command="pull")
git(command="branch", name="feature/new")
git(command="log", limit=10)
git(command="checkout", branch="master")
git(command="diff")
git(command="add", path=".")
```

## Command Reference

| Command | Description | Parameters |
|---------|-------------|-----------|
| `status` | Show working tree status | - |
| `clone` | Clone a repository | `path` (repo URL), `repo_path` (target dir) |
| `commit` | Commit staged changes | `message` |
| `push` | Push to remote | `branch` |
| `pull` | Pull from remote | - |
| `branch` | List/create/delete branches | `name`, `delete` |
| `log` | Show commit history | `limit` |
| `checkout` | Switch branches | `branch` |
| `diff` | Show unstaged changes | - |
| `add` | Stage files | `path` |

## Usage Examples

```
User: Clone a repository
AI: git(command="clone", path="https://github.com/itwake/opsclaw.git")

User: Check git status
AI: git(command="status")

User: Commit my changes
AI: git(command="commit", message="Update README")

User: Push to master
AI: git(command="push", branch="master")

User: Create a new branch
AI: git(command="branch", name="feature/new-feature")

User: Show recent commits
AI: git(command="log", limit=10)

User: Switch to master branch
AI: git(command="checkout", branch="master")
```

## Best Practices

1. **Always check status first** before committing
2. **Use meaningful commit messages**
3. **Pull before pushing** to avoid conflicts
4. **Create feature branches** for new work

## Configuration

No configuration required - uses local git installed on the system.

## SSH vs HTTPS Clone

```
# SSH clone (requires SSH key configured)
git(command="clone", path="git@github.com:owner/repo.git")

# HTTPS clone
git(command="clone", path="https://github.com/owner/repo.git")
```
