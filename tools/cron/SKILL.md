# Cron Tool - Scheduled Task Management

Manage scheduled tasks, cron jobs, and wake events.

## Usage

```bash
cron action="status"
cron action="list"
cron action="add" job='{"name":"daily-summary"}' schedule='{"kind":"cron","expr":"0 9 * * *"}' payload='{"kind":"agentTurn","message":"生成摘要"}'
cron action="update" jobId="daily-summary" patch='{"enabled":false}'
cron action="remove" jobId="daily-summary"
cron action="run" jobId="daily-summary"
cron action="wake" text="提醒我喝水" mode="now"
```

## Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|------------|
| action | string | Yes | Action to perform |
| jobId | string | No | Job identifier |
| job | dict | No | Complete job definition |
| patch | dict | No | Partial update |
| schedule | dict | No | Schedule definition |
| payload | dict | No | Job payload |
| sessionTarget | string | No | main or isolated (default: isolated) |
| enabled | bool | No | Whether enabled (default: true) |
| text | string | No | Wake event text |
| mode | string | No | now or next-heartbeat |
| includeDisabled | bool | No | Include disabled jobs |

### Schedule Types

**Cron expression:**
```json
{"kind": "cron", "expr": "0 9 * * *", "tz": "Asia/Hong_Kong"}
```

**One-shot:**
```json
{"kind": "at", "atMs": 1709904000000}
```

**Interval:**
```json
{"kind": "every", "everyMs": 3600000}
```

### Payload Types

**Agent turn (isolated sessions only):**
```json
{"kind": "agentTurn", "message": "Task description", "model": "gpt-4o"}
```

**System event (main session):**
```json
{"kind": "systemEvent", "text": "Reminder text"}
```

## Examples

Add daily job at 9 AM:
```
cron action="add" \
  job='{"name":"daily-summary"}' \
  schedule='{"kind":"cron","expr":"0 9 * * *"}' \
  payload='{"kind":"agentTurn","message":"生成今日工作摘要"}'
```

Add one-shot reminder:
```
cron action="add" \
  schedule='{"kind":"at","atMs":1709904000000}' \
  payload='{"kind":"systemEvent","text":"会议提醒"}'
```

Run job immediately:
```
cron action="run" jobId="daily-summary"
```

Disable a job:
```
cron action="update" jobId="daily-summary" patch='{"enabled":false}'
```

Send wake event:
```
cron action="wake" text="提醒我喝水" mode="now"
```
