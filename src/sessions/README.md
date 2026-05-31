# sessions/ - Session Support

## Structure

```
sessions/
├── persistence.py  # Session persistence
├── pruning.py      # Session pruning
└── usage.py        # Usage tracking
```

## Components

### Persistence
Persistent storage for sessions.

### Pruning
Automatic session cleanup based on configuration.

## Usage

The active gateway/session API is the EFP runtime facade:

```python
from src.efp_runtime.session.gateway_facade import runtime_session_manager

session = runtime_session_manager.get_session(session_id)
```
