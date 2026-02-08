# memory/ - Memory Storage

## Structure

```
memory/
├── __init__.py       # MemoryStore interface
└── sqlite_store.py   # SQLite implementation
```

## Components

### MemoryStore
Interface for persistent memory storage with FTS5 full-text search.

### SqliteMemoryStore
SQLite-based implementation with:
- FTS5 full-text search (BM25 ranking)
- Session transcript indexing
- Hybrid search ready for vector integration

## Usage

```python
from src.memory import MemoryStore, get_memory_store

store = get_memory_store()
```
