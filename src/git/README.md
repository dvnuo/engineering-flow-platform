# git/ - Git Operations

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
- Branch management
- Commit and push operations
- File operations (add, checkout)

### SSH Management (`ssh.py`)
- SSH key configuration
- Remote URL handling
- Credential management

## Usage

```python
from src.git import GitClient

git = GitClient()
git.clone_repo(url, path)
git.commit(message)
```
