# memory/ - Memory Storage

## Structure

```
memory/
├── __init__.py       # MemoryStore interface and exports
├── lightweight.py    # Lightweight TF-IDF based memory
└── vector.py        # Vector-based memory (optional)
```

## Components

### LightweightMemory
TF-IDF based in-memory search for session context.

Used by `agents/memory.py` for semantic search over memory files.

### MemoryStore
Legacy interface - currently disabled (using LightweightMemory instead).

## Usage

```python
from src.agents.memory import MemorySystem

memory = MemorySystem(workspace_path)
```
