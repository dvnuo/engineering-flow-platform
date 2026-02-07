# Browser Tool - Browser Control

Control web browser for navigation, snapshots, screenshots, and automation.

## Usage

```bash
browser action="status"
browser action="profiles"
browser action="open" targetUrl="https://example.com"
browser action="snapshot" fullPage=true
browser action="screenshot" fullPage=true type="png"
browser action="navigate" targetUrl="https://example.com"
browser action="act" request='{"kind":"click","selector":"#submit"}'
browser action="close"
```

## Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|------------|
| action | string | Yes | Action to perform |
| profile | string | No | openclaw or chrome (default: openclaw) |
| target | string | No | sandbox, host, or node (default: host) |
| targetUrl | string | No | URL to navigate to |
| selector | string | No | Element selector |
| fullPage | bool | No | Capture full page |
| type | string | No | png or jpeg (default: png) |
| request | dict | No | Interaction request |
| ref | string | No | Element reference |
| timeoutMs | int | No | Timeout in milliseconds |
| snapshotFormat | string | No | role or aria |

### Interaction Request Types

**Click:**
```json
{"kind": "click", "selector": "#button", "ref": "submit-btn"}
```

**Type:**
```json
{"kind": "type", "selector": "#input", "text": "Hello"}
```

**Navigate:**
```json
{"kind": "navigate", "targetUrl": "https://example.com"}
```

**Wait:**
```json
{"kind": "wait", "timeMs": 5000}
```

**Evaluate:**
```json
{"kind": "evaluate", "javaScript": "document.title"}
```

## Examples

Open URL:
```
browser action="open" targetUrl="https://example.com"
```

Take snapshot:
```
browser action="snapshot" fullPage=true snapshotFormat="aria"
```

Take screenshot:
```
browser action="screenshot" type="png" fullPage=false
```

Click element:
```
browser action="act" request='{"kind":"click","selector":"#submit"}'
```

Type text:
```
browser action="act" request='{"kind":"type","selector":"#email","text":"test@example.com"}'
```

Close browser:
```
browser action="close"
```
