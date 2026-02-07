# Nodes Tool - Remote Node Control

Control paired remote nodes for camera, screen, location, and notifications.

## Usage

```bash
nodes action="status"
nodes action="camera_snap" facing="back" maxWidth=1920
nodes action="camera_clip" duration="10s" facing="front"
nodes action="screen_record" fps=30 outPath="/tmp/screen.mp4"
nodes action="location_get" desiredAccuracy="precise"
nodes action="notify" title="提醒" body="任务完成"
nodes action="run" command="['ls', '-la']"
```

## Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|------------|
| action | string | Yes | Action to perform |
| node | string | No | Target node ID/name |
| deviceId | string | No | Device identifier |

### Camera Actions

| Parameter | Type | Description |
|-----------|------|------------|
| facing | string | Camera facing: front, back, both |
| maxWidth | int | Maximum image width |
| quality | int | Image quality (1-100) |
| duration | string | Recording duration (e.g., "10s") |
| includeAudio | bool | Include audio in clip |

### Screen Recording

| Parameter | Type | Description |
|-----------|------|------------|
| fps | int | Frames per second |
| outPath | string | Output file path |
| durationMs | int | Duration in milliseconds |
| includeAudio | bool | Include audio |

### Location

| Parameter | Type | Description |
|-----------|------|------------|
| desiredAccuracy | string | coarse, balanced, precise |
| locationTimeoutMs | int | Timeout in milliseconds |
| maxAgeMs | int | Maximum age of cached location |

### Notification

| Parameter | Type | Description |
|-----------|------|------------|
| title | string | Notification title |
| body | string | Notification body |
| sound | string | Notification sound |
| priority | string | passive, active, timeSensitive |

### Command Execution

| Parameter | Type | Description |
|-----------|------|------------|
| command | list | Command and arguments |
| cwd | string | Working directory |
| commandTimeoutMs | int | Command timeout |

## Examples

Take a photo:
```
nodes action="camera_snap" facing="back" maxWidth=1920 quality=90
```

Record a clip:
```
nodes action="camera_clip" duration="10s" facing="front"
```

Record screen:
```
nodes action="screen_record" fps=30 outPath="/tmp/screen.mp4"
```

Get location:
```
nodes action="location_get" desiredAccuracy="precise"
```

Send notification:
```
nodes action="notify" title="提醒" body="任务完成"
```

Run command:
```
nodes action="run" command="['ps', 'aux']" cwd="/home"
```
