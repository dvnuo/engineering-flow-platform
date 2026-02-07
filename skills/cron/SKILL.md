---
name: cron
description: Schedule and manage recurring tasks with cron expressions or simple intervals
metadata:
  emoji: ⏰
  requires:
    bins: []
    anyBins: []
    env: []
    config: []
---
# Cron Skill - Schedule and Manage Recurring Tasks

Schedule and manage recurring tasks with cron expressions or simple intervals.

## Skill Signature

\`\`\`python
cron(
    action: str = "list",
    name: str = None,
    schedule: str = None,
    command: str = None,
    job_id: str = None,
    enabled: bool = True
) -> SkillResult
\`\`\`

## Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `action` | string | No | Operation: list, status, add, remove, run, update (default: "list") |
| `name` | string | No | Job name (required for add) |
| `schedule` | string | No | Cron expression or interval (e.g., "*/5 * * * *" or "every 5m") |
| `command` | string | No | Command or message to execute (required for add) |
| `job_id` | string | No | Job ID (required for remove, run, update) |
| `enabled` | string | No | Whether job is enabled (default: True) |

## Examples

### List Jobs

\`\`\`python
# List all scheduled jobs
cron(action="list")

# List with details
cron(action="status")
\`\`\`

### Add Jobs

\`\`\`python
# Add a job (every 5 minutes)
cron(
    action="add",
    name="check-alerts",
    schedule="*/5 * * * *",
    command="Check system alerts"
)

# Add a job with simple interval
cron(
    action="add",
    name="daily-summary",
    schedule="every 1d",
    command="Send daily summary"
)

# Add a job with hourly interval
cron(
    action="add",
    name="hourly-backup",
    schedule="every 1h",
    command="Run hourly backup"
)
\`\`\`

### Run Jobs

\`\`\`python
# Run a job immediately
cron(action="run", job_id="job-123")

# Run with status check
cron(action="status", job_id="job-123")
\`\`\`

### Update Jobs

\`\`\`python
# Disable a job
cron(action="update", job_id="job-123", enabled=False)

# Enable a disabled job
cron(action="update", job_id="job-123", enabled=True)
\`\`\`

### Remove Jobs

\`\`\`python
# Remove a job
cron(action="remove", job_id="job-123")
\`\`\`

## Schedule Formats

### Cron Expression

Standard cron format: `* * * * *`

| Field | Values |
|-------|--------|
| Minute | 0-59 |
| Hour | 0-23 |
| Day of Month | 1-31 |
| Month | 1-12 |
| Day of Week | 0-6 (Sunday-Saturday) |

**Examples**:
- `*/5 * * * *` - Every 5 minutes
- `0 * * * *` - Every hour
- `0 9 * * *` - Every day at 9am
- `0 9 * * 1` - Every Monday at 9am
- `0 0 1 * *` - First day of every month

### Simple Interval

\`\`\`yaml
every Xm  - Every X minutes
every Xh  - Every X hours
every Xd  - Every X days
\`\`\`

**Examples**:
- `every 5m` - Every 5 minutes
- `every 1h` - Every hour
- `every 30m` - Every 30 minutes
- `every 1d` - Every day
