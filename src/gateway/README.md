# gateway/ - Web API Server

## Structure

```
gateway/
├── server.py                      # Main gateway server
├── runtime_chat.py                # EFP runtime chat adapter
├── runtime_api.py                 # Portal/runtime API routes
└── runtime_request_contracts.py   # Small request-id helpers
```

## Purpose

Provides the API-only native runtime HTTP surface. Portal owns the browser UI; the gateway does not serve embedded browser HTML, CSS, or JavaScript.

## Usage

```python
from src.gateway.server import gateway

gateway.run()
```
