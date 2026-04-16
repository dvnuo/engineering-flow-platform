# cron/ - Scheduled Tasks

## Structure

```
cron/
├── subscription_watchers.py  # Main runtime-profile automation ingress watchers
└── mention_poller.py         # Legacy mention poller (tests compatibility / old direct-execute path)
```

## Components

### Subscription Watchers (Primary runtime automation watcher)
Pulls runtime context + identity bindings from Portal internal APIs, builds in-memory automation rules, discovers external signals, and ingests normalized external events back to Portal.
Poll ingress metadata is minimal and focused on trigger/binding/source traceability.

### Mention Poller (Legacy)
Kept for backward compatibility and tests. Not used as the main runtime entrypoint.

## Usage

```python
import asyncio
from src.cron.subscription_watchers import (
    start_subscription_watchers,
    stop_subscription_watchers,
)

async def main():
    watcher_task = asyncio.create_task(start_subscription_watchers())
    ...
    await stop_subscription_watchers()
    await watcher_task
```
