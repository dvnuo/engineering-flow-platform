"""Memory module for OpenClaw-style long-term memory.

Features:
- SQLite storage for memory chunks
- FTS5 full-text search (BM25 ranking)
- Hybrid search ready for vector integration
- Session transcript indexing (optional)

## Vector Search Options (Future Enhancement)

Current: FTS5 full-text search only (BM25)

For semantic/vector search, choose one:

### Option A: sqlite-vec (Recommended by OpenClaw)
- Native SQLite extension for vector storage
- Fast in-process vector operations
- Requires: `pip install sqlite-vec`
- Config: `memory.store.vector.extensionPath`

### Option B: External Vector DB
- ChromaDB, Weaviate, Milvus, Qdrant
- Requires: `pip install chromadb` etc.
- Better for large-scale deployments

### Option C: Local Embeddings
- sentence-transformers (no API key)
- Model: `all-MiniLM-L6-v2` (~90MB, CPU-friendly)
- Requires: `pip install sentence-transformers`

## Configuration

```yaml
memory:
  enabled: true
  provider: "openai"  # or "local", "gemini"
  model: "text-embedding-3-small"
  hybrid:
    enabled: true
    vector_weight: 0.7
    text_weight: 0.3
  cache:
    enabled: true
    max_entries: 50000
```

## Memory Layers

1. Daily Notes: memory/YYYY-MM-DD.md
2. Long-term Memory: MEMORY.md
3. Session Transcripts: ~/.opsclaw/sessions/*.jsonl (future)
"""

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Default memory paths
DEFAULT_MEMORY_DIR = Path.home() / ".opsclaw/memory"
DEFAULT_WORKSPACE = Path.home() / ".opsclaw/workspace"


class MemoryConfig:
    """Memory configuration."""
    
    def __init__(self, config: Optional[Dict] = None):
        self.enabled = config.get("enabled", True) if config else True
        self.provider = config.get("provider", "openai") if config else "openai"
        self.model = config.get("model", "text-embedding-3-small") if config else "text-embedding-3-small"
        self.hybrid_enabled = config.get("hybrid", {}).get("enabled", True) if config else True
        self.vector_weight = config.get("hybrid", {}).get("vector_weight", 0.7) if config else 0.7
        self.text_weight = config.get("hybrid", {}).get("text_weight", 0.3) if config else 0.3
        self.cache_enabled = config.get("cache", {}).get("enabled", True) if config else True
        self.cache_max_entries = config.get("cache", {}).get("max_entries", 50000) if config else 50000
        self.memory_dir = Path(config.get("path", str(DEFAULT_MEMORY_DIR))) if config else DEFAULT_MEMORY_DIR
        self.workspace_dir = Path(config.get("workspace", str(DEFAULT_WORKSPACE))) if config else DEFAULT_WORKSPACE
        
        # Ensure memory directory exists
        self.memory_dir.mkdir(parents=True, exist_ok=True)
    
    def __repr__(self) -> str:
        return f"MemoryConfig(enabled={self.enabled}, provider={self.provider}, model={self.model})"


class MemoryStore:
    """Base memory store interface."""
    
    def __init__(self, config: Optional[MemoryConfig] = None):
        self.config = config or MemoryConfig()
        self._init_store()
    
    def _init_store(self):
        """Initialize the memory store."""
        raise NotImplementedError
    
    async def add_memory(self, content: str, metadata: Dict[str, Any]) -> str:
        """Add a memory chunk."""
        raise NotImplementedError
    
    async def search(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        """Search memories."""
        raise NotImplementedError
    
    async def get_memory(self, memory_id: str) -> Optional[Dict[str, Any]]:
        """Get a specific memory."""
        raise NotImplementedError
    
    async def delete_memory(self, memory_id: str) -> bool:
        """Delete a memory."""
        raise NotImplementedError
    
    async def list_memories(self, limit: int = 100) -> List[Dict[str, Any]]:
        """List all memories."""
        raise NotImplementedError


def get_memory_path(date_str: str = None) -> Path:
    """Get the path for daily memory file.
    
    Args:
        date_str: Date string in YYYY-MM-DD format. Uses today if not provided.
        
    Returns:
        Path to the memory file.
    """
    from datetime import datetime
    
    if date_str is None:
        date_str = datetime.now().strftime("%Y-%m-%d")
    
    memory_dir = DEFAULT_WORKSPACE / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)
    
    return memory_dir / f"{date_str}.md"


def get_long_term_memory_path() -> Path:
    """Get the path for long-term memory file.
    
    Returns:
        Path to MEMORY.md
    """
    return DEFAULT_WORKSPACE / "MEMORY.md"


# Global memory store instance
memory_store: Optional[MemoryStore] = None
_memory_auto_init = False


def init_memory_store(config: Optional[MemoryConfig] = None, auto_init: bool = False) -> MemoryStore:
    """Initialize the global memory store.
    
    Args:
        config: Optional memory configuration.
        auto_init: If True, automatically initialize on first use.
        
    Returns:
        Initialized memory store.
    """
    global memory_store, _memory_auto_init
    from memory.sqlite_store import SqliteMemoryStore
    
    memory_store = SqliteMemoryStore(config)
    _memory_auto_init = auto_init
    logger.info(f"Memory store initialized: {config}")
    return memory_store


def get_memory_store() -> Optional[MemoryStore]:
    """Get the global memory store.
    
    If not initialized and auto_init is True, initializes automatically.
    
    Returns:
        Current memory store or None if not initialized.
    """
    global memory_store, _memory_auto_init
    
    if memory_store is None and _memory_auto_init:
        try:
            memory_store = SqliteMemoryStore()
            logger.info("Memory store auto-initialized")
        except Exception as e:
            logger.warning(f"Failed to auto-initialize memory store: {e}")
    
    return memory_store


async def write_daily_memory(content: str, date_str: str = None) -> Path:
    """Write content to daily memory file.
    
    Args:
        content: Content to write.
        date_str: Optional date string (YYYY-MM-DD).
        
    Returns:
        Path to the written file.
    """
    memory_file = get_memory_path(date_str)
    
    with open(memory_file, "a", encoding="utf-8") as f:
        f.write(content + "\n")
    
    logger.info(f"Wrote daily memory: {memory_file}")
    return memory_file


async def write_long_term_memory(content: str) -> Path:
    """Write content to long-term memory file.
    
    Args:
        content: Content to write.
        
    Returns:
        Path to MEMORY.md
    """
    memory_file = get_long_term_memory_path()
    
    with open(memory_file, "a", encoding="utf-8") as f:
        f.write(content + "\n")
    
    logger.info(f"Wrote long-term memory: {memory_file}")
    return memory_file
