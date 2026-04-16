# channels/ - Channel Adapters

## Structure

```
channels/
├── github.py       # GitHub webhook/comments integration
├── jira.py         # Jira integration
└── confluence.py   # Confluence integration
```

## Purpose

Channel adapters bridge the agent to various communication platforms.

## Usage

Each channel exports a channel object that can be used by the gateway:

```python
from src.channels.jira import jira_channel
from src.channels.github import github_channel
```
