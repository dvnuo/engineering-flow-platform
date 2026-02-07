# Sessions Tool - Session Management

Manage sub-agent sessions: spawn, list, history, and send messages.

## Usage

```bash
sessions_spawn task="分析这个 PR 并给出建议" thinking="high" cleanup="delete"
sessions_list
sessions_history sessionKey="session-abc123" limit=50
sessions_send sessionKey="session-abc123" message="继续执行下一步"
agents_list
```

## Sub-tools

### sessions_spawn

Spawn a new sub-agent session.

| Parameter | Type | Required | Description |
|-----------|------|----------|------------|
| task | string | Yes | Task description |
| agentId | string | No | Agent ID to use |
| model | string | No | Model to use |
| thinking | string | No | Thinking level |
| cleanup | string | No | delete or keep (default: delete) |
| label | string | No | Human-readable label |
| timeoutSeconds | int | No | Timeout (default: 300) |

### sessions_list

List active sessions.

| Parameter | Type | Description |
|-----------|------|------------|
| activeMinutes | int | Filter by activity |
| kinds | list | Filter by status |
| limit | int | Maximum results |
| messageLimit | int | Include N messages |

### sessions_history

Get message history for a session.

| Parameter | Type | Description |
|-----------|------|------------|
| sessionKey | string | Session identifier |
| limit | int | Maximum messages (default: 50) |
| includeTools | bool | Include tool results |

### sessions_send

Send a message to another session.

| Parameter | Type | Description |
|-----------|------|------------|
| sessionKey | string | Target session |
| message | string | Message to send |
| timeoutSeconds | int | Timeout |

### agents_list

List available agent IDs.

## Examples

Spawn a coding agent:
```
sessions_spawn task="修复这个 bug: ..." model="gpt-4o" thinking="high"
```

List active sessions:
```
sessions_list activeMinutes=30 limit=10
```

Get session history:
```
sessions_history sessionKey="session-abc" limit=100 includeTools=true
```

Send message to session:
```
sessions_send sessionKey="session-abc" message="继续执行"
```
