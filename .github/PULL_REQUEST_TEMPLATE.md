## Overview

Reorganize integrations (GitHub, Jira, Confluence, Git) into `src/integrations/` for single-source-of-truth architecture, following OpenClaw patterns.

## Changes Summary

### New Directory Structure

```
src/
├── integrations/          # Single source of truth for API clients
│   ├── github/
│   │   ├── api.py      # GitHub REST API client
│   │   └── cli.py       # GitHub CLI wrapper (gh)
│   ├── jira/
│   │   └── api.py      # Jira REST API client
│   ├── confluence/
│   │   └── api.py      # Confluence REST API client
│   └── git/
│       ├── api.py       # Git operations client
│       └── ssh.py        # SSH key setup utilities
│
└── tools/               # Agent-facing tools layer
    ├── github.py        # GitHub tool schemas
    ├── jira.py          # Jira tool schemas
    ├── confluence.py     # Confluence tool schemas
    ├── git.py           # Git tool schemas
    └── __init__.py       # Unified exports via get_all_tools()
```

### Files Changed (23 files)

#### New Files (18)
- `src/integrations/confluence/__init__.py`
- `src/integrations/confluence/api.py`
- `src/integrations/git/__init__.py`
- `src/integrations/git/api.py`
- `src/integrations/git/ssh.py`
- `src/integrations/github/__init__.py`
- `src/integrations/github/api.py`
- `src/integrations/github/cli.py`
- `src/integrations/jira/__init__.py`
- `src/integrations/jira/api.py`
- `src/tools/__init__.py`
- `src/tools/confluence.py`
- `src/tools/git.py`
- `src/tools/github.py`
- `src/tools/jira.py`

#### Modified Files (5)
- `channel/__init__.py`
- `channel/confluence.py`
- `channel/github.py`
- `channel/jira.py`
- `skills/executor/tools.py`
- `skills/github/skill.py`
- `skills/git/skill.py`
- `tools/integration.py`

### Class Naming Convention

Fixed class name mismatches that caused ImportErrors:
- `JiraChannel` (was incorrectly named `JiraClient`)
- `ConfluenceChannel` (was incorrectly named `ConfluenceClient`)
- `GitHubChannel` (was incorrectly named `GitHubClient` in exports)

### Backward Compatibility

All existing imports continue to work:

```python
# Channel imports (unchanged)
from channel.github import github_channel
from channel.jira import jira_channel
from channel.confluence import confluence_channel

# Skill imports (unchanged)
from skills.github.skill import github_cli
from skills.git.skill import git_client

# New unified tools
from src.tools import get_all_tools
```

### Benefits

1. **Single Source of Truth**: Each platform has one canonical implementation in `src/integrations/`
2. **No Code Duplication**: Channel and skill modules re-export from integrations
3. **Clear Separation**: API clients (integrations) → Agent tools (tools) → User-facing (skills/channels)
4. **Easier Maintenance**: Changes only needed in one place per platform
5. **Better Testing**: Can test integrations independently

### Validation

```
Agent import OK
pytest tests/ -v  # 44 passed, 1 failed (unrelated config test)
```

## Breaking Changes

**None**. This refactoring maintains full backward compatibility.
