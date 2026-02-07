---
name: git
description: Execute git commands for repository management, version control, and collaborative workflows
metadata:
  emoji: 🔀
  requires:
    bins: [git]
    anyBins: []
    env: []
    config: []
---
# Git Skill - Local Git Management

Execute any git command with flexible arguments.

## Skill Signature

\`\`\`python
git(command="status", args="", cwd=None) -> SkillResult
\`\`\`

## Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `command` | string | No | Git subcommand (default: "status") |
| `args` | string | No | Additional arguments (space-separated) |
| `cwd` | string | No | Working directory |

## Examples

### Basic Commands

\`\`\`python
# Check repository status
git(command="status")

# View current branch and changes
git(command="status", args="--short")

# List all branches
git(command="branch", args="-a")

# View recent commits
git(command="log", args="--oneline -10")

# Show differences
git(command="diff")

# Stage all changes
git(command="add", args="-A")
\`\`\`

### Common Operations

\`\`\`python
# Clone a repository (HTTPS)
git(command="clone", args="https://github.com/owner/repo.git")

# Clone a repository (SSH)
git(command="clone", args="git@github.com:owner/repo.git")

# Commit with message
git(command="commit", args="-m 'feat: add new feature'")

# Push to remote
git(command="push", args="origin main")

# Pull from remote
git(command="pull", args="origin main")

# Create and switch to new branch
git(command="checkout", args="-b feature/new-feature")

# Switch to existing branch
git(command="checkout", args="develop")

# Delete a branch
git(command="branch", args="-d feature/old-feature")
\`\`\`

### Advanced Commands

\`\`\`python
# Rebase onto main branch
git(command="rebase", args="main")

# Stash changes
git(command="stash", args="push -m 'WIP: work in progress'")

# List stashes
git(command="stash", args="list")

# Apply stash
git(command="stash", args="apply stash@{0}")

# Cherry-pick a commit
git(command="cherry-pick", args="abc123def")

# Reset to previous commit (soft)
git(command="reset", args="HEAD~1")

# Reset hard (discard changes)
git(command="reset", args="--hard HEAD~1")

# Merge a branch
git(command="merge", args="feature-branch")

# Fetch all remotes
git(command="fetch", args="--all --prune")

# View tags
git(command="tag", args="-l")

# Create tag
git(command="tag", args="v1.0.0")

# Show remote URLs
git(command="remote", args="-v")

# Add remote
git(command="remote", args="add origin https://github.com/owner/repo.git")

# Blame a file
git(command="blame", args="README.md")

# Search in history
git(command="grep", args="'TODO' -- '*.py'")

# Show file at specific commit
git(command="show", args="abc123:path/to/file.py")

# Start bisect
git(command="bisect", args="start")
\`\`\`

## Working Directory

By default, git commands run in `~/.opsclaw/workspace`. Override with `cwd`:

\`\`\`python
# Run in specific directory
git(command="status", cwd="/path/to/my/repo")
\`\`\`

## SSH Key Setup

For private repositories, configure SSH key in `config.yaml`:

\`\`\`yaml
ssh:
  enabled: true
  private_key_path: "/run/secrets/github_ssh_key"
\`\`\`

The SSH key is automatically copied to `~/.ssh/` with proper permissions (600) at startup.

## Tips

1. **Use `--` to separate file paths from options**: `git(command="log", args="--oneline -10 -- .")`
2. **Quote arguments with spaces**: `args="-m 'commit message'"`
3. **Use `-` for stdin**: `git(command="apply", args="- < patch.diff")`
4. **Combine commands**: Stage → Commit → Push in one flow
