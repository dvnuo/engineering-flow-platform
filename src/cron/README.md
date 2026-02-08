# cron/ - Scheduled Tasks

## Structure

```
cron/
└── mention_poller.py  # Monitor @mentions across platforms
```

## Components

### Mention Poller
Monitors GitHub, Jira, and Confluence for @mentions and processes commands.

## Usage

```python
from src.cron.mention_poller import start_polling, stop_polling

start_polling()
```
