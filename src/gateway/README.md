# gateway/ - Web API Server

## Structure

```
gateway/
├── server.py     # Main gateway server
├── webchat.py    # Web chat interface
├── static/       # Static assets
└── templates/    # HTML templates
```

## Purpose

Provides REST API and web interface for the platform.

## Usage

```python
from src.gateway.server import gateway

gateway.run()
```
