# Gateway Tool - Gateway Management

Restart, apply config, or update the gateway.

## Usage

```bash
gateway action="restart" delayMs=5000
gateway action="config.get"
gateway action="config.schema"
gateway action="config.apply" baseHash="abc123"
gateway action="config.patch" patch='{"key":"value"}'
gateway action="update.run" note="Security update" reason="CVE fix"
```

## Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|------------|
| action | string | Yes | Action to perform |
| baseHash | string | No | Config hash for validation |
| delayMs | int | No | Delay before restart |
| restartDelayMs | int | No | Restart delay |
| sessionKey | string | No | Session key for targeted restart |
| note | string | No | Update note |
| reason | string | No | Update reason |
| patch | dict | No | Config patch |

## Actions

| Action | Description |
|--------|------------|
| restart | Restart gateway |
| config.get | Get current config |
| config.schema | Get config schema |
| config.apply | Apply full config |
| config.patch | Patch config |
| update.run | Run update |

## Examples

Restart gateway:
```
gateway action="restart" delayMs=5000
```

Get config:
```
gateway action="config.get"
```

Get schema:
```
gateway action="config.schema"
```

Apply config:
```
gateway action="config.apply" baseHash="abc123"
```

Patch config:
```
gateway action="config.patch" patch='{"channel.default":"discord"}'
```

Run update:
```
gateway action="update.run" note="v1.2.0" reason="New features"
```
