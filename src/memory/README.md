# Memory Directory

## Directory Structure

```
memory/
├── __init__.py
├── base.py                  # Base memory interface
├── sqlite_store.py          # SQLite implementation
├── file_store.py            # File-based implementation
├── semantic_search.py       # Semantic search functionality
├── memory_manager.py        # Memory management
├── (other memory implementations)
└── (memory data files)
```

## How It Works

### 1. Memory Architecture
```
┌─────────────────────────────────────────────────────────────┐
│                      Memory Manager                        │
├─────────────────────────────────────────────────────────────┤
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐ │
│  │  SQLite     │  │  File       │  │  Semantic Search    │ │
│  │  Store      │  │  Store      │  │  (FTS5)            │ │
│  └─────────────┘  └─────────────┘  └─────────────────────┘ │
├─────────────────────────────────────────────────────────────┤
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐ │
│  │ Short-term  │  │ Long-term   │  │  Working Memory     │ │
│  │ (Session)   │  │ (Persistent)│  │  (Context)          │ │
│  └─────────────┘  └─────────────┘  └─────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
```

### 2. Memory Types

#### Short-term Memory
```python
# Current session context
short_term_memory = {
    "conversation_history": [...],
    "current_task": "...",
    "user_preferences": {...},
    "temporary_data": {...}
}

# Automatically cleared on session end
```

#### Long-term Memory
```python
# Persistent storage
long_term_memory = {
    "learned_facts": [...],
    "user_profiles": [...],
    "project_knowledge": [...],
    "decisions_log": [...]
}

# Persisted to SQLite/File
```

#### Working Memory
```python
# Current context during processing
working_memory = {
    "current_context": {...},
    "active_entities": {...},
    "pending_actions": [...],
    "reasoning_trace": [...]
}

# Temporary, used during processing
```

### 3. Memory Storage Implementation
```python
# memory/sqlite_store.py

import sqlite3
from typing import List, Dict, Optional
from datetime import datetime

class SQLiteMemoryStore:
    """SQLite-based persistent memory storage."""
    
    def __init__(self, db_path: str = "memory/memory.db"):
        self.db_path = db_path
        self._init_db()
    
    def _init_db(self):
        """Initialize database schema."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                key TEXT UNIQUE NOT NULL,
                value TEXT NOT NULL,
                memory_type TEXT,
                tags TEXT,
                created_at TEXT,
                updated_at TEXT,
                access_count INTEGER DEFAULT 0,
                last_accessed TEXT
            )
        ''')
        
        # FTS5 full-text search
        cursor.execute('''
            CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts 
            USING fts5(key, value, content='memories', content_rowid='id')
        ''')
        
        conn.commit()
        conn.close()
    
    def save(self, key: str, value: str, memory_type: str = "general", 
             tags: List[str] = None) -> bool:
        """Save a memory."""
        ...
    
    def get(self, key: str) -> Optional[str]:
        """Retrieve a memory."""
        ...
    
    def search(self, query: str, limit: int = 10) -> List[Dict]:
        """Full-text search memories."""
        ...
    
    def delete(self, key: str) -> bool:
        """Delete a memory."""
        ...
    
    def list(self, memory_type: str = None, limit: int = 100) -> List[Dict]:
        """List memories."""
        ...
```

## What Problems It Solves

- **Knowledge Persistence**: Retain learned information across sessions
- **Semantic Search**: Find memories by meaning, not just keywords
- **Context Management**: Maintain conversation context
- **Learning Capability**: Improve over time based on interactions
- **Efficient Retrieval**: Fast access to relevant memories
- **Data Organization**: Tags and categories for memory organization

## Configuration Options

### Core Memory Configuration (config.yaml)

```yaml
# config.yaml
memory:
  # Storage backend
  backend: "sqlite"          # sqlite, file, hybrid
  
  # SQLite configuration
  sqlite:
    path: "memory/memory.db"
    pool_size: 5
    timeout: 30             # seconds
    wal_mode: true
    journal_mode: "WAL"
  
  # File-based storage
  file:
    path: "memory/data"
    format: "json"          # json, yaml, msgpack
    compression: false
    encryption: false
  
  # Memory types
  types:
    short_term:
      ttl: 3600            # Time to live (seconds)
      max_entries: 1000
      auto_cleanup: true
    
    long_term:
      ttl: null             # No expiration
      max_entries: 10000
      auto_cleanup: false
      archive_old: true
      archive_after: 2592000  # 30 days
  
  # Semantic search
  semantic_search:
    enabled: true
    engine: "sqlite_fts"    # sqlite_fts, external
    min_score: 0.5
    max_results: 10
    
    # Vector storage (for embedding-based search)
    vector:
      enabled: false
      model: "all-MiniLM-L6-v2"
      dimension: 384
      index_type: "hnsw"
      storage: "memory"     # memory, file
    
  # Memory organization
  organization:
    enable_tags: true
    auto_tagging: true
    max_tags_per_memory: 10
    categories:
      - "user_preference"
      - "project"
      - "decision"
      - "fact"
      - "context"
      - "learning"
  
  # Cleanup settings
  cleanup:
    enabled: true
    interval: 3600         # seconds
    strategy: "lru"        # lru, ttl, hybrid
    reclaim_space: true
    vacuum_on_cleanup: true
```

### Per-Memory-Type Configuration

```yaml
# Conversation memory
memory:
  types:
    conversation:
      backend: "sqlite"
      table: "conversations"
      max_context: 50       # Maximum messages in context
      summarize_after: 20  # Summarize after N messages
      embedding_model: "text-embedding-3-small"
    
    # User preferences
    preferences:
      backend: "file"
      path: "memory/preferences"
      format: "json"
      encryption: true
    
    # Project knowledge
    project:
      backend: "sqlite"
      table: "project_knowledge"
      auto_backup: true
      backup_interval: 86400
```

### Environment Variables

```bash
# Storage
MEMORY_BACKEND=sqlite
MEMORY_PATH=memory/memory.db

# Semantic search
SEMANTIC_SEARCH_ENABLED=true
EMBEDDING_MODEL=all-MiniLM-L6-v2

# Vector database (optional)
VECTOR_DB_URL=http://localhost:6333
VECTOR_DB_API_KEY=
```

## How to Run

### Initialize Memory Store
```python
from memory import MemoryStore

# Initialize with config
store = MemoryStore(config={
    "backend": "sqlite",
    "path": "memory.db"
})

# Auto-initialize
store = MemoryStore(auto_init=True)
```

### Basic Operations
```python
from memory import MemoryStore

store = MemoryStore()

# Save memory
store.save("user:preference:theme", "dark", 
           tags=["user", "preference"])

# Retrieve memory
theme = store.get("user:preference:theme")

# Search memories
results = store.search("user preferences")

# Delete memory
store.delete("user:preference:theme")

# List all
all_memories = store.list()
```

### Semantic Search
```python
from memory.semantic_search import SemanticSearch

search = SemanticSearch()

# Index memories
search.index("user likes dark mode")
search.index("user prefers python over javascript")

# Search by meaning
results = search.search("what does user prefer for programming")
# Returns: "user prefers python over javascript"
```

## Development Principles

### 1. Memory Pattern
```python
# Use composite keys
KEY_PREFIX = {
    "user": "user:{user_id}:{key}",
    "project": "project:{project_id}:{key}",
    "session": "session:{session_id}:{key}",
    "global": "global:{key}",
}

def save_user_preference(user_id: str, key: str, value: str):
    """Save user preference with proper key structure."""
    composite_key = f"user:{user_id}:preferences:{key}"
    store.save(
        key=composite_key,
        value=value,
        memory_type="preference",
        tags=["user", "preference", key]
    )
```

### 2. Error Handling
```python
class MemoryError(Exception):
    """Base memory error."""
    pass

class MemoryNotFoundError(MemoryError):
    """Requested memory doesn't exist."""
    pass

class MemoryFullError(MemoryError):
    """Memory storage is full."""
    pass

class SearchError(MemoryError):
    """Search operation failed."""
    pass

# Handling
try:
    value = store.get(key)
except MemoryNotFoundError:
    value = get_default_value()
```

### 3. Performance Optimization
```python
# Use connection pooling
from memory.sqlite_store import SQLiteMemoryStore

store = SQLiteMemoryStore(
    path="memory.db",
    pool_size=10,
    timeout=30
)

# Batch operations
with store.transaction():
    for item in items:
        store.save(item.key, item.value)

# Index frequently accessed keys
store.create_index(["user:*:preference:*", "session:*:context"])
```

### 4. Testing Standards
```python
class TestMemoryStore:
    def test_save_and_retrieve(self):
        """Test basic save/retrieve."""
        store = MemoryStore()
        store.save("test_key", "test_value")
        assert store.get("test_key") == "test_value"
    
    def test_search(self):
        """Test full-text search."""
        store = MemoryStore()
        store.save("fact", "Python is awesome")
        results = store.search("Python")
        assert len(results) > 0
    
    def test_memory_types(self):
        """Test different memory types."""
        ...
```

## API Reference

### MemoryStore (memory/__init__.py)

```python
class MemoryStore:
    """Main memory storage interface."""
    
    def __init__(self, config: Dict[str, Any] = None, auto_init: bool = False):
        """Initialize memory store."""
        ...
    
    def save(self, key: str, value: str, memory_type: str = "general",
             tags: List[str] = None) -> bool:
        """Save a memory."""
        ...
    
    def get(self, key: str) -> Optional[str]:
        """Retrieve a memory by key."""
        ...
    
    def get_many(self, keys: List[str]) -> Dict[str, str]:
        """Retrieve multiple memories."""
        ...
    
    def delete(self, key: str) -> bool:
        """Delete a memory."""
        ...
    
    def search(self, query: str, memory_type: str = None,
               limit: int = 10) -> List[SearchResult]:
        """Full-text search memories."""
        ...
    
    def semantic_search(self, query: str, limit: int = 10) -> List[SearchResult]:
        """Semantic search using embeddings."""
        ...
    
    def list(self, memory_type: str = None, limit: int = 100,
             offset: int = 0) -> List[Memory]:
        """List memories with optional filtering."""
        ...
    
    def count(self, memory_type: str = None) -> int:
        """Count memories."""
        ...
    
    def clear(self, memory_type: str = None) -> bool:
        """Clear memories of a type or all."""
        ...
    
    def backup(self, path: str) -> bool:
        """Backup memory store."""
        ...
    
    def restore(self, path: str) -> bool:
        """Restore from backup."""
        ...
```

### Memory Types

| Type | Purpose | Storage | TTL |
|------|---------|---------|-----|
| short_term | Session context | SQLite | 1 hour |
| long_term | Persistent knowledge | SQLite/File | None |
| working | Processing context | Memory | Session |
| conversation | Chat history | SQLite | Configurable |
| preference | User settings | File | None |
| project | Project knowledge | SQLite | None |

## Troubleshooting

### Database Issues
```bash
# Check database integrity
sqlite3 memory/memory.db "PRAGMA integrity_check"

# Repair corrupted database
sqlite3 memory/memory.db ".recover" > recovered.sql

# Reset database
rm memory/memory.db
python -c "from memory import MemoryStore; MemoryStore(auto_init=True)"
```

### Performance Issues
```python
# Check memory size
from memory import MemoryStore
store = MemoryStore()
stats = store.get_stats()
print(f"Total memories: {stats['count']}")
print(f"Database size: {stats['size']}")

# Optimize database
store.optimize()

# Rebuild indexes
store.rebuild_indexes()
```

### Search Not Working
```python
# Check FTS status
from memory.sqlite_store import SQLiteMemoryStore
store = SQLiteMemoryStore()
print(store.get_fts_status())

# Rebuild FTS index
store.rebuild_fts()
```
