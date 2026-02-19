# git/ - Git Operations

## Overview

The Git module provides git operations for repository management, version control, and collaboration workflows. Supports local and remote repository operations.

## Structure

```
git/
├── api.py      # Git operations API
├── ssh.py      # SSH key management
└── __init__.py # Module exports
```

## Components

### Git API (`api.py`)
- Repository operations (clone, init)
- Branch management (create, list, delete)
- Commit and push operations
- File operations (add, checkout, restore)
- Tag management

### SSH Management (`ssh.py`)
- SSH key configuration
- Remote URL handling
- Credential management

## Quick Start

```python
from src.git import GitClient

# Initialize
git = GitClient()

# Clone a repository
git.clone_repo("https://github.com/owner/repo.git", "/path/to/dir")

# Create and switch to branch
git.checkout("-b", "feature/new-feature")

# Stage and commit
git.add(".")
git.commit("Initial commit message")

# Push to remote
git.push("origin", "feature/new-feature")
```

## Configuration

```yaml
# In config.yaml
git:
  default_branch: "master"
  user_name: "Your Name"
  user_email: "your-email@example.com"
  ssh_key_path: "~/.ssh/id_rsa"

# SSH known hosts (for private repos)
ssh:
  known_hosts:
    - github.com
    - gitlab.com
```

## Dependencies

- `GitPython` - Python library for Git operations
- Standard library: `subprocess`, `pathlib`, `logging`
- System dependency: `git` CLI installed

## Development Guide

### Common Operations

| Operation | Method | Description |
|-----------|--------|-------------|
| Clone | `clone_repo(url, path)` | Clone remote repository |
| Init | `init(path)` | Initialize new repository |
| Branch | `checkout(*args)` | Switch/create branches |
| Commit | `commit(message)` | Create new commit |
| Push | `push(remote, branch)` | Push to remote |
| Pull | `pull(remote, branch)` | Pull from remote |

### SSH Setup

```python
from src.git.ssh import SSHManager

ssh = SSHManager()
ssh.add_key("~/.ssh/id_rsa")
ssh.set_known_hosts("github.com", "ssh-rsa ...")
```

### Best Practices

- Always pull before pushing
- Use meaningful commit messages
- Create feature branches for new work
- Delete merged branches promptly
- Use SSH for private repositories
