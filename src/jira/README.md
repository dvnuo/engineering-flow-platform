# jira/ - Jira Integration

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

## Usage

```python
from src.jira import JiraClient

jira = JiraClient()
issues = jira.search_issues("project = PROJ")
```
