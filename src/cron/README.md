# cron/ - Scheduled Tasks

## Structure

```
cron/
└── automation_watchers.py    # Main runtime-profile automation ingress watchers
```

## Components

### Automation Watchers (Primary runtime automation watcher)
Pulls runtime context + identity bindings from Portal internal APIs, builds in-memory automation rules, discovers external signals, and ingests normalized external events back to Portal.
Poll ingress metadata is minimal and focused on trigger/binding/source traceability.

## Usage

```python
import asyncio
from src.cron.automation_watchers import (
    start_automation_watchers,
    stop_automation_watchers,
)

async def main():
    watcher_task = asyncio.create_task(start_automation_watchers())
    ...
    await stop_automation_watchers()
    await watcher_task
```
