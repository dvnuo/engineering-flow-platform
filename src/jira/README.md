# jira/ - Jira Integration

## Overview

The Jira module provides integration with Atlassian Jira for issue tracking, project management, and team collaboration. It supports both Cloud and Server deployments.

## Structure

```
jira/
├── api.py      # Jira REST API client
└── __init__.py # Module exports
```

## Components

### Jira API (`api.py`)
- Issue search and retrieval
- Issue creation and updates
- Comment management
- Transition support
- JQL query execution

## Quick Start

```python
from src.jira import JiraClient

# Initialize with credentials from config
jira = JiraClient()

# Search for issues
issues = jira.search_issues("project = PROJ AND status = Open")

# Create a new issue
issue = jira.create_issue(
    project_key="PROJ",
    summary="New issue title",
    description="Issue description",
    issue_type="Task"
)
```

## Configuration

```yaml
# In config.yaml
jira:
  base_url: "https://your-domain.atlassian.net"
  email: "your-email@example.com"
  api_token: "your-api-token"
  default_project: "PROJ"
```

## Dependencies

- `requests` - HTTP library for REST API calls
- Standard library: `json`, `logging`

## Development Guide

### Supported Operations

| Operation | Method | Description |
|-----------|--------|-------------|
| Search | `search_issues(jql)` | Query issues using JQL |
| Get | `get_issue(key)` | Retrieve single issue |
| Create | `create_issue(**params)` | Create new issue |
| Update | `update_issue(key, **params)` | Modify existing issue |
| Comment | `add_comment(key, body)` | Add comment to issue |
| Transition | `transition_issue(key, transition)` | Move issue to new status |

### JQL Examples

```python
# Recent issues
jira.search_issues("project = PROJ ORDER BY created DESC")

# Issues assigned to me
jira.search_issues("assignee = currentUser() AND status != Done")

# Issues updated this week
jira.search_issues("project = PROJ AND updated >= -1w")
```

### Best Practices

- Use JQL for efficient queries
- Cache issue data when possible
- Handle rate limiting gracefully
- Use transitions instead of direct field updates
