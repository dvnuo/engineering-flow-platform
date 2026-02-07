# Canvas Tool - A2UI Canvas Control

Control A2UI Canvas rendering for UI presentation and snapshots.

## Usage

```bash
canvas action="present" url="https://example.com" width=800 height=600
canvas action="snapshot" outputFormat="png" maxWidth=1920
canvas action="eval" javaScript="document.title"
```

## Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|------------|
| action | string | Yes | Action: present, snapshot, eval, hide |
| url | string | No | URL to present (for present action) |
| width | int | No | Canvas width (default: 800) |
| height | int | No | Canvas height (default: 600) |
| outputFormat | string | No | png, jpeg (default: png) |
| maxWidth | int | No | Maximum width for snapshot |
| javaScript | string | No | JavaScript to evaluate (for eval action) |
| delayMs | int | No | Delay before capture |
| quality | int | No | JPEG quality (1-100) |

## Examples

Present a URL in canvas:
```
canvas action="present" url="https://example.com" width=1024 height=768
```

Capture snapshot:
```
canvas action="snapshot" outputFormat="png" maxWidth=1920
```

Evaluate JavaScript:
```
canvas action="eval" javaScript="document.title"
```
