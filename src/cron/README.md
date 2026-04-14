# cron/ - Scheduled Tasks

## Structure

```
cron/
├── subscription_watchers.py  # Main Portal-driven ingress watchers (poll/hybrid subscriptions)
└── mention_poller.py         # Legacy mention poller (tests compatibility / old direct-execute path)
```

## Components

### Subscription Watchers (Primary)
Pulls enabled subscriptions/bindings from Portal internal export, discovers external signals, and posts normalized ingress events back to Portal.

### Mention Poller (Legacy)
Kept for backward compatibility and tests. Not used as the main runtime entrypoint.

## Usage

```python
from src.cron.subscription_watchers import (
    start_subscription_watchers,
    stop_subscription_watchers,
)

start_subscription_watchers()
```
