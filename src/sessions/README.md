# sessions/ - Session Management

## Structure

```
sessions/
├── manager.py      # Session manager
├── persistence.py  # Session persistence
├── pruning.py      # Session pruning
└── usage.py        # Usage tracking
```

## Components

### Session Manager
Manages active sessions with context and history.

### Persistence
Persistent storage for sessions.

### Pruning
Automatic session cleanup based on configuration.

## Usage

```python
from src.sessions.manager import session_manager

session = session_manager.get_session(session_id)
```
