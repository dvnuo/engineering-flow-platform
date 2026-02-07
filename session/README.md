# Session Directory

## Directory Structure

```
session/
├── __init__.py
├── base.py                  # Base session interface
├── memory_session.py         # In-memory session storage
├── redis_session.py          # Redis-based session storage
├── session_manager.py       # Main session management
├── context.py               # Context management
├── (session data files)
└── (session state files)
```

## How It Works

### 1. Session Architecture
```
┌─────────────────────────────────────────────────────────────┐
│                    Session Manager                          │
├─────────────────────────────────────────────────────────────┤
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐ │
│  │   Memory    │  │   Redis     │  │   Context           │ │
│  │   Session  │  │   Session   │  │   Manager           │ │
│  └─────────────┘  └─────────────┘  └─────────────────────┘ │
├─────────────────────────────────────────────────────────────┤
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐ │
│  │ Session     │  │ State       │  │  Middleware         │ │
│  │ Lifecycle   │  │ Persistence │  │  (Auth, Rate Limit)│ │
│  └─────────────┘  └─────────────┘  └─────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
```

### 2. Session Lifecycle
```
Create → Initialize → Active → Idle → Expire → Cleanup
           ↓           ↓        ↓       ↓
        Context     State    Timer   Garbage
      Setup       Update   Reset    Collection
```

### 3. Session Storage Implementation
```python
# session/memory_session.py

from typing import Dict, Any, Optional
from datetime import datetime, timedelta
import uuid
import json
from dataclasses import dataclass, asdict

@dataclass
class SessionData:
    """Session data container."""
    session_id: str
    user_id: str
    created_at: datetime
    last_active: datetime
    expires_at: datetime
    data: Dict[str, Any]
    state: str

class MemorySessionStore:
    """In-memory session storage."""
    
    def __init__(self, max_sessions: int = 1000, ttl: int = 3600):
        self.sessions: Dict[str, SessionData] = {}
        self.max_sessions = max_sessions
        self.default_ttl = ttl
    
    def create(self, user_id: str, initial_data: Dict = None) -> SessionData:
        """Create a new session."""
        now = datetime.utcnow()
        session = SessionData(
            session_id=str(uuid.uuid4()),
            user_id=user_id,
            created_at=now,
            last_active=now,
            expires_at=now + timedelta(seconds=self.default_ttl),
            data=initial_data or {},
            state="active"
        )
        self.sessions[session.session_id] = session
        return session
    
    def get(self, session_id: str) -> Optional[SessionData]:
        """Retrieve session by ID."""
        session = self.sessions.get(session_id)
        if session and not self._is_expired(session):
            session.last_active = datetime.utcnow()
            return session
        return None
    
    def update(self, session_id: str, data: Dict[str, Any]) -> bool:
        """Update session data."""
        ...
    
    def delete(self, session_id: str) -> bool:
        """Delete session."""
        ...
    
    def refresh(self, session_id: str) -> bool:
        """Refresh session TTL."""
        ...
    
    def list(self, user_id: str = None, state: str = None) -> List[SessionData]:
        """List sessions with optional filters."""
        ...
```

## What Problems It Solves

- **Multi-User Support**: Concurrent session management
- **State Persistence**: Maintain conversation state across requests
- **Context Isolation**: User data isolation for security
- **Session Timeout**: Automatic cleanup of stale sessions
- **Resource Management**: Limit concurrent sessions
- **Failover**: Session recovery after restarts

## Configuration Options

### Core Session Configuration (config.yaml)

```yaml
# config.yaml
session:
  # Storage backend
  backend: "memory"           # memory, redis, file
  
  # Memory backend settings
  memory:
    max_sessions: 1000         # Maximum concurrent sessions
    max_entries_per_session: 1000
    cleanup_interval: 300    # seconds
  
  # Redis backend settings
  redis:
    host: "localhost"
    port: 6379
    db: 0
    password: null
    key_prefix: "session:"
    pool_size: 10
    socket_timeout: 30
    connection_pool: true
  
  # Session lifecycle
  lifecycle:
    ttl: 3600                 # Default TTL (seconds)
    absolute_timeout: 86400   # Maximum session age (24 hours)
    idle_timeout: 1800       # Idle timeout (30 minutes)
    sliding_expiration: true  # Auto-extend TTL on activity
  
  # Session data
  data:
    max_size: 1048576         # Max session data size (1MB)
    compression: false
    encryption: false
    serialization: "json"     # json, msgpack, pickle
  
  # Session types
  types:
    conversation:
      ttl: 3600
      max_messages: 100
      auto_summarize: true
      summarize_after: 50
    
    user:
      ttl: 86400              # 24 hours
      persistent: true
      auto_backup: true
    
    temporary:
      ttl: 300               # 5 minutes
      persistent: false
  
  # Cleanup settings
  cleanup:
    enabled: true
    interval: 300           # seconds
    strategy: "lru"         # lru, ttl, hybrid
    reclaim_space: true
    vacuum_on_cleanup: true
    archive_before_delete: true
    archive_after: 604800   # 7 days
  
  # Security
  security:
    cookie_name: "session_id"
    cookie_secure: true
    cookie_httponly: true
    cookie_samesite: "strict"
    regenerate_on_login: true
    validate_on_every_request: true
```

### Per-Session-Type Configuration

```yaml
# Conversation session
session:
  types:
    conversation:
      backend: "memory"
      ttl: 3600
      max_messages: 100
      context_window: 20
      summarize_strategy: "truncate"  # truncate, extract, abstract
      
      # Message storage
      messages:
        storage: "memory"
        max_per_session: 100
        
      # Context management
      context:
        enabled: true
        max_tokens: 8000
        strategy: "sliding_window"
```

### Environment Variables

```bash
# Session settings
SESSION_BACKEND=memory
SESSION_TTL=3600

# Redis (if using Redis backend)
REDIS_HOST=localhost
REDIS_PORT=6379
REDIS_DB=0
REDIS_PASSWORD=

# Cookie settings
COOKIE_NAME=session_id
COOKIE_SECURE=true
COOKIE_HTTPONLY=true
```

## How to Run

### Initialize Session Manager
```python
from session import SessionManager

# Initialize with config
manager = SessionManager(config={
    "backend": "memory",
    "ttl": 3600
})

# Auto-initialize
manager = SessionManager(auto_init=True)
```

### Basic Operations
```python
from session import SessionManager

manager = SessionManager()

# Create session
session = manager.create(user_id="user-123", initial_data={"pref": "dark"})
session_id = session.session_id

# Get session
session = manager.get(session_id)
if session:
    print(f"Session data: {session.data}")

# Update session
manager.update(session_id, {"new_data": "value"})

# Refresh session TTL
manager.refresh(session_id)

# Delete session
manager.delete(session_id)
```

### Context Management
```python
from session.context import ContextManager

context = ContextManager(session_id="session-123")

# Add to context
context.add("user_message", "Hello!")
context.add("bot_response", "Hi there!")

# Get context
messages = context.get_messages(limit=10)

# Clear context
context.clear()
```

## Development Principles

### 1. Session Pattern
```python
# Use session context manager
with manager.session(user_id="user-123") as session:
    session.data["counter"] = session.data.get("counter", 0) + 1
    session.save()
```

### 2. Error Handling
```python
class SessionError(Exception):
    """Base session error."""
    pass

class SessionNotFoundError(SessionError):
    """Session doesn't exist."""
    pass

class SessionExpiredError(SessionError):
    """Session has expired."""
    pass

class SessionFullError(SessionError):
    """Session storage is full."""
    pass

# Handling
try:
    session = manager.get(session_id)
except SessionExpiredError:
    create_new_session()
```

### 3. Performance Optimization
```python
# Connection pooling (Redis)
from session.redis_session import RedisSessionStore

store = RedisSessionStore(
    host="localhost",
    port=6379,
    pool_size=20
)

# Batch operations
with manager.batch() as batch:
    for session_id in session_ids:
        session = manager.get(session_id)
        batch.update(session_id, {"accessed": True})
```

### 4. Testing Standards
```python
class TestSessionManager:
    def test_create_session(self):
        """Test session creation."""
        manager = SessionManager()
        session = manager.create("user-123")
        assert session.user_id == "user-123"
    
    def test_session_expiry(self):
        """Test session expiration."""
        manager = SessionManager(ttl=1)  # 1 second TTL
        session = manager.create("user-123")
        import time
        time.sleep(2)
        assert manager.get(session.session_id) is None
```

## API Reference

### SessionManager (session/__init__.py)

```python
class SessionManager:
    """Main session management interface."""
    
    def __init__(self, config: Dict[str, Any] = None, auto_init: bool = False):
        """Initialize session manager."""
        ...
    
    def create(self, user_id: str, initial_data: Dict = None,
               session_type: str = "default") -> Session:
        """Create a new session."""
        ...
    
    def get(self, session_id: str) -> Optional[Session]:
        """Retrieve session by ID."""
        ...
    
    def get_many(self, session_ids: List[str]) -> Dict[str, Session]:
        """Retrieve multiple sessions."""
        ...
    
    def update(self, session_id: str, data: Dict[str, Any]) -> bool:
        """Update session data."""
        ...
    
    def delete(self, session_id: str) -> bool:
        """Delete session."""
        ...
    
    def refresh(self, session_id: str) -> bool:
        """Refresh session TTL."""
        ...
    
    def exists(self, session_id: str) -> bool:
        """Check if session exists and is valid."""
        ...
    
    def list(self, user_id: str = None, state: str = None,
             limit: int = 100) -> List[Session]:
        """List sessions with optional filters."""
        ...
    
    def count(self, user_id: str = None) -> int:
        """Count sessions."""
        ...
    
    def clear(self, user_id: str = None, state: str = None) -> int:
        """Clear sessions."""
        ...
    
    def cleanup(self) -> int:
        """Remove expired sessions."""
        ...
    
    def backup(self, path: str) -> bool:
        """Backup sessions."""
        ...
    
    def restore(self, path: str) -> bool:
        """Restore from backup."""
        ...
```

### Session Types

| Type | TTL | Persistence | Use Case |
|------|-----|-------------|----------|
| default | 1 hour | Memory | General sessions |
| conversation | 1 hour | Memory | Chat sessions |
| user | 24 hours | Redis/File | User preferences |
| temporary | 5 min | Memory | Short-lived data |
| api | 1 hour | Memory | API tokens |

## Troubleshooting

### Session Not Found
```python
# Debug session
from session import SessionManager

manager = SessionManager()
session = manager.get("session-id")

if session is None:
    # Check if expired
    print(f"Total sessions: {manager.count()}")
    
    # List all sessions
    sessions = manager.list()
    for s in sessions:
        print(f"{s.session_id}: {s.user_id} - {s.state}")
```

### Memory Issues
```bash
# Check session store size
python -c "
from session import SessionManager
manager = SessionManager()
print(f'Total sessions: {manager.count()}')
print(f'Memory usage: {manager.get_memory_usage()}')
"

# Force cleanup
manager.cleanup()
```

### Redis Connection Issues
```bash
# Test Redis connection
redis-cli ping

# Check session keys
redis-cli KEYS "session:*"

# Clear all sessions
redis-cli FLUSHDB
```
